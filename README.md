# 书童 (ShuTong)

多层记忆的智能开发助手。基于 ReAct Agent，支持会话隔离沙箱、权限审批和流式 SSE 交互。

## 技术栈

- **后端**: Python / FastAPI + LangChain / LangGraph
- **前端**: Vue 3 + TypeScript + Vite
- **模型**: 支持 Ollama (本地) 和 DashScope (云端)

## 快速开始

```bash
# 一键启动后端（安装依赖 + 启动 API 服务）
.\run.bat

# 或手动启动
cd backend
pip install -r requirements.txt
python main.py           # API 服务 (http://127.0.0.1:8000)
python main.py --cli     # 交互式 CLI 模式

# 前端
cd frontend
npm install
npm run dev              # Vite 开发服务器 (http://localhost:5173)
```

## 核心特性

- **ReAct Agent**: LLM 驱动的推理-行动循环，支持流式输出
- **会话隔离沙箱**: 每个会话独立的 workspace，写时复制 + 差异同步
- **权限审批**: 四级权限模型（读/写/删除/Shell），异步审批中断
- **多层记忆**: L0 短期 → L1 向量召回 → L2 文件持久化 → L3 衰退机制
- **多 Agent 协作**: 搜索 Agent、代码 Agent、深度分析 Agent 子任务委派
