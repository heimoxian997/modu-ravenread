<template>
  <div class="chapter-table">
    <div class="table-head">
      <div>
        <strong>章节与边界</strong>
        <span class="muted">共 {{ chapters.length }} 章</span>
      </div>
      <span class="hint">点击行预览 · 标题可直接编辑</span>
    </div>

    <el-table
      :data="pageRows"
      height="486"
      size="small"
      :row-class-name="rowClassName"
      empty-text="还没有章节"
      @row-click="handleRowClick"
    >
      <el-table-column label="#" width="52">
        <template #default="{ row }">
          <span class="index-cell">{{ row._index + 1 }}</span>
        </template>
      </el-table-column>

      <el-table-column label="标题" min-width="168">
        <template #default="{ row }">
          <el-input
            :model-value="row.title"
            size="small"
            class="title-input"
            @click.stop
            @update:model-value="
              $emit('rename', { index: row._index, title: $event })
            "
          />
        </template>
      </el-table-column>

      <el-table-column label="类型" width="74" align="center">
        <template #default="{ row }">
          <el-tag
            :type="row.kind === 'extra' ? 'warning' : 'success'"
            size="small"
            effect="plain"
            class="kind-tag"
            title="点击切换正文 / 番外"
            @click.stop="$emit('toggle-kind', row._index)"
          >
            {{ row.kind === 'extra' ? '番外' : '正文' }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="字数" width="74" align="right">
        <template #default="{ row }">
          <span class="word-count">{{ formatNumber(row.word_count) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="" width="58" align="center">
        <template #default="{ row }">
          <el-tooltip content="与上一章合并（删除当前边界）" placement="top">
            <el-button
              text
              size="small"
              :disabled="row._index === 0"
              @click.stop="$emit('merge', row._index)"
            >
              <el-icon><Fold /></el-icon>
            </el-button>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <div class="table-foot">
      <span class="muted">
        字数统计为源文本字符跨度；首个章节前的文字将保存为楔子 / 前言。
      </span>
      <el-pagination
        v-if="chapters.length > pageSize"
        v-model:current-page="page"
        small
        background
        layout="prev, pager, next"
        :page-size="pageSize"
        :total="chapters.length"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { formatNumber } from '@/utils/text'

const props = defineProps({
  chapters: { type: Array, default: () => [] },
  selectedIndex: { type: Number, default: null }
})

const emit = defineEmits(['select', 'rename', 'toggle-kind', 'merge'])

const pageSize = 200
const page = ref(1)

const pageRows = computed(() => {
  const offset = (page.value - 1) * pageSize
  return props.chapters.slice(offset, offset + pageSize).map((chapter, index) => ({
    ...chapter,
    _index: offset + index
  }))
})

watch(
  () => props.selectedIndex,
  (index) => {
    if (index === null || index < 0) return
    page.value = Math.floor(index / pageSize) + 1
  }
)

watch(
  () => props.chapters.length,
  () => {
    const maxPage = Math.max(1, Math.ceil(props.chapters.length / pageSize))
    if (page.value > maxPage) page.value = maxPage
  }
)

function handleRowClick(row) {
  if (row?._index === undefined) return
  emit('select', row._index)
}

function rowClassName({ row }) {
  return row._index === props.selectedIndex ? 'selected-row' : ''
}
</script>

<style scoped>
.chapter-table {
  min-width: 0;
}

.table-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 2px 12px;
}

.table-head > div {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.table-head strong {
  font-size: 14px;
}

.table-head .muted,
.hint {
  color: var(--muted);
  font-size: 11px;
}

.index-cell {
  color: var(--muted);
  font-size: 12px;
}

.title-input :deep(.el-input__inner) {
  font-size: 13px;
}

.kind-tag {
  cursor: pointer;
  user-select: none;
}

.word-count {
  color: var(--muted);
  font-size: 12px;
}

.table-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 10px;
}

.table-foot .muted {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}

:deep(.el-table .selected-row > td.el-table__cell) {
  background: var(--card-soft) !important;
}

:deep(.el-table .el-table__row) {
  cursor: pointer;
}
</style>
