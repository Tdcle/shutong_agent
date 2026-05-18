# Agent 定义

在此目录下创建子目录，每个子目录包含一个 `AGENT.md` 来定义自定义 Agent。

## AGENT.md 格式

```markdown
---
name: my-agent
display_name: 我的助手
description: 这个 Agent 擅长做什么
agent_class: react
tool_filter: [read_file, write_file, execute_python]
keywords: [关键词1, 关键词2]
priority: 5
icon: bot
requires_permission_broker: false
---

## 系统提示词

在这里写 Agent 专属的系统提示词...
```

## 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| name | 是 | 唯一标识，与目录名一致 |
| display_name | 否 | 前端展示名称 |
| description | 否 | 能力描述，用于 LLM 路由判断 |
| agent_class | 否 | `react` / `plan_execute` / `reflection`，默认 `react` |
| tool_filter | 否 | 可用工具列表，不填则全部可用 |
| keywords | 否 | 触发关键词，命中后优先路由到此 Agent |
| priority | 否 | 优先级，数字越大越优先 |
| icon | 否 | 前端图标标识 |
| requires_permission_broker | 否 | 是否需要权限审批中断 |

## 内置 Agent

内置的 `react`、`plan_execute`、`reflection` 三个 Agent 无需在此定义，通过代码注册。
在此目录下放置同名的 AGENT.md 可以覆盖内置 Agent 的关键词和系统提示词。
