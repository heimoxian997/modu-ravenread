<template>
  <div class="sample-input">
    <div v-for="(_, index) in modelValue" :key="index" class="sample-row">
      <span class="sample-index">{{ index + 1 }}</span>
      <el-input
        :model-value="modelValue[index]"
        :placeholder="placeholder"
        :disabled="disabled"
        clearable
        @update:model-value="updateValue(index, $event)"
      />
      <el-button
        v-if="modelValue.length > min"
        text
        type="danger"
        :disabled="disabled"
        title="删除这个样例"
        @click="removeRow(index)"
      >
        <el-icon><Delete /></el-icon>
      </el-button>
    </div>

    <div v-if="!modelValue.length" class="empty-sample">
      <span>可选，不填写则默认没有番外。</span>
    </div>

    <div class="sample-actions">
      <el-button
        v-if="modelValue.length < max"
        size="small"
        :disabled="disabled"
        @click="addRow"
      >
        <el-icon><Plus /></el-icon>
        {{ addLabel }}
      </el-button>
      <span class="limit">最多 {{ max }} 个</span>
    </div>

    <div v-if="examples.length" class="examples">
      <span class="example-label">示例：</span>
      <button
        v-for="example in examples"
        :key="example"
        type="button"
        class="example-chip"
        :disabled="disabled"
        @click="useExample(example)"
      >
        {{ example }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ElMessage } from 'element-plus'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 2 },
  placeholder: { type: String, default: '' },
  addLabel: { type: String, default: '再添加一个样例' },
  examples: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

function emitRows(rows) {
  emit('update:modelValue', rows)
}

function updateValue(index, value) {
  const rows = [...props.modelValue]
  rows[index] = value
  emitRows(rows)
}

function addRow() {
  if (props.modelValue.length >= props.max) return
  emitRows([...props.modelValue, ''])
}

function removeRow(index) {
  if (props.modelValue.length <= props.min) return
  const rows = [...props.modelValue]
  rows.splice(index, 1)
  emitRows(rows)
}

function useExample(example) {
  const rows = [...props.modelValue]
  const emptyIndex = rows.findIndex((item) => !String(item || '').trim())
  if (emptyIndex >= 0) {
    rows[emptyIndex] = example
  } else if (rows.length < props.max) {
    rows.push(example)
  } else {
    ElMessage.info(`最多填写 ${props.max} 个样例`)
    return
  }
  emitRows(rows)
}
</script>

<style scoped>
.sample-input {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sample-row {
  display: flex;
  align-items: center;
  gap: 9px;
}

.sample-index {
  display: grid;
  place-items: center;
  flex: none;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: var(--card-soft);
  color: var(--gold);
  font-size: 12px;
  font-weight: 700;
}

.empty-sample {
  padding: 12px 14px;
  border: 1px dashed var(--line);
  border-radius: 9px;
  color: var(--muted);
  font-size: 13px;
}

.sample-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.limit {
  color: var(--muted);
  font-size: 12px;
}

.examples {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  padding-top: 2px;
}

.example-label {
  color: var(--muted);
  font-size: 12px;
}

.example-chip {
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--card-soft);
  color: var(--gold);
  font-size: 12px;
  cursor: pointer;
}

.example-chip:hover {
  border-color: var(--gold);
  background: var(--card-soft);
}
</style>
