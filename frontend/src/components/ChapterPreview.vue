<template>
  <div class="preview-card">
    <div class="preview-head">
      <div class="preview-title">
        <el-tag
          :type="chapter?.kind === 'extra' ? 'warning' : 'success'"
          size="small"
          effect="plain"
        >
          {{ chapter?.kind === 'extra' ? '番外' : '正文' }}
        </el-tag>
        <strong>{{ chapter?.title || '选择章节预览' }}</strong>
        <span v-if="chapter" class="muted">
          {{ formatNumber(countChars(content)) }} 字
        </span>
      </div>
      <el-button
        size="small"
        type="primary"
        plain
        :disabled="!canSplit"
        @click="emitSplit"
      >
        <el-icon><Scissor /></el-icon>
        从选中处拆分
      </el-button>
    </div>

    <div class="selection-tip">
      <template v-if="chapter">
        <template v-if="selectedOffset !== null">
          <el-icon><Select /></el-icon>
          已选中 {{ selectedLength }} 字，拆分点位于本章第
          {{ formatNumber(selectedOffset) }} 个字之后。
        </template>
        <template v-else>
          <el-icon><InfoFilled /></el-icon>
          在正文中用鼠标选中文字，再点击“从选中处拆分”，即可插入章节边界。
        </template>
      </template>
      <template v-else>从左侧选择一个章节。</template>
    </div>

    <div v-loading="loading" class="preview-body">
      <div
        v-if="chapter && content"
        ref="contentRef"
        v-text="content"
        class="preview-content"
        @mouseup="scheduleCapture"
        @keyup="scheduleCapture"
      ></div>
      <el-empty
        v-else-if="!loading"
        :description="chapter ? '本章暂无正文' : '尚未选择章节'"
        :image-size="72"
      />
    </div>

    <div class="preview-foot">
      <span>预览保留原始段落与换行</span>
      <span v-if="chapter" class="mono">
        偏移 {{ formatNumber(chapter.start) }} – {{ formatNumber(chapter.end) }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { countChars, formatNumber } from '@/utils/text'

const props = defineProps({
  chapter: { type: Object, default: null },
  content: { type: String, default: '' },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['split'])
const contentRef = ref(null)
const selectedOffset = ref(null)
const selectedLength = ref(0)

const canSplit = computed(
  () =>
    selectedOffset.value !== null &&
    props.chapter &&
    selectedOffset.value > 0 &&
    selectedOffset.value < countChars(props.content)
)

watch(
  () => [props.chapter?.start, props.chapter?.end, props.content],
  () => {
    selectedOffset.value = null
    selectedLength.value = 0
  }
)

function scheduleCapture() {
  nextTick(captureSelection)
}

function captureSelection() {
  const element = contentRef.value
  if (!element) return
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    selectedOffset.value = null
    selectedLength.value = 0
    return
  }
  const range = selection.getRangeAt(0)
  if (
    !element.contains(range.startContainer) ||
    !element.contains(range.endContainer)
  ) {
    return
  }
  const selectedText = range.toString()
  if (!selectedText.trim()) return

  const prefixRange = document.createRange()
  prefixRange.selectNodeContents(element)
  prefixRange.setEnd(range.startContainer, range.startOffset)
  // Python 端按 Unicode 码点切片，所以这里也必须用码点计数。
  const offset = countChars(prefixRange.toString())
  const total = countChars(props.content)
  if (offset <= 0 || offset >= total) {
    selectedOffset.value = null
    selectedLength.value = 0
    return
  }
  selectedOffset.value = offset
  selectedLength.value = countChars(selectedText)
}

function emitSplit() {
  if (!canSplit.value) {
    ElMessage.warning('请先在正文中选中要作为新章节开头的文字')
    return
  }
  emit('split', selectedOffset.value)
  selectedOffset.value = null
  selectedLength.value = 0
}
</script>

<style scoped>
.preview-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 580px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--card);
  overflow: hidden;
}

.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 15px 18px 10px;
  border-bottom: 1px solid var(--line);
}

.preview-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.preview-title strong {
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 15px;
}

.preview-title .muted {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.selection-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 7px 18px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--card-soft);
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.preview-body {
  flex: 1;
  min-height: 420px;
  max-height: 660px;
  overflow: auto;
  background: var(--card-soft);
}

.preview-content {
  min-height: 100%;
  padding: 22px 24px 40px;
  color: var(--text);
  font-family: 'Songti SC', 'SimSun', serif;
  font-size: 16px;
  line-height: 2;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  user-select: text;
  cursor: text;
}

.preview-content::selection {
  background: var(--gold);
  color: #20180a;
}

.preview-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 18px;
  border-top: 1px solid var(--line-soft);
  color: var(--muted);
  font-size: 11px;
}

@media (max-width: 1100px) {
  .preview-content {
    padding: 18px;
  }
}
</style>
