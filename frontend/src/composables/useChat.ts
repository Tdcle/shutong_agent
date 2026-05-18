import { ref } from 'vue'
import type { Attachment } from '../types'
import { apiGet, apiPost, streamChat } from '../api/client'
import { uploadFile } from '../api/client'

interface UIMessage {
  role: 'user' | 'assistant'
  content: string
  isStreaming: boolean
  images?: Attachment[]
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

export interface UploadedFile {
  file: File
  filename: string
  path: string
  previewUrl: string
  type: 'image' | 'document' | 'text'
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
const uploadedFiles = ref<UploadedFile[]>([])
const deepAnalysis = ref(false)
const subProgress = ref<string[]>([])
let _currentSid = ''
let toolSeq = 0

export function useChat() {
  function _saveCurrentToCache() {
    if (_currentSid) {
      sessionMessageCache.set(_currentSid, messages.value.map((m) => ({
        ...m,
        toolCalls: m.toolCalls ? [...m.toolCalls] : undefined,
        images: m.images ? [...m.images] : undefined,
        isStreaming: false,
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
    uploadedFiles.value = []
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

  function addUserMessage(content: string, images?: Attachment[]) {
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
      const data = await apiGet<{
        messages: Array<{ role: string; content: string; tool_calls?: string | null; images?: string | null }>
      }>(`/api/sessions/${sessionId}`)
      const loaded: UIMessage[] = (data.messages || []).map((m) => {
        const msg: UIMessage = {
          role: m.role as 'user' | 'assistant',
          content: m.content || '',
          isStreaming: false,
        }
        if (m.tool_calls && m.role === 'assistant') {
          try {
            const parsed: ToolCallEntry[] = JSON.parse(m.tool_calls).map((tc: Record<string, unknown>, i: number) => ({
              id: `db_tc_${i}`,
              tool: String(tc.tool || ''),
              args: (tc.args || {}) as Record<string, unknown>,
              status: (tc.status === 'done' ? 'done' : 'running') as 'running' | 'done',
              visible: tc.visible !== false,
              success: tc.success as boolean | undefined,
              warning: tc.warning as boolean | undefined,
              result: tc.result as string | undefined,
            }))
            msg.toolCalls = parsed
          } catch {
            // Ignore parse errors for malformed tool_calls JSON
          }
        }
        if (m.images && m.role === 'user') {
          try {
            const raw = JSON.parse(m.images)
            msg.images = raw.map((item: Record<string, unknown> | string) => {
              // Support both old format (array of paths) and new format (array of objects)
              if (typeof item === 'string') {
                const filename = item.split(/[/\\]/).pop() || 'file'
                const ext = filename.split('.').pop()?.toLowerCase() || ''
                const type = ['png','jpg','jpeg','gif','bmp','webp','tiff','ico'].includes(ext) ? 'image'
                  : ['pdf','docx','xlsx','xls','pptx'].includes(ext) ? 'document' : 'text'
                return {
                  path: item, filename, type: type as Attachment['type'],
                  previewUrl: `/api/chat/file/${sessionId}/${encodeURIComponent(filename)}`,
                }
              }
              const a = item as Record<string, unknown>
              const filename = String(a.filename || 'file')
              return {
                path: String(a.path || ''),
                filename,
                type: (a.type as Attachment['type']) || 'document',
                previewUrl: (a.type === 'image')
                  ? `/api/chat/file/${sessionId}/${encodeURIComponent(filename)}`
                  : '',
              }
            })
          } catch {
            // Ignore parse errors
          }
        }
        return msg
      })
      sessionMessageCache.set(sessionId, loaded)
      if (sessionId === _currentSid) {
        messages.value = loaded
        const lastAssistant = [...loaded].reverse().find((m) => m.role === 'assistant')
        toolCalls.value = lastAssistant?.toolCalls || []
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
    const readyFiles = uploadedFiles.value.filter((f) => !f.uploading && !f.error)
    const imagePaths = readyFiles.map((f) => f.path)
    const attachments: Attachment[] = readyFiles.map((f) => ({
      path: f.path, filename: f.filename, type: f.type, previewUrl: f.previewUrl,
    }))

    addUserMessage(content, attachments.length > 0 ? attachments : undefined)
    uploadedFiles.value = []
    addAssistantPlaceholder()
    isStreaming.value = true
    pendingPermission.value = null
    subProgress.value = []
    toolSeq = 0
    const deepMode = deepAnalysis.value
    deepAnalysis.value = false

    return new Promise<string>((resolve, reject) => {
      const controller = streamChat(
        sessionId, content, 'auto', imagePaths, deepMode,
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
            case 'sub_progress':
              if (data.content) subProgress.value.push(data.content)
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
    messages.value = []
    toolCalls.value = []
    clearFiles()
  }

  async function addFile(file: File) {
    const blobUrl = URL.createObjectURL(file)
    const idx = uploadedFiles.value.length
    uploadedFiles.value.push({
      file, filename: file.name, path: '', previewUrl: blobUrl,
      type: 'text', // placeholder, will be updated after upload
      uploading: true,
    })
    try {
      let sid = _currentSid
      if (!sid) {
        const data = await apiPost<{ id: string }>('/api/sessions', {})
        sid = data.id
        _currentSid = sid
      }
      const result = await uploadFile(file, sid)
      URL.revokeObjectURL(blobUrl)
      uploadedFiles.value[idx] = {
        ...uploadedFiles.value[idx],
        path: result.path,
        previewUrl: result.type === 'image' ? result.url : '',
        type: result.type as 'image' | 'document' | 'text',
        uploading: false,
      }
    } catch (err: any) {
      uploadedFiles.value[idx] = {
        ...uploadedFiles.value[idx], uploading: false, error: err.message || 'Upload failed',
      }
    }
  }

  function removeFile(index: number) {
    const entry = uploadedFiles.value[index]
    if (entry) URL.revokeObjectURL(entry.previewUrl)
    uploadedFiles.value.splice(index, 1)
  }

  function clearFiles() {
    const msgUrls = new Set(
      messages.value.filter((m) => m.images).flatMap((m) => m.images!.map((i) => i.previewUrl))
    )
    for (const entry of uploadedFiles.value) {
      if (!msgUrls.has(entry.previewUrl)) URL.revokeObjectURL(entry.previewUrl)
    }
    uploadedFiles.value = []
  }

  function hasUploadsInProgress(): boolean {
    return uploadedFiles.value.some((f) => f.uploading)
  }

  function onSessionDestroyed(sid: string) {
    sessionMessageCache.delete(sid)
  }

  return {
    messages, isStreaming, pendingPermission, toolCalls, resolvedAgent, uploadedFiles,
    deepAnalysis, subProgress,
    toolLabel, toolArgPreview, loadMessages, sendMessage, respondPermission,
    cancelStreaming, clearMessages, addFile, removeFile, clearFiles,
    hasUploadsInProgress, switchToSession, claimSession, onSessionDestroyed,
  }
}
