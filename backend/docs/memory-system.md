# 书童 记忆系统

## 概述

书童 采用**四层文件记忆架构**，参考了 Claude Code、Hermes 和 Letta/MemGPT 的设计。零外部依赖，纯 Markdown 文件存储，agent 可用自身的文件工具自维护。

## 架构

```
┌── L0  工作记忆（Working Memory）─────────────────┐
│     当前对话的上下文窗口                             │
│     生命周期：单次对话                               │
│     存储：内存中                                     │
│     容量：取决于模型上下文窗口（128K-200K tokens）     │
└──────────────────────────────────────────────────┘
          ↑ agent 可通过会话历史工具回顾

┌── L1  提示记忆（Prompt Memory）  【每轮对话始终加载】───┐
│     MEMORY.md 前 100 行 + user.md                     │
│     生命周期：持久，agent 自动更新                       │
│     存储：~/.shutong/memory/ + .shutong/memory/      │
│     容量：约 2000-3000 字符（约占 128K 上下文的 2%）     │
└──────────────────────────────────────────────────┘
          ↑ agent 用 read_file 工具按需读取

┌── L2  持久记忆（Persistent Memory）  【按需加载】───────┐
│     详细的记忆条目文件，按类型分目录                       │
│     生命周期：持久                                       │
│     检索方式：读 MEMORY.md 索引 → read_file 具体文件       │
│     存储：Markdown 文件，带 YAML frontmatter             │
└──────────────────────────────────────────────────┘
          ↑ agent 用 grep / shell 工具搜索

┌── L3  对话存档（Conversation Archive）  【可选】────────┐
│     历史会话的完整记录                                    │
│     生命周期：持久，低频访问                                │
│     存储：sessions/*.json（SQLite，每会话一条记录）         │
└──────────────────────────────────────────────────┘
```

## 目录结构

```
~/.shutong/                          ← 全局记忆（跨项目共享）
├── memory/
│   ├── MEMORY.md                     ← 记忆总索引（入口，始终加载前 100 行）
│   ├── user.md                       ← 用户画像（始终加载）
│   ├── feedback/                     ← 反馈型记忆
│   │   ├── _index.md
│   │   └── *.md
│   ├── project/                      ← 项目型记忆
│   │   ├── _index.md
│   │   └── *.md
│   └── reference/                    ← 外部引用
│       ├── _index.md
│       └── *.md
└── config.yaml                       ← 全局配置（预留）

项目目录/
└── .shutong/                        ← 项目记忆（仅该项目可见）
    ├── CONTEXT.md                    ← 项目上下文说明
    └── memory/
        └── MEMORY.md                 ← 项目级记忆索引
```

## 记忆类型

| 类型 | 目录 | 含义 | 示例 |
|------|------|------|------|
| `user` | `user.md`（根级别，单文件） | 用户身份、角色、偏好、知识背景 | 技术栈、沟通风格、职业 |
| `feedback` | `feedback/` | 累积的行为指导 | 代码风格偏好、回复方式偏好 |
| `project` | `project/` | 项目目标、决策、进行中的工作 | 当前任务进度、架构决策 |
| `reference` | `reference/` | 外部系统的指针 | API 文档地址、Bug 追踪链接 |

## 记忆文件格式

每个记忆文件由 **YAML frontmatter** 和 **Markdown 正文** 组成：

```markdown
---
name: feedback-coding-style
description: 代码风格偏好
type: feedback
importance: 0.8
created: 2026-05-14
updated: 2026-05-14
links:
  - user-profile
  - project-shutong
---

## 规则

用户偏好简洁代码，不要过度抽象。

**Why:** 用户多次拒绝引入不必要的设计模式。

**How to apply:**
- 3 行重复代码优于提前引入一个抽象层
- 不做未来才需要的扩展
```

### 字段说明

- `name` — 唯一标识符，也是文件名（不含 .md）和跨文件链接的锚点
- `description` — 一句话概述，显示在索引中
- `type` — 记忆类型：`user` | `feedback` | `project` | `reference`
- `importance` — 0.0~1.0，用于排序、淘汰优先级
- `links` — 关联的其他记忆文件名列表（wiki 风格的交叉引用）

## MEMORY.md 索引

索引文件是记忆系统的入口。每轮对话加载前 100 行（约 70-80 条记忆索引）。

格式：
```markdown
# 书童 Memory Index

## User
- [user](user.md) — Bigchui: 资深后端工程师，偏好简洁风格

## Feedback
- [coding-style](feedback/coding-style.md) — 代码风格：不要过度抽象

## Project
- [shutong-migration](project/shutong-migration.md) — 迁移到 FastAPI，已完成核心引擎

## Reference
- [api-docs](reference/api-docs.md) — DashScope API 文档地址
```

### 索引维护规则

- 每条记忆在索引中占一行（不超过 150 字符）
- 超出 200 行时自动压缩：淘汰低 `importance` 条目，保留前 80%
- `importance >= 0.6` 的条目不会被淘汰

## 隔离机制

```
全局记忆 (~/.shutong/)
  ├── 跨所有项目共享
  ├── user.md（用户画像）
  ├── feedback/*（通用行为指导）
  └── reference/*（外部系统地址）

项目记忆 (.shutong/)
  ├── 仅当前项目可见
  ├── CONTEXT.md（项目是什么，在做什么）
  └── memory/MEMORY.md（项目特定的决策、规划）

会话记忆
  └── 仅单次对话可见，对话结束释放
```

**优先级**：项目 > 全局。同名条目项目级覆盖全局级。

## 加载策略

```
对话开始：
  ① 读取 MEMORY.md 前 100 行 → 拼入 system prompt
  ② 读取 user.md 全文 → 拼入 system prompt
  ③ 若检测到 .shutong/CONTEXT.md → 拼入 system prompt
  ④ 总计 ~2000-3000 字符，占 128K 上下文的约 2%

对话进行中（agent 自行决定）：
  ⑤ 发现任务与某记忆相关 → 调用 read_file 读取对应文件
  ⑥ 需要更多信息 → 调用 execute_bash grep 搜索 feedback/ 或 project/
  ⑦ 需要历史对话 → 通过 API 搜索 sessions 表

对话结束后（自动）：
  ⑧ LLM 从对话中提取新记忆 → 创建/更新记忆文件
  ⑨ 更新 MEMORY.md 索引
  ⑩ 自动检查是否需要压缩索引
```

## 自动记忆提取

系统会用 LLM 分析对话内容，自动提取值得记忆的信息：

- **用户个人信息**（姓名、职业、技能、偏好）→ 更新 `user.md`
- **行为反馈**（用户说"不要 X"、"做 Y 更好"）→ 新建 `feedback/*.md`
- **项目信息**（任务进度、决策）→ 新建 `project/*.md`

### 提取时机

通过 `memory_extract_mode` 控制：

| 模式 | 行为 |
|------|------|
| `on_compress`（默认） | 仅在上下文压缩时批量提取，减少 LLM 调用 |
| `every_turn` | 每轮对话后提取 |
| `manual` | 不自动提取 |

`memory_extract_every_n_turns` 控制最大间隔（默认 10 轮），防止长时间不压缩导致记忆丢失。

## 去重合并

保存新记忆前，系统会检查是否已存在主题相同的记忆：

1. 对 name + description 做中英文分词（中文 bigram + 英文按 kebab/snake/camelCase 拆分）
2. 计算 Jaccard 相似度（默认阈值 0.25）
3. 命中则**合并到已有文件**而非新建，同时略微提升其 importance

## 选择性加载

L1 上下文不再全量注入。每轮对话根据用户问题 `query` 筛选记忆：

- `user.md` 始终加载（用户身份永远相关）
- MEMORY.md 条目按与 query 的**关键词重叠度**评分
- 只注入 top N（`memory_l1_max_entries`，默认 10）条最相关记忆

不相关的记忆不占上下文空间。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory` | 列出所有记忆 |
| GET | `/api/memory/{name}` | 获取单条记忆完整内容 |
| GET | `/api/memory/profile` | 获取用户画像 |
| POST | `/api/memory/search` | 搜索记忆（关键词匹配） |
| POST | `/api/memory` | 创建记忆 |
| DELETE | `/api/memory/{name}` | 删除记忆 |

## 核心模块

| 文件 | 职责 |
|------|------|
| `app/memory/types.py` | 记忆数据类型定义（MemoryEntry, MemoryType） |
| `app/memory/store.py` | 文件存储引擎（CRUD + 索引管理 + 压缩） |
| `app/memory/short_term.py` | L0 工作记忆（会话缓冲区 + 自动摘要） |
| `app/memory/manager.py` | 总调度器（L0-L3 协调 + 自动提取） |

## 与同类系统的对比

| 特性 | Claude Code | Hermes | 书童 |
|------|------------|--------|----------|
| 存储 | Markdown 文件 | SQLite + Markdown | Markdown 文件 |
| L1 始终加载 | MEMORY.md 前 200 行 | MEMORY.md + USER.md | MEMORY.md 前 100 行 + user.md |
| L2 按需加载 | Read 工具 | SQLite FTS5 搜索 | Read 工具 + grep |
| 记忆类型 | 4 种 | 无明确分类 | 4 种（同 Claude Code） |
| 跨记忆链接 | `[[name]]` | 无 | `[[name]]` |
| 项目隔离 | `.claude/` vs `~/.claude/` | 目录分离 | `.shutong/` vs `~/.shutong/` |
| 自动策展 | Dream 进程 | 事实提取 + 技能生成 | 每轮对话后 LLM 提取 |
| 数据库 | 无 | SQLite | **无** |
