<script setup lang="ts">
import type { Session } from '../types'
import { useSessions } from '../composables/useSessions'

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
            class="btn-delete"
            @click.stop="deleteSession(s.id); emit('refresh')"
            title="删除会话"
          >
            x
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
  width: 260px;
  min-width: 260px;
  background: #16213e;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #0f3460;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid #0f3460;
}

.sidebar-header h2 {
  margin: 0 0 12px 0;
  font-size: 18px;
  color: #e94560;
}

.btn-new {
  width: 100%;
  padding: 8px;
  background: #e94560;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}

.btn-new:hover {
  background: #c73b54;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.15s;
}

.session-item:hover {
  background: #1a1a3a;
}

.session-item.active {
  background: #0f3460;
}

.session-title {
  font-size: 14px;
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
  color: #888;
}

.btn-delete {
  background: none;
  border: none;
  color: #666;
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
}

.btn-delete:hover {
  color: #e94560;
}

.empty-hint {
  padding: 24px 12px;
  text-align: center;
  color: #666;
  font-size: 14px;
}
</style>
