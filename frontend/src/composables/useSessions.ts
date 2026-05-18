import { ref, computed } from 'vue'
import type { Session } from '../types'
import { apiGet, apiPost, apiPut, apiDelete } from '../api/client'

const sessions = ref<Session[]>([])
const currentSessionId = ref<string>('')
const loading = ref(false)

export function useSessions() {
  const currentSession = computed(() =>
    sessions.value.find((s) => s.id === currentSessionId.value)
  )

  async function loadSessions() {
    loading.value = true
    try {
      const data = await apiGet<Session[]>('/api/sessions')
      sessions.value = data
    } catch (e) {
      console.error('Failed to load sessions:', e)
    } finally {
      loading.value = false
    }
  }

  async function startNewChat(): Promise<string> {
    // Check if an empty session already exists
    const emptySession = sessions.value.find((s) => s.message_count === 0)
    if (emptySession) {
      currentSessionId.value = emptySession.id
      return emptySession.id
    }
    // Create a new session
    try {
      const data = await apiPost<{ id: string; title: string }>('/api/sessions', {})
      await loadSessions()
      currentSessionId.value = data.id
      return data.id
    } catch (e) {
      console.error('Failed to create session:', e)
      currentSessionId.value = ''
      return ''
    }
  }

  function selectSession(id: string) {
    currentSessionId.value = id
  }

  async function deleteSession(id: string) {
    await apiDelete(`/api/sessions/${id}`)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    if (currentSessionId.value === id) {
      currentSessionId.value = ''
    }
  }

  async function updateSessionTitle(id: string, title: string) {
    await apiPut(`/api/sessions/${id}`, { title })
    const session = sessions.value.find((s) => s.id === id)
    if (session) session.title = title
  }

  function setCurrentId(id: string) {
    currentSessionId.value = id
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    loading,
    loadSessions,
    startNewChat,
    selectSession,
    deleteSession,
    updateSessionTitle,
    setCurrentId,
  }
}
