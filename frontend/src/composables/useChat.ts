import { ref } from 'vue'
import { apiGet, apiPost, streamChat } from '../api/client'
import { uploadImage } from '../api/client'

export interface SentImage {
  filename: string
  path: string
  previewUrl: string
}

interface UIMessage {
  role: 'user' | 'assistant'
  content: string
  isStreaming: boolean
  images?: SentImage[]
  toolCalls?: ToolCallEntry[]
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

export interface UploadedImage {
  file: File
  filename: string
  path: string
  previewUrl: string
  uploading: boolean
  error?: string
}

const TOOL_LABELS: Record<string, string> = {
  read_file: '读取文件', write_file: '写入文件', edit_file: '编辑文件',
  grep: '内容搜索', glob: '文件匹配', move_file: '移动文件', copy_file: '复制文件',
  delete_file: '删除文件', list_files: '列出文件', move_paths: '批量移动',
  copy_paths: '批量复制', delete_paths: '批量删除', search_web: '联网搜索',
  execute_python: '执行 Python', execute_bash: '执行命令', read_skill: '读取技能说明',
  analyze_image: '分析图片',
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
  if (keys.length > 0) return String(args[keys[0]]).slice(0, 60)
  return ''
}

// === Per-session message cache (DB-loaded, not live streaming state) ===
const sessionMessageCache = new Map<string, UIMessage[]>()

// === Module-level reactive state ===
const messages = ref<UIMessage[]>([])
const isStreaming = ref(false)
const abortControllerRef = ref<AbortController | null>(null)
const pendingPermission = ref<PermissionRequest | null>(null)
const toolCalls = ref<ToolCallEntry[]>([])
const resolvedAgent = ref<{ name: string; displayName: string; icon: string } | null>(null)
const uploadedImages = ref<UploadedImage[]>([])
let _currentSid = ''
let toolSeq = 0

export function useChat() {
  function _saveCurrentToCache() {
    if (_currentSid) {
      sessionMessageCache.set(_currentSid, messages.value.map((m) => ({
        ...m,
        toolCalls: m.toolCalls ? [...m.toolCalls] : undefined,
        images: m.images ? [...m.images] : undefined,
        isStreaming: false, // never cache streaming state
      })))
    }
  }

  function _loadFromCache(sid: string): boolean {
    const cached = sessionMessageCache.get(sid)
    if (cached) {
      messages.value = cached
      const lastAssistant = [...cached].reverse().find((m) => m.role === 'assistant')
      toolCalls.value = lastAssistant?.toolCalls || []
      return true
    }
    return false
  }

  function switchToSession(sid: string) {
    _saveCurrentToCache()
    _currentSid = sid || ''
    uploadedImages.value = []
    resolvedAgent.value = null
    isStreaming.value = false
    pendingPermission.value = null
    abortControllerRef.value = null
    if (sid && _loadFromCache(sid)) return
    messages.value = []
    toolCalls.value = []
  }

  function claimSession(sid: string) {
    _currentSid = sid
    _saveCurrentToCache()
  }

  function addUserMessage(content: string, images?: SentImage[]) {
    messages.value.push({ role: 'user', content, isStreaming: false, images })
  }

  function addAssistantPlaceholder() {
    messages.value.push({ role: 'assistant', content: '', isStreaming: true, toolCalls: [] })
    toolCalls.value = messages.value[messages.value.length - 1].toolCalls!
  }

  function appendToLastAssistant(chunk: string) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') last.content += chunk
  }

  function finishStreaming() {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') last.isStreaming = false
    isStreaming.value = false
    pendingPermission.value = null
  }

  async function loadMessages(sessionId: string) {
    if (!sessionId) return
    try {
      const data = await apiGet<{ messages: Array<{ role: string; content: string }> }>(`/api/sessions/${sessionId}`)
      const loaded: UIMessage[] = (data.messages || []).map((m) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content || '',
        isStreaming: false,
      }))
      sessionMessageCache.set(sessionId, loaded)
      if (sessionId === _currentSid) {
        messages.value = loaded
        toolCalls.value = []
      }
    } catch (error) {
      console.error('Failed to load session messages:', error)
    }
  }

  async function respondPermission(requestId: string, approved: boolean, remember = false) {
    try {
      await apiPost('/api/chat/permission-response', { request_id: requestId, approved, remember })
    } catch (error) {
      console.error('Permission response failed:', error)
    }
  }

  async function sendMessage(sessionId: string, content: string) {
    const readyImages = uploadedImages.value.filter((img) => !img.uploading && !img.error)
    const imagePaths = readyImages.map((img) => img.path)
    const sentImages: SentImage[] = readyImages.map((img) => ({
      filename: img.filename, path: img.path, previewUrl: img.previewUrl,
    }))

    addUserMessage(content, sentImages.length > 0 ? sentImages : undefined)
    uploadedImages.value = []
    addAssistantPlaceholder()
    isStreaming.value = true
    pendingPermission.value = null
    toolSeq = 0

    return new Promise<string>((resolve, reject) => {
      const controller = streamChat(
        sessionId, content, 'auto', imagePaths,
        (data) => {
          switch (data.type) {
            case 'text':
              if (data.content) appendToLastAssistant(data.content)
              break
            case 'tool_call':
              toolCalls.value.push({
                id: `tc_${toolSeq++}`, tool: data.tool || '', args: data.args || {},
                status: 'running', visible: data.visible !== false,
              })
              break
            case 'tool_result': {
              const running = [...toolCalls.value].reverse()
                .find((e) => e.status === 'running' && e.tool === data.tool)
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
            case 'agent_info':
              resolvedAgent.value = {
                name: data.agent_type || 'react',
                displayName: data.agent_display_name || 'ReAct推理',
                icon: data.agent_icon || 'bot',
              }
              break
            case 'permission_request':
              pendingPermission.value = {
                request_id: data.request_id || '', tool: data.tool || '',
                level: (data.level as PermissionRequest['level']) || 'write',
                args: data.args || {}, purpose: data.purpose || '',
              }
              break
          }
        },
        (error) => { finishStreaming(); reject(error) },
        () => {},
      )
      abortControllerRef.value = controller
    })
  }

  function cancelStreaming() {
    if (abortControllerRef.value) {
      abortControllerRef.value.abort()
      abortControllerRef.value = null
    }
    finishStreaming()
  }

  function clearMessages() {
    for (const msg of messages.value) {
      if (msg.images) for (const img of msg.images) URL.revokeObjectURL(img.previewUrl)
    }
    messages.value = []
    toolCalls.value = []
    clearImages()
  }

  async function addImage(file: File) {
    const previewUrl = URL.createObjectURL(file)
    const idx = uploadedImages.value.length
    uploadedImages.value.push({ file, filename: file.name, path: '', previewUrl, uploading: true })
    try {
      // Ensure we have a session before uploading
      let sid = _currentSid
      if (!sid) {
        const data = await apiPost<{ id: string }>('/api/sessions', {})
        sid = data.id
        _currentSid = sid
      }
      const result = await uploadImage(file, sid)
      uploadedImages.value[idx] = {
        ...uploadedImages.value[idx], path: result.path, uploading: false,
      }
    } catch (err: any) {
      uploadedImages.value[idx] = {
        ...uploadedImages.value[idx], uploading: false, error: err.message || 'Upload failed',
      }
    }
  }

  function removeImage(index: number) {
    const entry = uploadedImages.value[index]
    if (entry) URL.revokeObjectURL(entry.previewUrl)
    uploadedImages.value.splice(index, 1)
  }

  function clearImages() {
    const msgUrls = new Set(
      messages.value.filter((m) => m.images).flatMap((m) => m.images!.map((i) => i.previewUrl))
    )
    for (const entry of uploadedImages.value) {
      if (!msgUrls.has(entry.previewUrl)) URL.revokeObjectURL(entry.previewUrl)
    }
    uploadedImages.value = []
  }

  function hasUploadsInProgress(): boolean {
    return uploadedImages.value.some((img) => img.uploading)
  }

  function onSessionDestroyed(sid: string) {
    sessionMessageCache.delete(sid)
  }

  return {
    messages, isStreaming, pendingPermission, toolCalls, resolvedAgent, uploadedImages,
    toolLabel, toolArgPreview, loadMessages, sendMessage, respondPermission,
    cancelStreaming, clearMessages, addImage, removeImage, clearImages,
    hasUploadsInProgress, switchToSession, claimSession, onSessionDestroyed,
  }
}
