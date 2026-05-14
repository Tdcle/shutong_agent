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
  type: 'text' | 'thinking' | 'error' | 'done'
  content?: string
  session_id?: string
}

export interface ChatRequest {
  session_id: string
  message: string
  agent_type: 'react' | 'plan_execute' | 'reflection'
}
