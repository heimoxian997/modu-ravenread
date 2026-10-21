<template>
  <div class="compare">
    <div v-if="compare" class="compare-meta">
      <span v-if="compare.rerank_applied">
        RRF 召回 {{ compare.recall }} 条 → 重排，耗时
        {{ compare.rerank_latency_ms }}ms
      </span>
      <span v-else class="warn">
        重排未生效（{{ compare.rerank_error || '模型不可用' }}），两栏一致
      </span>
    </div>

    <div
      v-if="columns.length"
      class="compare-columns"
      :class="{ split: columns.length > 1 }"
    >
      <div
        v-for="column in columns"
        :key="column.key"
        class="compare-column"
      >
        <div v-if="columns.length > 1" class="compare-head">
          <strong>{{ column.title }}</strong>
          <small>{{ column.note }}</small>
        </div>
        <slot :items="column.items" :column="column" />
      </div>
    </div>
    <slot v-else name="empty" />
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  // 打开重排时后端返回的对比结构；关掉时为 null
  compare: { type: Object, default: null },
  // 关掉重排时直接展示的原始列表
  items: { type: Array, default: () => [] }
})

const columns = computed(() => {
  const compare = props.compare
  if (!compare) {
    return props.items.length
      ? [{ key: 'rrf', title: '', note: '', items: props.items }]
      : []
  }
  return [
    {
      key: 'rrf',
      title: 'RRF 融合',
      note: `召回 ${compare.recall} 条，按融合分排序`,
      items: compare.rrf_items || []
    },
    {
      key: 'rerank',
      title: '重排后',
      note: compare.rerank_applied
        ? `bge-reranker，${compare.rerank_latency_ms}ms`
        : '未生效，与左栏一致',
      items: compare.rerank_items || []
    }
  ].filter((column) => column.items.length)
})
</script>

<style scoped>
.compare-meta {
  margin: 10px 0 4px;
  color: var(--muted);
  font-size: 12px;
}

.compare-meta .warn {
  color: var(--gold);
}

.compare-columns {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.compare-columns.split {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.compare-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 2px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
}

.compare-head strong {
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
}

@media (max-width: 900px) {
  .compare-columns.split {
    grid-template-columns: 1fr;
  }
}
</style>
