const BASE_URL = ''  // Use Vite proxy in dev, same origin in production

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`)
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export async function apiDelete(path: string): Promise<void> {
  const res = await fetch(`${BASE_URL}${path}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
}

export interface SSEData {
  type: string
  content?: string
  session_id?: string
  // agent_info
  agent_type?: string
  agent_display_name?: string
  agent_icon?: string
  // permission_request
  request_id?: string
  level?: string
  purpose?: string
  // tool_call / tool_result
  tool?: string
  args?: Record<string, unknown>
  visible?: boolean
  success?: boolean
  warning?: boolean
  result?: string
}

export function streamChat(
  sessionId: string,
  message: string,
  agentType: string = 'auto',
  images: string[] = [],
  onChunk: (data: SSEData) => void,
  onError: (err: Error) => void,
  onDone: () => void
): AbortController {
  const controller = new AbortController()

  fetch(`${BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message, agent_type: agentType, images }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              onChunk(data)
              if (data.type === 'done') onDone()
            } catch {
              // Skip parse errors for partial chunks
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError(err)
    })

  return controller
}

export async function uploadImage(
  file: File,
  sessionId: string
): Promise<{ filename: string; path: string; session_id: string }> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('session_id', sessionId)

  const res = await fetch(`${BASE_URL}/api/chat/upload`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`Upload failed: ${res.status} ${detail}`)
  }
  return res.json()
}
