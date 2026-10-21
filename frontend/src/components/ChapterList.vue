<template>
  <div class="detail-chapter-list">
    <button
      v-for="chapter in chapters"
      :key="chapter.idx"
      type="button"
      class="detail-chapter-row"
      :class="{ 'is-selected': chapter.idx === selectedIdx }"
      @click="$emit('select', chapter)"
    >
      <span class="chapter-order">{{ String(chapter.idx + 1).padStart(2, '0') }}</span>
      <span class="chapter-title">{{ chapter.title }}</span>
      <span class="chapter-words">{{ formatNumber(chapter.word_count) }} 字</span>
    </button>
    <div v-if="!chapters.length" class="chapter-empty">{{ emptyText }}</div>
  </div>
</template>

<script setup>
import { formatNumber } from '@/utils/text'

defineProps({
  chapters: { type: Array, default: () => [] },
  selectedIdx: { type: Number, default: null },
  emptyText: { type: String, default: '暂无章节' }
})

defineEmits(['select'])
</script>

<style scoped>
.detail-chapter-list {
  display: flex;
  flex-direction: column;
  max-height: 540px;
  overflow: auto;
  border-top: 1px solid var(--line-soft);
}

.detail-chapter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 11px 12px;
  border: 0;
  border-bottom: 1px solid var(--line-soft);
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}

.detail-chapter-row:hover {
  background: var(--card-soft);
}

.detail-chapter-row.is-selected {
  background: var(--card-soft);
}

.chapter-order {
  flex: none;
  width: 28px;
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
}

.chapter-title {
  flex: 1;
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chapter-words {
  flex: none;
  color: var(--muted);
  font-size: 10px;
}

.chapter-empty {
  padding: 28px;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}
</style>
