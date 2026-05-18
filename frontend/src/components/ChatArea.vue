<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useChat } from '../composables/useChat'
import PermissionDialog from './PermissionDialog.vue'
import { marked } from 'marked'

const props = defineProps<{
  sessionId: string
}>()

const emit = defineEmits<{
  sessionCreated: [id: string]
  sessionUpdated: []
}>()

const {
  messages,
  isStreaming,
  pendingPermission,
  toolCalls,
  resolvedAgent,
  uploadedFiles,
  deepAnalysis,
  subProgress,
  toolLabel,
  toolArgPreview,
  loadMessages,
  sendMessage,
  respondPermission,
  cancelStreaming,
  clearMessages,
  addFile,
  removeFile,
  hasUploadsInProgress,
  switchToSession,
  claimSession,
} = useChat()

const inputText = ref('')
const chatContainer = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const expandedTools = ref<Set<string>>(new Set())
const previewImageUrl = ref<string | null>(null)
const previewImageName = ref('')

function handlePreviewImage(url: string, name: string) {
  previewImageUrl.value = url
  previewImageName.value = name
}

// Track last session that we created locally to avoid redundant DB reload
let lastCreatedSessionId = ''

// Tracks which sessions we've already loaded from DB
const loadedSessions = new Set<string>()

// After messages render, add copy buttons to code blocks
watch(
  () => messages.value.length,
  async () => {
    await nextTick()
    if (chatContainer.value) enhanceCodeBlocks(chatContainer.value)
  },
)

watch(
  () => props.sessionId,
  async (newId, oldId) => {
    if (newId === oldId) return
    // Don't switch sessions during active streaming
    if (isStreaming.value) return

    if (newId) {
      const isJustCreated = newId === lastCreatedSessionId
      if (isJustCreated) {
        lastCreatedSessionId = ''
        loadedSessions.add(newId)
        claimSession(newId)
        return
      }
      switchToSession(newId)
      if (!loadedSessions.has(newId)) {
        loadedSessions.add(newId)
        loadMessages(newId)
      }
    } else {
      switchToSession('')
    }

    await nextTick()
    scrollToBottom()
  },
  { immediate: true },
)

function onSessionCreated(id: string) {
  lastCreatedSessionId = id
  emit('sessionCreated', id)
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || isStreaming.value) return

  inputText.value = ''
  expandedTools.value = new Set()

  try {
    const newSessionId = await sendMessage(props.sessionId, text)
    if (newSessionId && !props.sessionId) {
      onSessionCreated(newSessionId)
    }
    // Refresh session list in case title was updated
    emit('sessionUpdated')
  } catch (error) {
    console.error('Chat error:', error)
  }

  await nextTick()
  scrollToBottom()
}

async function handlePermissionApprove(requestId: string, remember = false) {
  pendingPermission.value = null
  await respondPermission(requestId, true, remember)
}

async function handlePermissionDeny(requestId: string) {
  pendingPermission.value = null
  await respondPermission(requestId, false)
}

function toggleToolExpand(toolCallId: string) {
  const next = new Set(expandedTools.value)
  if (next.has(toolCallId)) {
    next.delete(toolCallId)
  } else {
    next.add(toolCallId)
  }
  expandedTools.value = next
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSend()
  }
}

function autoResize(event: Event) {
  const element = event.target as HTMLTextAreaElement
  element.style.height = 'auto'
  element.style.height = `${Math.min(element.scrollHeight, 150)}px`
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

// Configure marked for safe rendering (no raw HTML from model output)
marked.setOptions({ breaks: true, gfm: true })

function formatContent(text: string): string {
  return marked.parse(text) as string
}

function handleContentClick(event: Event) {
  const target = event.target as HTMLElement
  if (!target.classList.contains('code-copy-btn')) return

  const id = target.getAttribute('data-copy-target')
  if (!id) return

  const codeEl = document.getElementById(id)
  if (!codeEl) return

  navigator.clipboard.writeText(codeEl.textContent || '')
  target.textContent = '已复制'
  setTimeout(() => {
    target.textContent = '复制'
  }, 1500)
}

// Enhance code blocks rendered by marked with copy buttons
function enhanceCodeBlocks(container: HTMLElement) {
  let codeIdx = 0
  container.querySelectorAll('pre code').forEach((codeEl) => {
    const pre = codeEl.parentElement
    if (!pre || pre.querySelector('.code-header')) return
    codeIdx++
    const id = `code-block-${codeIdx}`
    codeEl.id = id
    const lang = codeEl.className.replace('language-', '') || 'code'
    const header = document.createElement('div')
    header.className = 'code-header'
    header.innerHTML = `<span class="code-lang">${lang}</span><button class="code-copy-btn" data-copy-target="${id}">复制</button>`
    pre.insertBefore(header, codeEl)
  })
}

function handleUploadClick() {
  fileInput.value?.click()
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files) {
    for (const file of input.files) {
      handleFile(file)
    }
  }
  input.value = ''
}

const supportedExts = /\.(png|jpg|jpeg|gif|bmp|webp|tiff|ico|pdf|docx|xlsx|xls|pptx|txt|md|csv|json|xml|yaml|yml|toml|ini|cfg|log|html|css|py|js|ts|java|go|rs|c|cpp|sh|bat|sql|vue|jsx|tsx|svg)$/i

function handleFile(file: File) {
  if (!supportedExts.test(file.name)) {
    console.warn('Unsupported file type:', file.name)
    return
  }
  addFile(file)
}

function handlePaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      event.preventDefault()
      const file = item.getAsFile()
      if (file) {
        // Generate a name for pasted images
        const ext = item.type.split('/')[1] || 'png'
        const renamed = new File([file], `paste_${Date.now()}.${ext}`, { type: item.type })
        handleFile(renamed)
      }
    }
  }
}
</script>

<template>
  <div class="chat-area">
    <div ref="chatContainer" class="chat-messages">
      <div v-if="messages.length === 0" class="welcome">
        <h1>书童</h1>
        <p>可以帮你查看项目、修改文件、执行命令，也能处理日常文件整理和本地自动化任务。</p>
        <p class="welcome-hint">直接输入你的目标即可，复杂任务也可以一步一步来。</p>
      </div>

      <template v-for="(msg, idx) in messages" :key="'msg_' + idx">
        <div class="message" :class="msg.role">
          <div v-if="msg.role === 'assistant'" class="message-role">
            Agent
            <span v-if="resolvedAgent" class="agent-badge-inline">{{ resolvedAgent.displayName }}</span>
          </div>
          <div v-if="msg.images && msg.images.length > 0" class="message-images">
            <div
              v-for="(att, i) in msg.images"
              :key="'msg_att_' + i"
              class="message-attachment"
              :class="{ 'is-image': att.type === 'image' }"
            >
              <img
                v-if="att.type === 'image' && att.previewUrl"
                :src="att.previewUrl"
                :alt="att.filename"
                class="message-image-thumb"
                @click="handlePreviewImage(att.previewUrl, att.filename)"
              />
              <div v-else class="message-doc-badge">
                <span class="doc-ext-badge">{{ att.filename.split('.').pop()?.toUpperCase() }}</span>
                <span class="doc-name">{{ att.filename }}</span>
              </div>
            </div>
          </div>
          <div
            class="message-content"
            v-html="formatContent(msg.content) || (msg.isStreaming ? '思考中...' : '')"
            @click="handleContentClick"
          ></div>
          <div v-if="msg.isStreaming" class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
          <div v-if="subProgress.length > 0" class="sub-progress-box">
            <div v-for="(p, i) in subProgress" :key="'prog_' + i" class="sub-progress-line">{{ p }}</div>
          </div>
          <!-- Tool calls for this message -->
          <div v-if="msg.role === 'assistant' && msg.toolCalls && msg.toolCalls.some((tc) => tc.visible)" class="tool-steps-section">
            <div
              v-for="tc in msg.toolCalls.filter((item) => item.visible)"
              :key="tc.id"
              class="tool-step"
              :class="{
                'is-running': tc.status === 'running',
                'is-done': tc.status === 'done',
                'is-warning': tc.status === 'done' && tc.warning,
                'is-error': tc.status === 'done' && !tc.success,
              }"
              @click="toggleToolExpand(tc.id)"
            >
              <div class="tool-step-header">
                <span class="tool-step-status">
                  <span v-if="tc.status === 'running'" class="spinner"></span>
                  <span v-else-if="tc.warning">&#9888;</span>
                  <span v-else-if="tc.success">&#10003;</span>
                  <span v-else>&#10007;</span>
                </span>
                <span class="tool-step-name">{{ toolLabel(tc.tool) }}</span>
                <span class="tool-step-arg">{{ toolArgPreview(tc.args) }}</span>
                <span class="tool-step-expand">{{ expandedTools.has(tc.id) ? '收起' : '展开' }}</span>
              </div>
              <div v-if="expandedTools.has(tc.id) && tc.result" class="tool-step-body">
                <pre>{{ tc.result }}</pre>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <div v-if="uploadedFiles.length > 0" class="upload-bar">
      <div
        v-for="(file, idx) in uploadedFiles"
        :key="'file_' + idx"
        class="upload-item"
        :class="{ 'is-uploading': file.uploading, 'has-error': file.error }"
        :title="file.error || file.filename"
      >
        <!-- Image: show thumbnail -->
        <img
          v-if="file.type === 'image' && file.previewUrl"
          :src="file.previewUrl"
          :alt="file.filename"
          class="upload-thumb-img"
          @click="handlePreviewImage(file.previewUrl, file.filename)"
        />
        <!-- Document: show file type icon -->
        <div
          v-else
          class="upload-file-icon"
          @click="file.previewUrl && handlePreviewImage(file.previewUrl, file.filename)"
        >
          <span class="file-ext">{{ file.filename.split('.').pop()?.toUpperCase() || 'FILE' }}</span>
        </div>
        <div class="upload-filename">{{ file.filename.length > 12 ? file.filename.slice(0,10) + '...' : file.filename }}</div>
        <div v-if="file.uploading" class="upload-overlay">
          <span class="upload-spinner"></span>
        </div>
        <button
          v-if="!file.uploading"
          class="upload-remove"
          @click.stop="removeFile(idx)"
          title="移除"
        >&times;</button>
        <div v-if="file.error" class="upload-error-flag" title="上传失败">!</div>
      </div>
    </div>

    <div class="chat-input-area">
      <textarea
        v-model="inputText"
        class="chat-input"
        placeholder="输入你的问题或任务...（Enter 发送，Shift+Enter 换行）"
        rows="1"
        :disabled="isStreaming"
        @keydown="handleKeydown"
        @input="autoResize"
        @paste="handlePaste"
      ></textarea>
      <button
        v-if="!isStreaming"
        class="btn-send"
        :disabled="!inputText.trim() || hasUploadsInProgress()"
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

    <input
      ref="fileInput"
      type="file"
      accept=".png,.jpg,.jpeg,.gif,.bmp,.webp,.tiff,.ico,.pdf,.docx,.xlsx,.xls,.pptx,.txt,.md,.csv,.json,.xml,.yaml,.yml,.toml,.ini,.cfg,.log,.html,.css,.py,.js,.ts,.java,.go,.rs,.c,.cpp,.sh,.bat,.sql,.vue,.jsx,.tsx,.svg"
      multiple
      style="display: none"
      @change="handleFileChange"
    />

    <div class="toolbar">
      <button class="toolbar-btn" @click="handleUploadClick" title="上传文件">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/>
        </svg>
        <span>文件</span>
      </button>
      <label class="deep-analysis-toggle" title="启用后将对上传的文件或主题进行深度分析（含搜索和多轮反思）">
        <input v-model="deepAnalysis" type="checkbox" />
        <span>深度分析</span>
      </label>
    </div>

    <!-- Image preview modal -->
    <Teleport to="body">
      <div
        v-if="previewImageUrl"
        class="image-preview-overlay"
        @click="previewImageUrl = null"
      >
        <div class="image-preview-container">
          <img :src="previewImageUrl" :alt="previewImageName" />
          <div class="image-preview-name">{{ previewImageName }}</div>
          <button class="image-preview-close" @click="previewImageUrl = null">&times;</button>
        </div>
      </div>
    </Teleport>

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

.agent-badge-inline {
  display: inline-block;
  margin-left: 8px;
  padding: 1px 8px;
  font-size: 10px;
  font-weight: 500;
  text-transform: none;
  letter-spacing: 0;
  background: var(--primary);
  color: white;
  border-radius: 10px;
  vertical-align: middle;
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

/* Markdown styling */
.message-content :deep(h1) { font-size: 1.4em; font-weight: 700; margin: 0.6em 0 0.3em; }
.message-content :deep(h2) { font-size: 1.2em; font-weight: 600; margin: 0.5em 0 0.25em; }
.message-content :deep(h3) { font-size: 1.05em; font-weight: 600; margin: 0.4em 0 0.2em; }
.message-content :deep(ul), .message-content :deep(ol) { padding-left: 1.5em; margin: 0.3em 0; }
.message-content :deep(li) { margin: 0.15em 0; }
.message-content :deep(table) { border-collapse: collapse; margin: 0.5em 0; font-size: 0.9em; }
.message-content :deep(th), .message-content :deep(td) { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
.message-content :deep(th) { background: #f1f5f9; font-weight: 600; }
.message-content :deep(blockquote) { border-left: 3px solid var(--primary); padding-left: 12px; margin: 0.4em 0; color: var(--text-secondary); }
.message-content :deep(a) { color: var(--primary); text-decoration: underline; }
.message-content :deep(hr) { border: none; border-top: 1px solid var(--border); margin: 0.8em 0; }
.message-content :deep(p) { margin: 0.3em 0; }
.message-content :deep(strong) { font-weight: 600; }
.message-content :deep(em) { font-style: italic; }

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

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing {
  0%, 60%, 100% {
    opacity: 0.3;
    transform: scale(0.8);
  }

  30% {
    opacity: 1;
    transform: scale(1);
  }
}

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

.tool-step.is-warning {
  border-left: 3px solid var(--warning);
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

.tool-step.is-warning .tool-step-status {
  color: var(--warning);
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
  to {
    transform: rotate(360deg);
  }
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

/* Upload bar and toolbar */
.toolbar {
  display: flex;
  gap: 4px;
  padding: 0 0 8px 0;
}

.toolbar-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.toolbar-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: rgba(99, 102, 241, 0.04);
}

.deep-analysis-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  user-select: none;
}

.deep-analysis-toggle:hover {
  border-color: #8b5cf6;
  color: #7c3aed;
}

.deep-analysis-toggle input[type='checkbox'] {
  accent-color: #7c3aed;
  width: 14px;
  height: 14px;
}

.deep-analysis-toggle:has(input:checked) {
  border-color: #8b5cf6;
  color: #7c3aed;
  background: rgba(139, 92, 246, 0.06);
}

.sub-progress-box {
  margin-top: 6px;
  padding: 8px 12px;
  background: rgba(139, 92, 246, 0.06);
  border: 1px solid rgba(139, 92, 246, 0.2);
  border-radius: var(--radius-sm);
  max-height: 140px;
  overflow-y: auto;
}

.sub-progress-line {
  font-size: 11px;
  color: #7c3aed;
  line-height: 1.6;
}

.sub-progress-line::before {
  content: '⏳ ';
}

.upload-bar {
  display: flex;
  gap: 8px;
  padding: 0 0 10px 0;
  flex-wrap: wrap;
}

.upload-item {
  position: relative;
  width: 64px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  flex-shrink: 0;
  cursor: pointer;
  transition: border-color 0.15s;
  display: flex;
  flex-direction: column;
  align-items: center;
  overflow: hidden;
}

.upload-item:hover {
  border-color: #6366f1;
}

.upload-thumb-img {
  width: 64px;
  height: 48px;
  object-fit: cover;
}

.upload-file-icon {
  width: 64px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f1f5f9;
}

.file-ext {
  font-size: 11px;
  font-weight: 700;
  color: #1e293b;
  padding: 2px 6px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  background: #e2e8f0;
}

.upload-filename {
  font-size: 10px;
  font-weight: 600;
  color: #0f172a;
  padding: 4px 4px;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60px;
}

.upload-item.is-uploading .upload-thumb-img,
.upload-item.is-uploading .upload-file-icon {
  opacity: 0.5;
}

.upload-item.has-error {
  border-color: var(--danger);
}

.upload-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.3);
}

.upload-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid white;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.upload-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.6);
  color: white;
  font-size: 12px;
  line-height: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.upload-remove:hover {
  background: rgba(0, 0, 0, 0.8);
}

.upload-error-flag {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(239, 68, 68, 0.15);
  color: var(--danger);
  font-size: 18px;
  font-weight: bold;
  pointer-events: none;
}

/* Images in message bubbles */
.message-images {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.message-attachment {
  flex-shrink: 0;
}

.message-image-thumb {
  width: 80px;
  height: 80px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border);
  cursor: pointer;
  transition: transform 0.15s, border-color 0.15s;
  object-fit: cover;
}

.message-image-thumb:hover {
  border-color: var(--primary);
  transform: scale(1.05);
}

.message-doc-badge {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.doc-ext-badge {
  font-size: 11px;
  font-weight: 700;
  color: #1e293b;
  background: #e2e8f0;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid #cbd5e1;
}

.doc-name {
  font-size: 13px;
  color: var(--text);
  font-weight: 500;
}

.message.user .message-doc-badge {
  background: rgba(255,255,255,0.15);
  border-color: rgba(255,255,255,0.3);
}

.message.user .doc-ext-badge {
  color: #1e293b;
  background: #e2e8f0;
  border-color: #cbd5e1;
}

.message.user .doc-name {
  color: #1e293b;
}

/* Image preview modal */
.image-preview-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: fadeIn 0.15s ease;
}

.image-preview-container {
  position: relative;
  max-width: 90vw;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.image-preview-container img {
  max-width: 90vw;
  max-height: 80vh;
  object-fit: contain;
  border-radius: 8px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.4);
}

.image-preview-name {
  margin-top: 12px;
  color: rgba(255, 255, 255, 0.7);
  font-size: 13px;
}

.image-preview-close {
  position: absolute;
  top: -40px;
  right: -40px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  background: rgba(255, 255, 255, 0.1);
  color: white;
  font-size: 22px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}

.image-preview-close:hover {
  background: rgba(255, 255, 255, 0.25);
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
