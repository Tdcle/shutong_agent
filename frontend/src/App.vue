<script setup lang="ts">
import { onMounted } from 'vue'
import SessionSidebar from './components/SessionSidebar.vue'
import ChatArea from './components/ChatArea.vue'
import { useSessions } from './composables/useSessions'

const { loadSessions, currentSessionId, setCurrentId, sessions } = useSessions()

onMounted(() => {
  loadSessions()
})
</script>

<template>
  <div class="app-layout">
    <SessionSidebar
      :sessions="sessions"
      :current-session-id="currentSessionId"
      @select="setCurrentId"
      @new-chat="setCurrentId('')"
      @refresh="loadSessions"
    />
    <main class="main-area">
      <ChatArea
        :session-id="currentSessionId"
        @session-created="(id: string) => { setCurrentId(id); loadSessions() }"
      />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: #1a1a2e;
  color: #e0e0e0;
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
</style>
