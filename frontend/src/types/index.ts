export interface Session {
  id: string
  title: string
  status: string
  message_count: number
  created_at: string
  updated_at: string
}

export interface Message {
  id: number
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  tool_calls?: ToolCall[]
  created_at: string
}

export interface ToolCall {
  name: string
  arguments: string
  result?: string
}

export interface SSEChunk {
  type: 'text' | 'thinking' | 'error' | 'done' | 'agent_info'
  content?: string
  session_id?: string
  agent_type?: string
  agent_display_name?: string
  agent_icon?: string
}

export interface ChatRequest {
  session_id: string
  message: string
  agent_type: string  // "auto" | "react" | "plan_execute" | "reflection" | ...
}

export interface AgentInfo {
  name: string
  display_name: string
  description: string
  icon: string
  keywords: string[]
}
