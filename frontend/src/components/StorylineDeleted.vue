<template>
  <div>
    <el-button @click="open">已删除</el-button>
    <el-dialog v-model="visible" title="已删除的剧情线" width="min(680px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!busyId" :show-close="!busyId" :before-close="close">
      <p class="hint">恢复时会保留删除前的节点、连接与证据引用。若事件已修订，恢复的剧情线会提示核对。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <el-button :loading="loading" :disabled="!!busyId" @click="load(false)">刷新列表</el-button>
      <el-empty v-if="!loading && !items.length && !error" description="当前阅读范围内没有已删除的剧情线" />
      <article v-for="line in items" :key="line.note_id" class="deleted-line">
        <div><h3>{{ line.title }}</h3><span>{{ line.node_count }} 个节点</span></div>
        <el-button type="primary" plain :loading="busyId === line.note_id" :disabled="!!busyId || loading" @click="restore(line)">恢复剧情线</el-button>
      </article>
      <el-button v-if="nextOffset !== null" :loading="loading" :disabled="!!busyId" @click="load(true)">加载更多</el-button>
      <template #footer><el-button :disabled="!!busyId" @click="visible = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { listDeletedStorylines, restoreStoryline } from '@/api/storylines'
const props = defineProps({ bookId: Number })
const emit = defineEmits(['restored'])
const visible = ref(false), loading = ref(false), error = ref(''), items = ref([]), nextOffset = ref(null), busyId = ref('')
let generation = 0
function close(done) { if (!busyId.value) done() }
function open() { visible.value = true; load(false) }
async function load(more) {
  const current = ++generation
  if (!more) { items.value = []; nextOffset.value = null }
  loading.value = true; error.value = ''
  try {
    const data = await listDeletedStorylines(props.bookId, more ? nextOffset.value : 0)
    if (current !== generation) return
    items.value = more ? [...items.value, ...data.items] : data.items; nextOffset.value = data.next_offset
  } catch (e) { if (current === generation) error.value = e.message || '已删除列表加载失败' }
  finally { if (current === generation) loading.value = false }
}
async function restore(line) {
  if (busyId.value || loading.value) return
  const current = generation
  busyId.value = line.note_id; error.value = ''
  try {
    const data = await restoreStoryline(props.bookId, line.note_id, line.version)
    if (current !== generation) return
    ElMessage.success('剧情线已恢复'); emit('restored', data)
    await load(false)
  } catch (e) { if (current === generation) error.value = e.message || '恢复失败，请刷新列表后重试' }
  finally { busyId.value = '' }
}
onBeforeUnmount(() => { generation++ })
</script>
<style scoped>
.hint { color:var(--muted); font-size:12px; line-height:1.8; margin-bottom:16px; }
.el-alert { margin-bottom:16px; }
.deleted-line { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:18px 0; border-bottom:1px solid var(--line); }
h3 { font-size:15px; font-weight:500; margin:0 0 8px; overflow-wrap:anywhere; }
span { font-size:12px; color:var(--muted); }
</style>
