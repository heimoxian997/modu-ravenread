<template>
  <div v-if="warnings.length" class="warning-list">
    <el-alert
      v-for="(warning, index) in warnings"
      :key="`${warning.code}-${index}`"
      :type="alertType(warning.level)"
      :title="warning.message"
      :closable="false"
      show-icon
    >
      <template v-if="warning.lines?.length" #default>
        <div class="line-examples">
          <span v-for="line in warning.lines" :key="line.line">
            第{{ line.line }}行：{{ line.text }}
          </span>
        </div>
      </template>
    </el-alert>
  </div>
</template>

<script setup>
defineProps({
  warnings: { type: Array, default: () => [] }
})

function alertType(level) {
  if (level === 'error') return 'error'
  if (level === 'success') return 'success'
  return 'warning'
}
</script>

<style scoped>
.warning-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.line-examples {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 5px;
  font-size: 12px;
  opacity: 0.82;
}
</style>
