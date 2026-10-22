<template>
  <el-form label-position="top" class="book-form" @submit.prevent>
    <div class="form-grid">
      <el-form-item label="书名" required>
        <el-input
          :model-value="modelValue.title"
          maxlength="200"
          show-word-limit
          placeholder="例如：长夜书店"
          :disabled="disabled"
          @update:model-value="update('title', $event)"
        />
      </el-form-item>
      <el-form-item label="作者">
        <el-input
          :model-value="modelValue.author"
          maxlength="100"
          placeholder="可选"
          :disabled="disabled"
          @update:model-value="update('author', $event)"
        />
      </el-form-item>
    </div>

    <el-form-item label="主角">
      <el-input
        :model-value="modelValue.protagonistsText"
        maxlength="80"
        placeholder="可选，1–3 个名字，用逗号或换行分隔"
        :disabled="disabled"
        @update:model-value="update('protagonistsText', $event)"
      />
      <div class="field-hint">
        用于第二步之后的角色库默认项；现在可以留空。
        <span v-if="protagonistNames.length" class="protagonist-preview">
          已识别：
          <el-tag
            v-for="name in protagonistNames"
            :key="name"
            size="small"
            effect="plain"
          >
            {{ name }}
          </el-tag>
        </span>
      </div>
    </el-form-item>

    <el-form-item label="简介 / 文案">
      <el-input
        :model-value="modelValue.synopsis"
        type="textarea"
        :rows="5"
        maxlength="20000"
        show-word-limit
        resize="vertical"
        placeholder="可选。后续会作为 AI 伴读与角色聊天的世界观背景；没有就留空。"
        :disabled="disabled"
        @update:model-value="update('synopsis', $event)"
      />
      <p class="field-hint">伴读只读取前 1000 字，超出部分不会进入提示词。</p>
    </el-form-item>
  </el-form>
</template>

<script setup>
import { computed } from 'vue'
import { parseProtagonists } from '@/utils/text'

const props = defineProps({
  modelValue: {
    type: Object,
    default: () => ({
      title: '',
      author: '',
      synopsis: '',
      protagonistsText: ''
    })
  },
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const protagonistNames = computed(() =>
  parseProtagonists(props.modelValue.protagonistsText)
)

function update(field, value) {
  emit('update:modelValue', {
    ...props.modelValue,
    [field]: value
  })
}
</script>

<style scoped>
.book-form {
  width: 100%;
}

.form-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 18px;
}

.field-hint {
  margin-top: 7px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.protagonist-preview {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: 8px;
}

@media (max-width: 700px) {
  .form-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
