"""ReAct 风格 Agent，负责工具调用、权限中断和上下文压缩。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.config import settings
from app.tools.base import ToolDef
from app.tools.permissions import PermissionBroker, PermissionLevel

logger = logging.getLogger(__name__)

FAILURE_PREFIXES = (
    "error:",
    "path rejected:",
    "read failed:",
    "write failed:",
    "edit failed:",
    "move failed:",
    "copy failed:",
    "delete failed:",
    "command execution failed:",
    "python execution failed:",
    "command blocked by sandbox policy:",
    "sandbox sync failed",
    "grep failed:",
    "invalid regex:",
    "invalid glob pattern:",
)

WARNING_PREFIXES = (
    "warning:",
)

WORKFLOW_GUIDANCE_PROMPT = """## 工具使用约定
- 查看项目内容时，优先使用 `read_file`、`grep`、`glob`、`list_files`。
- 精确修改单个文本文件时，优先使用 `write_file` 或 `edit_file`。
- 精确移动、复制或删除单个路径时，优先使用 `move_file`、`copy_file` 或 `delete_file`。
- 如果用户指的是一组已经明确知道的文件或目录，优先使用 `move_paths`、`copy_paths` 或 `delete_paths`，传入精确路径列表，不要用通配符重新猜测范围。
- 如果用户表达的是模糊集合（如"所有.jpg文件"），先用 `glob` 或 `list_files` 发现具体文件列表，然后将精确路径传入 `move_paths`、`copy_paths` 或 `delete_paths`。
- 如果要在工作区外批量操作文件，不要直接执行带通配符的 shell 命令。
- 如果任务本质上是 Python 脚本更擅长的事情，例如循环、批量生成文件、文本/JSON/CSV 处理，请优先使用 `execute_python`，不要把 Python 代码塞进 `execute_shell`。
- 只有在确实需要命令执行、测试、构建或运行非 Python 命令行程序时，才使用 `execute_shell`。
"""

REACT_SYSTEM_PROMPT = """你是当前项目的本地智能开发助手，需要以稳妥、可审计的方式完成任务。

## 工作原则
- 先理解需求，再选择最合适的工具。
- 能直接回答的问题直接回答，不要为了展示能力而滥用工具。
- 对代码和文件的修改要尽量精确，避免一次性做无关改动。
- 如果一次操作失败，要根据失败原因调整方案，而不是机械重复。

## 工具使用规则
- `read_file`：读取单个文件内容。
- `write_file`：创建文件或整体覆盖文件内容。
- `edit_file`：只替换文件中的唯一片段；如果匹配不唯一，应先缩小范围。
- `grep`：按正则检索内容。
- `glob`：按模式查找文件。
- `list_files`：查看目录下的文件和子目录。
- `move_file`：移动或重命名单个路径。
- `copy_file`：复制单个文件或目录。
- `delete_file`：删除单个文件或目录。
- `move_paths`：对已知的精确路径集合执行批量移动。
- `copy_paths`：对已知的精确路径集合执行批量复制。
- `delete_paths`：对已知的精确路径集合执行批量删除。
- `execute_python`：直接执行 Python 代码，适合循环、批处理、文本处理和小型自动化。
- `execute_shell`：运行命令、测试或构建流程。
- `search_web`：需要联网查找信息时使用。
- `read_skill`：查看技能说明或工作流文档时使用。

## 编辑要求
- 修改代码前，先尽量读取足够的上下文。
- 使用 `edit_file` 时，`old_string` 必须是精确且唯一的原文片段。
- 如果需要替换多处内容，应分多次调用，避免用模糊文本一次性覆盖。

## 批量操作流程
- 凡是需要按模式批量操作文件（删除、移动、复制等），必须先用 `glob` 或 `list_files` 发现具体文件列表。
- 发现文件列表后，**必须**逐项检查列表是否与用户的要求完全一致：
  - 如果匹配到的文件比用户指定的多或少、名称不符合预期，说明 pattern 范围不对，**必须**调整 pattern 重新搜索。
  - 只有当文件列表与用户意图精确匹配时，才能将确定的路径列表传入 `move_paths`、`copy_paths` 或 `delete_paths` 执行。
  - 绝不能在文件列表明显包含非目标文件的情况下仍继续执行。

## 输出要求
- 正常回答时保持简洁清楚。
- 如果工具返回错误，先理解错误原因，再决定是否换工具或调整参数。
- 如果权限被拒绝，要基于当前限制重新规划，而不是假装已经成功。

## 输出限制
当操作因权限、路径限制或安全策略被阻止时，只需简明告知用户结果，例如：
- "无法访问该路径，请将文件移动到项目目录后再试。"
- "该操作被安全策略拒绝，请尝试逐个文件操作。"
- "目标路径不在当前可访问范围内。"

**不得**解释"因为沙箱策略"、"通过 xxx 工具绕过"、"当前工作区是 xxx"等内部信息。不得向用户暴露系统的内部工作机制。
"""


def _tools_to_openai_with_purpose(tools: list[BaseTool]) -> list[dict]:
    """为每个工具额外注入 purpose 参数，要求模型说明调用原因。"""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    definitions: list[dict] = []
    for tool in tools:
        tool_def = convert_to_openai_tool(tool)
        fn_def = tool_def.get("function", tool_def)
        params = fn_def.get("parameters", {})
        props = params.get("properties", {})
        props["purpose"] = {
            "type": "string",
            "description": "请简要说明这次调用这个工具的原因、目标，或你希望通过它确认什么信息。",
        }
        required = list(params.get("required", []))
        if "purpose" not in required:
            required.append("purpose")
        params["properties"] = props
        params["required"] = required
        fn_def["parameters"] = params
        definitions.append(tool_def)
    return definitions


class ReactAgent:
    """带上下文压缩、权限中断和流式输出的 ReAct Agent。"""

    def __init__(
        self,
        tools: list[BaseTool],
        llm: ChatOpenAI | None = None,
        system_prompt: str = "",
        max_rounds: int | None = None,
        context_char_limit: int | None = None,
        tool_defs: dict[str, ToolDef] | None = None,
    ):
        self.tools = {tool.name: tool for tool in tools}
        self.tool_list = tools
        self.llm = llm or self._default_llm()
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds or settings.max_agent_rounds
        self.context_char_limit = context_char_limit or settings.context_char_limit
        self._tool_defs = tool_defs or {}

    @staticmethod
    def _default_llm() -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    def _build_messages(self, question: str, history: list | None = None) -> list:
        messages = [
            SystemMessage(content=REACT_SYSTEM_PROMPT),
            SystemMessage(content=WORKFLOW_GUIDANCE_PROMPT),
            SystemMessage(content=self.system_prompt),
        ]
        if history:
            messages.extend(history)
        messages.append(HumanMessage(content=f"<question>\n{question}\n</question>"))
        return messages

    def _get_permission_level(self, tool_name: str) -> PermissionLevel:
        tool_def = self._tool_defs.get(tool_name)
        if tool_def is None:
            return PermissionLevel.SHELL
        try:
            return PermissionLevel(tool_def.permission_level)
        except ValueError:
            return PermissionLevel.SHELL

    @staticmethod
    def _tool_result_success(result: object) -> bool:
        text = str(result).strip().lower()
        return not any(text.startswith(prefix) for prefix in FAILURE_PREFIXES)

    @staticmethod
    def _tool_result_warning(tool_name: str, result: object) -> bool:
        text = str(result).strip().lower()
        return any(text.startswith(prefix) for prefix in WARNING_PREFIXES)

    @staticmethod
    def _parse_tool_args(raw_args: object) -> dict:
        if isinstance(raw_args, dict):
            return dict(raw_args)
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    @staticmethod
    def _max_rounds_message() -> HumanMessage:
        return HumanMessage(
            content="你已经达到当前回合上限。请不要继续调用工具，直接基于已有信息给出结论，并明确说明剩余的不确定性。"
        )

    async def call(self, question: str, history: list | None = None) -> str:
        """非流式调用。危险工具在这里默认不允许交互审批。"""
        llm_with_tools = self.llm.bind(tools=_tools_to_openai_with_purpose(self.tool_list))
        messages = self._build_messages(question, history)
        round_count = 0

        while True:
            round_count += 1
            if self.max_rounds > 0 and round_count > self.max_rounds:
                messages.append(self._max_rounds_message())
                response = await self.llm.ainvoke(messages)
                return str(response.content)

            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)

            if not response.tool_calls:
                return str(response.content)

            for tool_call in response.tool_calls:
                tool = self.tools.get(tool_call["name"])
                args = self._parse_tool_args(tool_call.get("args", {}))
                args.pop("purpose", None)

                if tool is None:
                    result = f"错误：未知工具 `{tool_call['name']}`。"
                else:
                    perm_level = self._get_permission_level(tool_call["name"])
                    if perm_level != PermissionLevel.READ:
                        result = (
                            f"工具 `{tool_call['name']}` 需要 `{perm_level.value}` 级权限。"
                            "当前为非交互调用，不能自动批准，请改用更安全的工具或直接给出结论。"
                        )
                    else:
                        try:
                            result = await tool.ainvoke(args)
                        except Exception as exc:
                            result = f"工具执行异常：{exc}"
                messages.append(ToolMessage(content=str(result), tool_call_id=tool_call["id"]))

            self._compress_if_needed(messages)

    async def stream(
        self,
        question: str,
        history: list | None = None,
        permission_broker: PermissionBroker | None = None,
    ) -> AsyncIterator[str | dict]:
        """流式调用，支持工具事件和权限中断。"""
        llm_with_tools = self.llm.bind(tools=_tools_to_openai_with_purpose(self.tool_list))
        messages = self._build_messages(question, history)
        round_count = 0

        while True:
            round_count += 1
            if self.max_rounds > 0 and round_count > self.max_rounds:
                messages.append(self._max_rounds_message())
                async for chunk in self.llm.astream(messages):
                    if chunk.content:
                        yield str(chunk.content)
                return

            full_content = ""
            tool_calls_acc: list[dict] = []
            async for chunk in llm_with_tools.astream(messages):
                if chunk.content:
                    full_content += str(chunk.content)
                    yield str(chunk.content)
                if chunk.tool_calls:
                    for tool_call in chunk.tool_calls:
                        existing = next((item for item in tool_calls_acc if item.get("id") == tool_call.get("id")), None)
                        if existing:
                            existing["args"] = (existing.get("args", "") + str(tool_call.get("args", "")))
                        else:
                            tool_calls_acc.append(dict(tool_call))

            if not tool_calls_acc:
                return

            messages.append(AIMessage(content=full_content or "", tool_calls=tool_calls_acc))

            for raw_call in tool_calls_acc:
                tool = self.tools.get(raw_call["name"])
                args = self._parse_tool_args(raw_call.get("args", {}))
                llm_purpose = str(args.pop("purpose", "")).strip()

                if tool is None:
                    result = f"错误：未知工具 `{raw_call['name']}`。"
                    messages.append(ToolMessage(content=result, tool_call_id=raw_call["id"]))
                    continue

                perm_level = self._get_permission_level(raw_call["name"])
                if perm_level != PermissionLevel.READ:
                    if permission_broker is None:
                        result = (
                            f"工具 `{raw_call['name']}` 需要 `{perm_level.value}` 级权限，"
                            "但当前没有可用的权限代理。"
                        )
                        messages.append(ToolMessage(content=result, tool_call_id=raw_call["id"]))
                        continue

                    if not permission_broker.should_skip_prompt(raw_call["name"], args):
                        request_id = permission_broker.create_request(perm_level, raw_call["name"], args)
                        yield {
                            "type": "permission_request",
                            "request_id": request_id,
                            "tool": raw_call["name"],
                            "level": perm_level.value,
                            "args": args,
                            "purpose": llm_purpose,
                        }
                        approved = await permission_broker.wait(request_id)
                        if not approved:
                            result = f"用户拒绝了工具 `{raw_call['name']}` 的执行请求。"
                            messages.append(ToolMessage(content=result, tool_call_id=raw_call["id"]))
                            continue

                yield {
                    "type": "tool_call",
                    "tool": raw_call["name"],
                    "args": args,
                    "visible": getattr(self._tool_defs.get(raw_call["name"]), "visible", True),
                }

                try:
                    result = await tool.ainvoke(args)
                    success = self._tool_result_success(result)
                    warning = self._tool_result_warning(raw_call["name"], result)
                except Exception as exc:
                    result = f"工具执行异常：{exc}"
                    success = False
                    warning = False

                yield {
                    "type": "tool_result",
                    "tool": raw_call["name"],
                    "success": success,
                    "warning": warning,
                    "result": str(result)[:500],
                    "visible": getattr(self._tool_defs.get(raw_call["name"]), "visible", True),
                }

                if permission_broker is not None:
                    permission_broker.clear_current_approval()

                messages.append(ToolMessage(content=str(result), tool_call_id=raw_call["id"]))

            self._compress_if_needed(messages)

    def _compress_if_needed(self, messages: list):
        total_chars = sum(len(str(message.content or "")) for message in messages)
        if total_chars <= self.context_char_limit:
            return

        logger.warning("Context too large (%d chars), compressing...", total_chars)

        system_messages = [message for message in messages if isinstance(message, SystemMessage)]
        other_messages = [message for message in messages if not isinstance(message, SystemMessage)]

        if len(other_messages) <= 4:
            return

        keep_tail = other_messages[-4:]
        to_compress = other_messages[1:-4]
        if not to_compress:
            return

        compressed_text = "\n".join(
            f"[{message.__class__.__name__}] {str(message.content)[:200]}..."
            for message in to_compress
        )
        summary_message = SystemMessage(
            content="以下是较早对话的压缩摘要，供你继续参考：\n" + compressed_text
        )

        messages.clear()
        messages.extend(system_messages)
        messages.append(other_messages[0])
        messages.append(summary_message)
        messages.extend(keep_tail)
