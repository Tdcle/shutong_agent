import { ref } from 'vue'
import { apiGet, streamChat } from '../api/client'

interface UIMessage {
  role: 'user' | 'assistant'
  content: string
  isStreaming: boolean
}

// Module-level state — shared across ChatArea instances
const messages = ref<UIMessage[]>([])
const isStreaming = ref(false)
const abortController = ref<AbortController | null>(null)

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
  }

  async function loadMessages(sessionId: string) {
    messages.value = []
    if (!sessionId) return

    try {
      const data = await apiGet<{ messages: Array<{ role: string; content: string }> }>(`/api/sessions/${sessionId}`)
      messages.value = (data.messages || []).map((m) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content || '',
        isStreaming: false,
      }))
    } catch (e) {
      console.error('Failed to load session messages:', e)
    }
  }

  async function sendMessage(
    sessionId: string,
    content: string,
    agentType: string = 'react'
  ) {
    addUserMessage(content)
    addAssistantPlaceholder()
    isStreaming.value = true

    return new Promise<string>((resolve, reject) => {
      const controller = streamChat(
        sessionId,
        content,
        agentType,
        (data) => {
          if (data.type === 'text' && data.content) {
            appendToLastAssistant(data.content)
          } else if (data.type === 'done') {
            finishStreaming()
            resolve(data.session_id || sessionId)
          } else if (data.type === 'error') {
            finishStreaming()
            reject(new Error(data.content || 'Unknown error'))
          }
        },
        (err) => {
          finishStreaming()
          reject(err)
        },
        () => {}
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
  }

  return {
    messages,
    isStreaming,
    loadMessages,
    sendMessage,
    cancelStreaming,
    clearMessages,
  }
}
