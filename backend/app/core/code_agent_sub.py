"""CodeSubAgent — ReactAgent-based sub-agent for coding tasks.

Handles code writing, refactoring, debugging, and script generation.
Uses a strengthened ReAct loop with mandatory read-then-edit and verify workflow.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from langchain_core.tools import StructuredTool

from app.core.agent import ReactAgent

logger = logging.getLogger(__name__)

CODE_SUBAGENT_SYSTEM_PROMPT = """你是一位资深软件工程师。用户委托你完成一项编程任务。

## 工作纪律（严格遵守）

### 1. 先理解再动手
- 修改已有代码前，必须先用 read_file 阅读目标文件
- 不熟悉项目结构时，先用 glob / grep / list_files 了解代码布局
- 不确定的函数签名或类名，先 grep 搜索确认

### 2. 精确修改
- 单处修改优先使用 edit_file，传入精确的 old_string
- 只有创建新文件或整体重写时才用 write_file
- 不要顺手改和任务无关的代码

### 3. 写后验证（必须）
- 每改完一个文件，立即运行验证：
  - Python 代码 → execute_python 执行并确认无报错
  - 前端代码 → 确认语法正确
  - 有测试套件 → 运行相关测试
- 验证失败必须根据错误信息修复，不能跳过
- 不要假装验证通过

### 4. 多文件策略
- 修改多个互不依赖的文件时，可以一次工具调用中并行执行
- 有依赖关系的修改必须按顺序处理
- 所有修改完成后做一次整体验证

### 5. 完成报告
任务完成后简要说明：
- 修改了哪些文件，每个改了什么
- 验证结果（通过/失败）
- 注意事项（如果有）

## 可用工具
- read_file / write_file / edit_file：文件读写编辑
- grep / glob / list_files：代码搜索和文件查找
- execute_python：执行 Python 代码、脚本、测试
- execute_bash：运行构建命令、npm/pip、测试框架
- 你没有删除、移动或批量操作权限——这些由用户在主对话中处理
"""


class CodeSubAgent:
    """A sub-agent for coding tasks using ReactAgent with code-specific workflow."""

    def __init__(
        self,
        tools: list[StructuredTool],
        llm=None,
        max_rounds: int = 12,
        on_progress: Callable[[str], None] | None = None,
        tool_defs: dict | None = None,
    ):
        self.tools = tools
        self.llm = llm
        self.max_rounds = max_rounds
        self.on_progress = on_progress or (lambda msg: None)
        self.tool_defs = tool_defs or {}

    async def execute(self, task: str, working_dir: str = ".") -> str:
        """Run the coding task and return the result.

        Args:
            task: Description of the coding task.
            working_dir: Working directory for the task.
        """
        self.on_progress("准备开始编程任务...")

        full_task = f"""## 编程任务

{task}

工作目录：{working_dir}

请按照工作纪律完成上述任务。"""

        agent = ReactAgent(
            tools=self.tools,
            llm=self.llm,
            system_prompt=CODE_SUBAGENT_SYSTEM_PROMPT,
            max_rounds=self.max_rounds,
            tool_defs=self.tool_defs,
        )

        self.on_progress("开始分析和编写...")
        try:
            result = await agent.call(full_task)
        except Exception as e:
            logger.exception("Code sub-agent failed")
            return f"代码任务执行出错：{e}"

        self.on_progress("任务完成")
        return result
