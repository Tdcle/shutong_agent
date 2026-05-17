<script setup lang="ts">
import { ref } from 'vue'
import type { Session } from '../types'
import { useSessions } from '../composables/useSessions'
import { useChat } from '../composables/useChat'

const props = defineProps<{
  sessions: Session[]
  currentSessionId: string
}>()

const emit = defineEmits<{
  select: [id: string]
  newChat: []
  refresh: []
}>()

const { deleteSession } = useSessions()
const { onSessionDestroyed } = useChat()

// Track which session is awaiting delete confirmation
const pendingDeleteId = ref<string | null>(null)

function requestDelete(id: string) {
  // Toggle: clicking again cancels
  pendingDeleteId.value = pendingDeleteId.value === id ? null : id
}

function confirmDelete(id: string) {
  pendingDeleteId.value = null
  onSessionDestroyed(id)
  deleteSession(id)
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <h2>书童</h2>
      <button class="btn-new" @click="emit('newChat')">+ 新对话</button>
    </div>

    <div class="session-list">
      <div
        v-for="s in sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === currentSessionId }"
        @click="emit('select', s.id)"
      >
        <div class="session-title">{{ s.title || '新对话' }}</div>
        <div class="session-meta">
          <span>{{ s.message_count }} 条消息</span>
          <button
            v-if="pendingDeleteId !== s.id"
            class="btn-delete-icon"
            @click.stop="requestDelete(s.id)"
            title="删除会话"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
          <button
            v-else
            class="btn-confirm-delete"
            @click.stop="confirmDelete(s.id)"
          >
            确认删除
          </button>
        </div>
      </div>

      <div v-if="sessions.length === 0" class="empty-hint">
        暂无对话，点击上方按钮开始
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 280px;
  min-width: 280px;
  background: var(--bg-sidebar);
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}

.sidebar-header {
  padding: 20px;
  border-bottom: 1px solid var(--border);
}

.sidebar-header h2 {
  margin: 0 0 14px 0;
  font-size: 20px;
  font-weight: 700;
  background: linear-gradient(135deg, var(--primary), #8b5cf6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.btn-new {
  width: 100%;
  padding: 10px;
  background: var(--primary);
  color: white;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s;
}

.btn-new:hover {
  background: var(--primary-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
}

.session-item {
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  margin-bottom: 4px;
  transition: all 0.15s;
  border: 1px solid transparent;
}

.session-item:hover {
  background: var(--primary-light);
  border-color: var(--border);
}

.session-item.active {
  background: var(--primary-light);
  border-color: var(--primary);
  box-shadow: var(--shadow-sm);
}

.session-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.session-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--text-muted);
}

.btn-delete-icon {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: all 0.15s;
  display: flex;
  align-items: center;
}

.btn-delete-icon:hover {
  color: var(--danger);
  background: #fef2f2;
}

.btn-confirm-delete {
  background: var(--danger);
  color: white;
  border: none;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  padding: 3px 8px;
  border-radius: 4px;
  transition: all 0.15s;
  white-space: nowrap;
}

.btn-confirm-delete:hover {
  background: #dc2626;
}

.empty-hint {
  padding: 32px 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
}
</style>
