<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { apiGet, apiPut } from '../api/client'

interface UserConfig {
  dashscope_api_key: string
  text_model: string
  vision_model: string
  bocha_api_key: string
}

const show = ref(false)
const saving = ref(false)
const message = ref('')
const config = ref<UserConfig>({
  dashscope_api_key: '',
  text_model: 'qwen-plus',
  vision_model: 'qwen-vl-plus',
  bocha_api_key: '',
})

onMounted(async () => {
  try {
    const data = await apiGet<UserConfig>('/api/config')
    if (data) {
      config.value = data
    }
  } catch {
    // Config not available — use defaults
  }
})

async function save() {
  saving.value = true
  message.value = ''
  try {
    await apiPut('/api/config', config.value)
    message.value = '配置已保存'
  } catch (e: any) {
    message.value = '保存失败: ' + (e?.message || e)
  } finally {
    saving.value = false
  }
}

function toggle() {
  show.value = !show.value
  message.value = ''
}
</script>

<template>
  <!-- Gear icon in bottom-left -->
  <button class="btn-settings-toggle" @click="toggle" title="配置">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  </button>

  <!-- Modal overlay -->
  <div v-if="show" class="settings-overlay" @click.self="toggle">
    <div class="settings-panel">
      <div class="settings-header">
        <h3>模型配置</h3>
        <button class="btn-close" @click="toggle">&times;</button>
      </div>

      <div class="settings-body">
        <div class="field-group">
          <label class="field-label">
            DashScope API Key <span class="required">*</span>
          </label>
          <input
            v-model="config.dashscope_api_key"
            type="password"
            placeholder="sk-..."
            class="field-input"
          />
          <span class="field-hint">阿里云 DashScope API Key，用于调用文本和多模态模型</span>
        </div>

        <div class="field-group">
          <label class="field-label">
            文本模型 <span class="required">*</span>
          </label>
          <input
            v-model="config.text_model"
            type="text"
            placeholder="qwen-plus"
            class="field-input"
          />
          <span class="field-hint">纯文本对话模型，如 qwen-plus、qwen-max、glm-5</span>
        </div>

        <div class="field-group">
          <label class="field-label">多模态模型</label>
          <input
            v-model="config.vision_model"
            type="text"
            placeholder="（可选）qwen-vl-plus"
            class="field-input"
          />
          <span class="field-hint">支持图片理解的视觉模型。留空则使用 analyze_image 工具降级识别</span>
        </div>

        <div class="field-group">
          <label class="field-label">博查 API Key</label>
          <input
            v-model="config.bocha_api_key"
            type="password"
            placeholder="（可选）用于联网搜索"
            class="field-input"
          />
          <span class="field-hint">博查搜索 API Key，不填则使用免费搜索（DuckDuckGo）</span>
        </div>

        <div v-if="message" class="message" :class="{ error: message.includes('失败') }">
          {{ message }}
        </div>
      </div>

      <div class="settings-footer">
        <button class="btn-cancel" @click="toggle">取消</button>
        <button class="btn-save" :disabled="saving" @click="save">
          {{ saving ? '保存中...' : '保存' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.btn-settings-toggle {
  position: fixed;
  bottom: 16px;
  left: 16px;
  z-index: 100;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  box-shadow: var(--shadow-sm);
}

.btn-settings-toggle:hover {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-light);
}

/* Overlay */
.settings-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
}

.settings-panel {
  background: white;
  border-radius: 12px;
  width: 420px;
  max-width: 90vw;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
}

.settings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px 0;
}

.settings-header h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.btn-close {
  background: none;
  border: none;
  font-size: 22px;
  cursor: pointer;
  color: var(--text-muted);
  padding: 0 4px;
}

.btn-close:hover {
  color: var(--text);
}

.settings-body {
  padding: 20px 24px;
  overflow-y: auto;
  flex: 1;
}

.field-group {
  margin-bottom: 18px;
}

.field-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 6px;
  color: var(--text);
}

.required {
  color: var(--danger);
}

.field-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 14px;
  font-family: inherit;
  transition: border-color 0.2s;
  box-sizing: border-box;
}

.field-input:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
}

.field-hint {
  display: block;
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
}

.message {
  padding: 10px 14px;
  border-radius: 6px;
  background: #f0fdf4;
  color: #166534;
  font-size: 13px;
  margin-top: 8px;
}

.message.error {
  background: #fef2f2;
  color: #991b1b;
}

.settings-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 16px 24px;
  border-top: 1px solid var(--border);
}

.btn-cancel,
.btn-save {
  padding: 8px 20px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.15s;
}

.btn-cancel {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
}

.btn-cancel:hover {
  background: var(--border);
}

.btn-save {
  background: var(--primary);
  color: white;
}

.btn-save:hover:not(:disabled) {
  background: var(--primary-hover);
}

.btn-save:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
