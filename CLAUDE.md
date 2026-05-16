# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

书童 (ShuTong) — 多层记忆的智能开发助手。Python/FastAPI 后端 + Vue 3/TypeScript 前端，使用 LangChain/LangGraph 驱动 ReAct Agent，支持会话隔离沙箱、权限审批和流式 SSE 交互。

## 常用命令

```bash
# 后端
cd backend
python main.py                    # 启动 API 服务 (http://127.0.0.1:8000)
python main.py --cli              # 交互式 CLI 模式
cd backend && python tests/test_sandbox.py  # 运行沙箱集成测试（唯一测试套件）

# 前端
cd frontend
npm run dev                       # Vite 开发服务器 (http://localhost:5173, 代理 /api 到后端)
npm run build                     # 生产构建

# 一键启动（仅后端）
.\run.bat                         # 安装依赖 + 启动后端
```

## 架构

### 请求流程

```
前端 (Vue/SSE) → /api/chat/stream → session_workspace 设置 → AgentService.stream_chat()
  → 加载 L1 记忆上下文 → 组装 system prompt（环境信息 + 技能列表）
  → ReactAgent.stream() 循环:
      LLM 流式生成 → 工具调用检测 → 权限检查 → 沙箱执行 → sync_back → 结果返回
  → post_turn 写记忆
```

### 关键模块

**Agent 核心** (`app/core/agent.py`):
- `ReactAgent` 是主要使用的 agent。`stream()` 方法支持流式输出、权限中断和上下文压缩。
- 工具调用时注入 `purpose` 参数，要求模型说明调用原因。
- `FAILURE_PREFIXES` / `WARNING_PREFIXES` 用于判断工具执行成功/失败。
- 上下文超过 `context_char_limit` 时压缩中间消息，保留首尾。

**AgentService** (`app/services/agent_service.py`):
- 组装工具注册、记忆管理、技能注册的入口。
- `_build_system_prompt()` 注入运行环境（OS、主机名、桌面路径、workspace）和可用技能列表。
- 支持三种 agent 类型：`react`（默认）、`plan_execute`、`reflection`。

**沙箱** (`app/tools/sandbox.py`):
- `SessionSandboxManager` 为每个 session workspace 懒创建 `.sandbox/` 目录，实现 copy-on-write。
- 写操作在 output workspace 中进行，通过 `sync_back()` 与 host workspace 同步，产生 unified diff。
- 冲突检测：基于上次同步的 SHA256 哈希比对，若 host 被外部修改则不覆盖。
- 命令执行在 Windows 上使用 Job Objects 做进程隔离，可选低完整性令牌（pywin32）。
- 两层命令拦截：Tier 1 始终拦截（恶意命令），Tier 2 仅在用户未批准时拦截。

**权限系统** (`app/tools/permissions.py`):
- `PermissionLevel`: READ < WRITE < DESTROY < SHELL
- `PermissionBroker`: 异步审批机制 — 创建 request → 等待 → 前端 approve/deny
- `PathCapability` + `PathRule`: 会话级路径能力规则，remember 后自动批准同范围操作
- `resolve_with_capability()` 在文件操作工具中检查外部路径是否需要能力批准

**Workspace** (`app/tools/workspace.py`):
- 每个 session 一个隔离工作区（`workspaces/<session_id>/`）
- `resolve()` 强制路径不超出 workspace 边界，外部路径需 `resolve_with_capability()` 批准
- READ 级别的外部路径读取无需审批

**批量文件操作** (`app/tools/file_ops.py`):
- `move_paths` / `copy_paths` / `delete_paths`：按精确路径集合批量操作
- 工作流：先用 `glob` / `list_files` 发现文件列表，确认后传精确路径到批量工具
- 沙箱拦截外部通配符破坏性命令，引导到精确路径工具

**记忆系统** (`app/memory/`):
- L0: `ShortTermMemory` — 会话内最近 N 条消息，支持 LLM 摘要压缩
- L1: `load_l1_context()` — 嵌入向量召回相关记忆注入 system prompt
- L2: `FileMemoryStore` — 文件持久化记忆，支持 LLM 驱动的去重和合并
- L3: 衰退机制 — 闲置记忆重要性递减，活跃记忆（recall_count >= 阈值）不衰退

**模型配置** (`app/profiles.py`):
- `APP_PROFILE=dev` (默认): 本地 Ollama (qwen3:8b) + DDGS 搜索
- `APP_PROFILE=prod`: 阿里云 DashScope (glm-5) + Bocha 搜索

**API 路由**:
- `/api/chat/stream` — SSE 流式聊天，支持 permission_request 事件
- `/api/chat/permission-response` — 前端响应权限审批
- `/api/chat/send` — 非流式聊天（危险工具自动拒绝）
- `/api/sessions` — 会话 CRUD，删除时清理 sandbox + workspace

### 工具权限级别

| 级别 | 工具 |
|------|------|
| read | read_file, grep, glob, list_files, search_web, read_skill |
| write | write_file, edit_file, move_file, copy_file, move_paths, copy_paths |
| destroy | delete_file, delete_paths |
| shell | execute_shell, execute_python |
| shell | execute_shell, execute_python |

### 目录约定

- `backend/data/` — SQLite 数据库
- `backend/workspaces/` — 会话隔离工作区（每个 session 一个子目录）
- `~/.shutong/memory/` — 全局持久记忆存储
- `backend/skills/` — 技能定义（SKILL.md 文件）
- `backend/.agent_runtime/` — 隔离的 Python 运行时，用于 execute_python
