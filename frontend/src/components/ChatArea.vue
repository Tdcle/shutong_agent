<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import { useChat } from '../composables/useChat'
import PermissionDialog from './PermissionDialog.vue'

const props = defineProps<{
  sessionId: string
}>()

const emit = defineEmits<{
  sessionCreated: [id: string]
}>()

const { messages, isStreaming, pendingPermission, toolCalls, toolLabel, toolArgPreview, loadMessages, sendMessage, respondPermission, cancelStreaming, clearMessages } = useChat()
const inputText = ref('')
const chatContainer = ref<HTMLElement | null>(null)
const expandedTools = ref<Set<string>>(new Set())

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
  expandedTools.value = new Set()

  try {
    const newSessionId = await sendMessage(props.sessionId, text)
    if (newSessionId && !props.sessionId) {
      emit('sessionCreated', newSessionId)
    }
  } catch (e) {
    console.error('Chat error:', e)
  }

  await nextTick()
  scrollToBottom()
}

async function handlePermissionApprove(requestId: string, remember: boolean = false) {
  pendingPermission.value = null
  await respondPermission(requestId, true, remember)
}

async function handlePermissionDeny(requestId: string) {
  pendingPermission.value = null
  await respondPermission(requestId, false)
}

function toggleToolExpand(tcId: string) {
  const next = new Set(expandedTools.value)
  if (next.has(tcId)) {
    next.delete(tcId)
  } else {
    next.add(tcId)
  }
  expandedTools.value = next
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function autoResize(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 150) + 'px'
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

let codeBlockId = 0

function formatContent(text: string): string {
  return text
    .replace(/```(\w*)\n([\s\S]*?)```/g, (_m, lang, code) => {
      const id = `code-block-${++codeBlockId}`
      const langLabel = lang || 'code'
      return `<div class="code-block" data-id="${id}"><div class="code-header"><span class="code-lang">${langLabel}</span><button class="code-copy-btn" data-copy-target="${id}">复制</button></div><pre><code id="${id}">${code}</code></pre></div>`
    })
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')
}

function handleContentClick(e: Event) {
  const target = e.target as HTMLElement
  if (target.classList.contains('code-copy-btn')) {
    const id = target.getAttribute('data-copy-target')
    if (!id) return
    const codeEl = document.getElementById(id)
    if (codeEl) {
      navigator.clipboard.writeText(codeEl.textContent || '')
      target.textContent = '已复制'
      setTimeout(() => { target.textContent = '复制' }, 1500)
    }
  }
}
</script>

<template>
  <div class="chat-area">
    <div ref="chatContainer" class="chat-messages">
      <div v-if="messages.length === 0" class="welcome">
        <h1>书童</h1>
        <p>你的贴身智能助手 · 会记忆 · 会搜索 · 会写代码</p>
        <p class="welcome-hint">发送消息开始对话</p>
      </div>

      <template v-for="(msg, idx) in messages" :key="'msg_' + idx">
        <div class="message" :class="msg.role">
          <div v-if="msg.role === 'assistant'" class="message-role">Agent</div>
          <div
            class="message-content"
            v-html="formatContent(msg.content) || (msg.isStreaming ? '思考中...' : '')"
            @click="handleContentClick"
          ></div>
          <div v-if="msg.isStreaming" class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
        </div>
      </template>

      <!-- Tool call steps — show inline during streaming -->
      <div v-if="toolCalls.length > 0" class="tool-steps-section">
        <div
          v-for="tc in toolCalls"
          :key="tc.id"
          class="tool-step"
          :class="{ 'is-running': tc.status === 'running', 'is-done': tc.status === 'done', 'is-error': tc.status === 'done' && !tc.success }"
          @click="toggleToolExpand(tc.id)"
        >
          <div class="tool-step-header">
            <span class="tool-step-status">
              <span v-if="tc.status === 'running'" class="spinner"></span>
              <span v-else-if="tc.success">&#10003;</span>
              <span v-else>&#10007;</span>
            </span>
            <span class="tool-step-name">{{ toolLabel(tc.tool) }}</span>
            <span class="tool-step-arg">{{ toolArgPreview(tc.args) }}</span>
            <span class="tool-step-expand">{{ expandedTools.has(tc.id) ? '▾' : '▸' }}</span>
          </div>
          <div v-if="expandedTools.has(tc.id) && tc.result" class="tool-step-body">
            <pre>{{ tc.result }}</pre>
          </div>
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
        @input="autoResize"
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

    <PermissionDialog
      v-if="pendingPermission"
      :request="pendingPermission"
      @approve="handlePermissionApprove"
      @deny="handlePermissionDeny"
    />
  </div>
</template>

<style scoped>
.chat-area {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 860px;
  margin: 0 auto;
  width: 100%;
  padding: 0 16px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px 0;
}

.welcome {
  text-align: center;
  padding: 80px 20px;
}

.welcome h1 {
  font-size: 36px;
  font-weight: 700;
  background: linear-gradient(135deg, var(--primary), #8b5cf6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 12px;
}

.welcome p {
  color: var(--text-secondary);
  font-size: 16px;
}

.welcome-hint {
  margin-top: 28px;
  font-size: 14px !important;
  color: var(--text-muted) !important;
}

.message {
  margin-bottom: 20px;
  max-width: 80%;
}

.message.user {
  margin-left: auto;
}

.message.assistant {
  margin-right: auto;
}

.message-role {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  margin-bottom: 6px;
}

.message-content {
  padding: 12px 16px;
  border-radius: var(--radius);
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.message.user .message-content {
  background: var(--bg-user-msg);
  color: white;
  border-radius: var(--radius) var(--radius) 4px var(--radius);
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.2);
}

.message.assistant .message-content {
  background: var(--bg-assistant-msg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius) var(--radius) var(--radius) 4px;
  box-shadow: var(--shadow-sm);
}

.message-content :deep(.code-block) {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  margin: 10px 0;
  overflow: hidden;
}

.message-content :deep(.code-header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 12px;
  background: #f1f5f9;
  border-bottom: 1px solid var(--border);
}

.message-content :deep(.code-lang) {
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 500;
}

.message-content :deep(.code-copy-btn) {
  font-size: 12px;
  color: var(--primary);
  background: none;
  border: 1px solid var(--primary);
  border-radius: 4px;
  padding: 2px 8px;
  cursor: pointer;
  transition: all 0.15s;
}

.message-content :deep(.code-copy-btn:hover) {
  background: var(--primary);
  color: white;
}

.message-content :deep(pre) {
  background: #f8fafc;
  color: #334155;
  padding: 14px;
  overflow-x: auto;
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
}

.message-content :deep(code) {
  background: #f1f5f9;
  color: var(--primary);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}

.message-content :deep(pre code) {
  background: none;
  color: inherit;
  padding: 0;
}

.typing-indicator {
  display: flex;
  gap: 5px;
  padding: 6px 16px;
}

.typing-indicator span {
  width: 7px;
  height: 7px;
  background: var(--primary);
  border-radius: 50%;
  animation: typing 1.4s infinite;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: scale(0.8); }
  30% { opacity: 1; transform: scale(1); }
}

/* ---- Tool call steps ---- */
.tool-steps-section {
  margin-top: 4px;
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tool-step {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.2s;
  max-width: 500px;
}

.tool-step:hover {
  border-color: var(--primary);
  box-shadow: var(--shadow-sm);
}

.tool-step.is-running {
  border-left: 3px solid var(--warning);
}

.tool-step.is-done {
  border-left: 3px solid var(--success);
}

.tool-step.is-error {
  border-left: 3px solid var(--danger);
}

.tool-step-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  font-size: 13px;
}

.tool-step-status {
  width: 16px;
  text-align: center;
  font-size: 12px;
  color: var(--success);
}

.tool-step.is-error .tool-step-status {
  color: var(--danger);
}

.tool-step.is-running .tool-step-status {
  color: var(--warning);
}

.spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 2px solid var(--warning);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.tool-step-name {
  color: var(--text);
  font-weight: 500;
  white-space: nowrap;
}

.tool-step-arg {
  color: var(--text-muted);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.tool-step-expand {
  color: var(--text-muted);
  font-size: 10px;
  margin-left: auto;
}

.tool-step-body {
  padding: 0 12px 10px 36px;
}

.tool-step-body pre {
  background: #1e293b;
  color: #cbd5e1;
  padding: 10px;
  border-radius: 6px;
  font-size: 11px;
  max-height: 150px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  line-height: 1.4;
}

/* ---- Input area ---- */
.chat-input-area {
  padding: 16px 0;
  border-top: 1px solid var(--border);
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.chat-input {
  flex: 1;
  background: var(--bg-card);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 16px;
  font-size: 14px;
  resize: none;
  outline: none;
  font-family: inherit;
  min-height: 44px;
  max-height: 150px;
  box-shadow: var(--shadow-sm);
  transition: border-color 0.2s, box-shadow 0.2s;
}

.chat-input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
}

.btn-send,
.btn-stop {
  padding: 12px 22px;
  border: none;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  transition: all 0.2s;
}

.btn-send {
  background: var(--primary);
  color: white;
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3);
}

.btn-send:hover:not(:disabled) {
  background: var(--primary-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4);
}

.btn-send:disabled {
  background: var(--bg-input);
  color: var(--text-muted);
  cursor: not-allowed;
  box-shadow: none;
}

.btn-stop {
  background: var(--bg-input);
  color: var(--text);
  border: 1px solid var(--border);
}

.btn-stop:hover {
  background: var(--border);
}
</style>
