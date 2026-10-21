<template>
  <div
    class="dropzone"
    :class="{ 'is-active': dragging, 'is-loading': loading }"
    role="button"
    tabindex="0"
    @click="openPicker"
    @keydown.enter.prevent="openPicker"
    @keydown.space.prevent="openPicker"
    @dragenter.prevent="dragging = true"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="handleDrop"
  >
    <input
      ref="fileInput"
      class="file-input"
      type="file"
      accept=".txt,text/plain"
      @change="handleInput"
    />

    <div class="drop-icon">
      <el-icon :size="30"><UploadFilled /></el-icon>
    </div>
    <h3>{{ loading ? '正在上传并探测编码…' : '拖入小说 txt，或点击选择文件' }}</h3>
    <p>只接受 .txt，单个文件不超过 50MB。文件只保存在你的电脑上。</p>
    <div v-if="file" class="picked-file">
      <el-icon><Document /></el-icon>
      <span class="text-ellipsis">{{ file.name }}</span>
      <span class="muted">{{ formatFileSize(file.size) }}</span>
    </div>
    <el-button type="primary" :loading="loading" @click.stop="openPicker">
      {{ file ? '重新选择' : '选择 txt 文件' }}
    </el-button>
    <span class="format-hint">支持 UTF-8 / UTF-8-SIG / GBK / GB18030 编码</span>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { formatFileSize } from '@/utils/text'

const props = defineProps({
  loading: { type: Boolean, default: false },
  file: { type: Object, default: null }
})

const emit = defineEmits(['file-selected'])
const fileInput = ref(null)
const dragging = ref(false)

function openPicker() {
  if (props.loading) return
  if (fileInput.value) fileInput.value.click()
}

function validateAndEmit(file) {
  if (!file || props.loading) return
  if (!file.name.toLowerCase().endsWith('.txt')) {
    ElMessage.error('只支持 .txt 小说文件')
    return
  }
  if (file.size > 50 * 1024 * 1024) {
    ElMessage.error('文件不能超过 50MB')
    return
  }
  if (!file.size) {
    ElMessage.error('文件内容为空')
    return
  }
  emit('file-selected', file)
}

function handleInput(event) {
  validateAndEmit(event.target.files?.[0])
  event.target.value = ''
}

function handleDrop(event) {
  dragging.value = false
  if (props.loading) return
  validateAndEmit(event.dataTransfer?.files?.[0])
}
</script>

<style scoped>
.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 360px;
  padding: 44px 24px;
  border: 1.5px dashed var(--line);
  border-radius: 18px;
  background:
    linear-gradient(180deg, var(--card-soft), var(--card-soft)),
    repeating-linear-gradient(
      45deg,
      transparent,
      transparent 12px,
      var(--gold-soft-bg) 12px,
      var(--gold-soft-bg) 24px
    );
  cursor: pointer;
  outline: none;
  transition: border-color 0.2s ease, transform 0.2s ease, background 0.2s ease;
}

.dropzone:hover,
.dropzone:focus-visible,
.dropzone.is-active {
  border-color: var(--amber);
  background: var(--card-soft);
  transform: translateY(-1px);
}

.dropzone.is-loading {
  cursor: wait;
  opacity: 0.86;
}

.file-input {
  display: none;
}

.drop-icon {
  display: grid;
  place-items: center;
  width: 72px;
  height: 72px;
  margin-bottom: 18px;
  border-radius: 22px;
  color: var(--amber-deep);
  background: var(--card-soft);
}

h3 {
  margin-bottom: 10px;
  font-size: 19px;
  font-weight: 700;
}

p {
  max-width: 520px;
  margin-bottom: 20px;
  color: var(--ink-soft);
  text-align: center;
  line-height: 1.8;
}

.picked-file {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: min(100%, 520px);
  margin-bottom: 18px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--card);
  color: var(--ink);
}

.picked-file span:first-of-type {
  max-width: 340px;
}

.format-hint {
  margin-top: 14px;
  color: var(--muted);
  font-size: 12px;
}
</style>
