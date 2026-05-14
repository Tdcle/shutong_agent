<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import { useChat } from '../composables/useChat'

const props = defineProps<{
  sessionId: string
}>()

const emit = defineEmits<{
  sessionCreated: [id: string]
}>()

const { messages, isStreaming, loadMessages, sendMessage, cancelStreaming, clearMessages } = useChat()
const inputText = ref('')
const agentType = ref<'react' | 'plan_execute' | 'reflection'>('react')
const chatContainer = ref<HTMLElement | null>(null)

watch(() => props.sessionId, (newId) => {
  if (newId) {
    loadMessages(newId)
  } else {
    clearMessages()
  }
}, { immediate: true })

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || isStreaming.value) return

  inputText.value = ''

  try {
    const newSessionId = await sendMessage(props.sessionId, text, agentType.value)
    if (newSessionId && !props.sessionId) {
      emit('sessionCreated', newSessionId)
    }
  } catch (e) {
    console.error('Chat error:', e)
  }

  await nextTick()
  scrollToBottom()
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

function formatContent(text: string): string {
  // Basic markdown: code blocks and inline code
  return text
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')
}
</script>

<template>
  <div class="chat-area">
    <div class="chat-header">
      <select v-model="agentType" class="agent-select">
        <option value="react">ReAct Agent</option>
        <option value="plan_execute">Plan-Execute Agent</option>
        <option value="reflection">Reflection Agent</option>
      </select>
    </div>

    <div ref="chatContainer" class="chat-messages">
      <div v-if="messages.length === 0" class="welcome">
        <h1>书童</h1>
        <p>你的贴身智能助手 · 会记忆 · 会搜索 · 会写代码</p>
        <p class="welcome-hint">发送消息开始对话</p>
      </div>

      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        class="message"
        :class="msg.role"
      >
        <div class="message-role">{{ msg.role === 'user' ? '你' : 'Agent' }}</div>
        <div
          class="message-content"
          v-html="formatContent(msg.content) || (msg.isStreaming ? '思考中...' : '')"
        ></div>
        <div v-if="msg.isStreaming" class="typing-indicator">
          <span></span><span></span><span></span>
        </div>
      </div>
    </div>

    <div class="chat-input-area">
      <textarea
        v-model="inputText"
        class="chat-input"
        placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
        rows="1"
        :disabled="isStreaming"
        @keydown="handleKeydown"
        @input="() => { const el = $event.target as HTMLTextAreaElement; el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 150) + 'px' }"
      ></textarea>
      <button
        v-if="!isStreaming"
        class="btn-send"
        :disabled="!inputText.trim()"
        @click="handleSend"
      >
        发送
      </button>
      <button
        v-else
        class="btn-stop"
        @click="cancelStreaming"
      >
        停止
      </button>
    </div>
  </div>
</template>

<style scoped>
.chat-area {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 900px;
  margin: 0 auto;
  width: 100%;
}

.chat-header {
  padding: 12px 16px;
  border-bottom: 1px solid #0f3460;
  display: flex;
  align-items: center;
}

.agent-select {
  background: #16213e;
  color: #e0e0e0;
  border: 1px solid #0f3460;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 13px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.welcome {
  text-align: center;
  padding: 60px 20px;
}

.welcome h1 {
  font-size: 32px;
  color: #e94560;
  margin-bottom: 8px;
}

.welcome p {
  color: #888;
  font-size: 16px;
}

.welcome-hint {
  margin-top: 24px;
  font-size: 14px !important;
  color: #666 !important;
}

.message {
  margin-bottom: 16px;
  max-width: 85%;
}

.message.user {
  margin-left: auto;
}

.message.assistant {
  margin-right: auto;
}

.message-role {
  font-size: 12px;
  color: #888;
  margin-bottom: 4px;
}

.message-content {
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}

.message.user .message-content {
  background: #0f3460;
  color: #e0e0e0;
}

.message.assistant .message-content {
  background: #16213e;
  color: #e0e0e0;
  border: 1px solid #0f3460;
}

.message-content :deep(pre) {
  background: #0a0a1a;
  padding: 12px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 8px 0;
  font-size: 13px;
}

.message-content :deep(code) {
  background: #0a0a1a;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}

.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 4px 14px;
}

.typing-indicator span {
  width: 6px;
  height: 6px;
  background: #888;
  border-radius: 50%;
  animation: typing 1.4s infinite;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: scale(0.8); }
  30% { opacity: 1; transform: scale(1); }
}

.chat-input-area {
  padding: 12px 16px;
  border-top: 1px solid #0f3460;
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.chat-input {
  flex: 1;
  background: #16213e;
  color: #e0e0e0;
  border: 1px solid #0f3460;
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 14px;
  resize: none;
  outline: none;
  font-family: inherit;
  min-height: 42px;
  max-height: 150px;
}

.chat-input:focus {
  border-color: #e94560;
}

.btn-send,
.btn-stop {
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  white-space: nowrap;
}

.btn-send {
  background: #e94560;
  color: white;
}

.btn-send:hover:not(:disabled) {
  background: #c73b54;
}

.btn-send:disabled {
  background: #333;
  color: #666;
  cursor: not-allowed;
}

.btn-stop {
  background: #333;
  color: #e0e0e0;
}

.btn-stop:hover {
  background: #444;
}
</style>
