<script setup lang="ts">
import { computed, ref } from 'vue'

export interface PermissionRequest {
  request_id: string
  tool: string
  level: 'read' | 'write' | 'destroy' | 'shell'
  args: Record<string, unknown>
}

const props = defineProps<{
  request: PermissionRequest
}>()

const emit = defineEmits<{
  approve: [requestId: string, remember: boolean]
  deny: [requestId: string]
}>()

const remember = ref(false)
const canRemember = computed(() => props.request.level === 'write')

function handleApprove() {
  emit('approve', props.request.request_id, remember.value)
}

function handleDeny() {
  emit('deny', props.request.request_id)
}

const levelLabel = computed(() => {
  const map: Record<string, string> = {
    read: '读取',
    write: '写入/修改',
    destroy: '删除',
    shell: '执行命令',
  }
  return map[props.request.level] || props.request.level
})

const levelClass = computed(() => `level-${props.request.level}`)

const toolLabel = computed(() => {
  const map: Record<string, string> = {
    write_file: '写入文件',
    edit_file: '编辑文件',
    move_file: '移动文件',
    delete_file: '删除文件',
    execute_shell: '执行终端命令',
  }
  return map[props.request.tool] || props.request.tool
})

function formatArgs(args: Record<string, unknown>): string {
  const lines: string[] = []
  for (const [key, value] of Object.entries(args)) {
    const val = typeof value === 'string' ? value : JSON.stringify(value)
    lines.push(`${key}: ${val}`)
  }
  return lines.join('\n')
}
</script>

<template>
  <Teleport to="body">
    <div class="permission-overlay">
      <div class="permission-dialog" :class="levelClass">
        <div class="dialog-header">
          <span class="dialog-icon">&#9888;</span>
          <h3>工具操作确认</h3>
        </div>

        <div class="dialog-body">
          <div class="info-row">
            <span class="label">操作</span>
            <span class="value">{{ toolLabel }}</span>
          </div>
          <div class="info-row">
            <span class="label">风险级别</span>
            <span class="badge" :class="levelClass">{{ levelLabel }}</span>
          </div>
          <div class="args-box" v-if="request.args && Object.keys(request.args).length > 0">
            <div class="label">参数</div>
            <pre>{{ formatArgs(request.args) }}</pre>
          </div>
        </div>

        <div v-if="canRemember" class="dialog-remember">
          <label>
            <input type="checkbox" v-model="remember" />
            <span>本次会话内自动允许同类操作</span>
          </label>
        </div>

        <div class="dialog-footer">
          <button class="btn-deny" @click="handleDeny">拒绝</button>
          <button class="btn-approve" @click="handleApprove">允许</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.permission-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}

.permission-dialog {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  width: 440px;
  max-width: 90vw;
  box-shadow: var(--shadow-lg);
  animation: slideUp 0.25s ease-out;
}

.permission-dialog.level-write {
  border-color: var(--warning);
}
.permission-dialog.level-destroy {
  border-color: var(--danger);
}
.permission-dialog.level-shell {
  border-color: var(--danger);
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}

.dialog-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 22px;
  border-bottom: 1px solid var(--border);
}

.dialog-icon {
  font-size: 20px;
  color: var(--warning);
}

.dialog-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}

.dialog-body {
  padding: 18px 22px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.label {
  color: var(--text-secondary);
  font-size: 13px;
}

.value {
  color: var(--text);
  font-size: 14px;
  font-weight: 500;
}

.badge {
  padding: 3px 12px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
}

.badge.level-read {
  background: #ecfdf5;
  color: var(--success);
}
.badge.level-write {
  background: #fffbeb;
  color: var(--warning);
}
.badge.level-destroy {
  background: #fef2f2;
  color: var(--danger);
}
.badge.level-shell {
  background: #fef2f2;
  color: var(--danger);
}

.args-box {
  margin-top: 10px;
}

.args-box .label {
  display: block;
  margin-bottom: 6px;
}

.args-box pre {
  background: #1e293b;
  color: #cbd5e1;
  padding: 12px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  max-height: 120px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}

.dialog-remember {
  padding: 0 22px 4px;
}

.dialog-remember label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
}

.dialog-remember input[type="checkbox"] {
  accent-color: var(--primary);
  width: 15px;
  height: 15px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 22px;
  border-top: 1px solid var(--border);
}

.btn-deny,
.btn-approve {
  padding: 9px 22px;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s;
}

.btn-deny {
  background: var(--bg-input);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
.btn-deny:hover {
  background: var(--border);
}

.btn-approve {
  background: var(--primary);
  color: white;
}
.btn-approve:hover {
  background: var(--primary-hover);
}
</style>
