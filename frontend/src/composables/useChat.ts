import { ref } from 'vue'
import { apiGet, apiPost, streamChat } from '../api/client'

interface UIMessage {
  role: 'user' | 'assistant'
  content: string
  isStreaming: boolean
}

export interface ToolCallEntry {
  id: string
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'done'
  visible: boolean
  success?: boolean
  warning?: boolean
  result?: string
}

export interface PermissionRequest {
  request_id: string
  tool: string
  level: 'read' | 'write' | 'destroy' | 'shell'
  args: Record<string, unknown>
  purpose: string
}

const TOOL_LABELS: Record<string, string> = {
  read_file: '读取文件',
  write_file: '写入文件',
  edit_file: '编辑文件',
  grep: '内容搜索',
  glob: '文件匹配',
  move_file: '移动文件',
  copy_file: '复制文件',
  delete_file: '删除文件',
  list_files: '列出文件',
  move_paths: '批量移动',
  copy_paths: '批量复制',
  delete_paths: '批量删除',
  search_web: '联网搜索',
  execute_python: '执行 Python',
  execute_shell: '执行命令',
  read_skill: '读取技能说明',
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] || name
}

function toolArgPreview(args: Record<string, unknown>): string {
  if (Array.isArray(args.paths)) {
    const items = args.paths.map((item) => String(item))
    if (items.length === 1) return items[0]
    return `${items[0]} 等 ${items.length} 项`
  }
  if (args.path) return String(args.path)
  if (args.pattern) return String(args.pattern)
  if (args.query) return String(args.query)
  if (args.code) return String(args.code).slice(0, 60)
  if (args.command) return String(args.command).slice(0, 60)
  if (args.source) return `${args.source} -> ${args.destination || ''}`
  if (args.root) return String(args.root)

  const keys = Object.keys(args)
  if (keys.length > 0) {
    return String(args[keys[0]]).slice(0, 60)
  }
  return ''
}

const messages = ref<UIMessage[]>([])
const isStreaming = ref(false)
const abortController = ref<AbortController | null>(null)
const pendingPermission = ref<PermissionRequest | null>(null)
const toolCalls = ref<ToolCallEntry[]>([])

let toolSeq = 0

export function useChat() {
  function addUserMessage(content: string) {
    messages.value.push({ role: 'user', content, isStreaming: false })
  }

  function addAssistantPlaceholder() {
    messages.value.push({ role: 'assistant', content: '', isStreaming: true })
  }

  function appendToLastAssistant(chunk: string) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      last.content += chunk
    }
  }

  function finishStreaming() {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      last.isStreaming = false
    }
    isStreaming.value = false
    pendingPermission.value = null
  }

  async function loadMessages(sessionId: string) {
    messages.value = []
    toolCalls.value = []
    if (!sessionId) return

    try {
      const data = await apiGet<{ messages: Array<{ role: string; content: string }> }>(`/api/sessions/${sessionId}`)
      messages.value = (data.messages || []).map((message) => ({
        role: message.role as 'user' | 'assistant',
        content: message.content || '',
        isStreaming: false,
      }))
    } catch (error) {
      console.error('Failed to load session messages:', error)
    }
  }

  async function respondPermission(requestId: string, approved: boolean, remember = false) {
    try {
      await apiPost('/api/chat/permission-response', {
        request_id: requestId,
        approved,
        remember,
      })
    } catch (error) {
      console.error('Permission response failed:', error)
    }
  }

  async function sendMessage(sessionId: string, content: string) {
    addUserMessage(content)
    addAssistantPlaceholder()
    isStreaming.value = true
    pendingPermission.value = null
    toolCalls.value = []
    toolSeq = 0

    return new Promise<string>((resolve, reject) => {
      const controller = streamChat(
        sessionId,
        content,
        (data) => {
          switch (data.type) {
            case 'text':
              if (data.content) appendToLastAssistant(data.content)
              break
            case 'tool_call':
              toolCalls.value.push({
                id: `tc_${toolSeq++}`,
                tool: data.tool || '',
                args: data.args || {},
                status: 'running',
                visible: data.visible !== false,
              })
              break
            case 'tool_result': {
              const running = [...toolCalls.value]
                .reverse()
                .find((entry) => entry.status === 'running' && entry.tool === data.tool)
              if (running) {
                running.status = 'done'
                running.visible = data.visible !== false
                running.success = data.success
                 running.warning = data.warning === true
                running.result = data.result
              }
              break
            }
            case 'done':
              finishStreaming()
              resolve(data.session_id || sessionId)
              break
            case 'error':
              finishStreaming()
              reject(new Error(data.content || 'Unknown error'))
              break
            case 'permission_request':
              pendingPermission.value = {
                request_id: data.request_id || '',
                tool: data.tool || '',
                level: (data.level as PermissionRequest['level']) || 'write',
                args: data.args || {},
                purpose: data.purpose || '',
              }
              break
          }
        },
        (error) => {
          finishStreaming()
          reject(error)
        },
        () => {},
      )
      abortController.value = controller
    })
  }

  function cancelStreaming() {
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
    finishStreaming()
  }

  function clearMessages() {
    messages.value = []
    toolCalls.value = []
  }

  return {
    messages,
    isStreaming,
    pendingPermission,
    toolCalls,
    toolLabel,
    toolArgPreview,
    loadMessages,
    sendMessage,
    respondPermission,
    cancelStreaming,
    clearMessages,
  }
}
