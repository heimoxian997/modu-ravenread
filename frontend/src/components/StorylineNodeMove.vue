<template>
  <div class="move-control">
    <el-button size="small" @click="open">移动到另一条剧情线</el-button>
    <el-dialog v-model="visible" title="移动事件节点" width="min(680px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" :before-close="close">
      <p class="node-summary">第 {{ node.chapter_number }} 章 · {{ node.conclusion }}</p>
      <p class="hint">本节点及其引用的事件一起移入目标线；源线的相关连接会移除，原始事件保留。</p>
      <el-alert v-if="nodeCount === 1" title="这是源线的最后一个节点。移动后，源线会进入“已删除”，可在那里恢复。" type="warning" :closable="false" />
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <el-form label-position="top" :disabled="saving">
        <el-form-item label="查找目标剧情线"><div class="search"><el-input v-model="search" placeholder="按标题、人物或事件查找" clearable @keyup.enter="loadLines(false)" /><el-button :loading="listLoading" @click="loadLines(false)">查找</el-button></div></el-form-item>
        <el-form-item label="目标剧情线"><el-select v-model="targetId" filterable :loading="listLoading" @change="loadTarget"><el-option v-for="line in lines" :key="line.note_id" :value="line.note_id" :label="line.title" /></el-select></el-form-item>
        <p v-if="!listLoading && !lines.length" class="hint">暂无可选剧情线。可以调整搜索词，或先新建另一条剧情线。后期整理且暂不可编辑的线不会列出。</p>
        <el-button v-if="hasMore" text :loading="listLoading" @click="loadLines(true)">加载更多剧情线</el-button>
        <p v-if="targetLoading" class="hint">正在读取目标线…</p>
        <div v-if="target">
          <p class="hint">{{ target.overview }}</p>
          <el-form-item label="接在哪个目标节点之后（先后关系）"><el-select v-model="connectAfter" clearable placeholder="留空时不添加连接"><el-option v-for="n in predecessors" :key="n.node_id" :value="n.node_id" :label="`第 ${n.chapter_number} 章 · ${n.conclusion}`" /></el-select></el-form-item>
        </div>
      </el-form>
      <template #footer><el-button :disabled="saving" @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="!target || targetLoading || listLoading" @click="move">确认移动</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { computed, ref, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { listStorylines, getStorylineEdit, moveStorylineNode } from '@/api/storylines'
const props = defineProps({ bookId: Number, noteId: String, version: Number, node: Object, nodeCount: Number })
const emit = defineEmits(['moved'])
const visible = ref(false), saving = ref(false), error = ref(''), search = ref(''), activeSearch = ref('')
const lines = ref([]), cursor = ref(null), hasMore = ref(false), listLoading = ref(false)
const targetId = ref(''), target = ref(null), targetLoading = ref(false), connectAfter = ref('')
const predecessors = computed(() => (target.value?.nodes || []).filter(n => n.chapter_number <= props.node.chapter_number))
let listVersion = 0, targetVersion = 0, generation = 0
function close(done) { if (!saving.value) done() }
function open() { generation++; search.value = ''; visible.value = true; loadLines(false) }
async function loadLines(more) {
  if (saving.value || (more && listLoading.value)) return
  const version = ++listVersion
  if (!more) {
    activeSearch.value = search.value.trim(); lines.value = []; cursor.value = null; hasMore.value = false
    targetVersion++; targetId.value = ''; target.value = null; targetLoading.value = false; connectAfter.value = ''
  }
  error.value = ''; listLoading.value = true
  try {
    const data = await listStorylines(props.bookId, { ...(activeSearch.value ? { query: activeSearch.value } : {}), ...(more ? { cursor: cursor.value } : {}) })
    if (version !== listVersion) return
    const available = data.items.filter(l => l.note_id !== props.noteId && !l.limited_view)
    lines.value = more ? [...lines.value, ...available] : available
    cursor.value = data.cursor; hasMore.value = data.has_more
  } catch (e) { if (version === listVersion) error.value = e.message || '目标剧情线加载失败' }
  finally { if (version === listVersion) listLoading.value = false }
}
async function loadTarget() {
  const version = ++targetVersion
  target.value = null; targetLoading.value = true; connectAfter.value = ''; error.value = ''
  try { const data = await getStorylineEdit(props.bookId, targetId.value); if (version === targetVersion) target.value = data }
  catch (e) { if (version === targetVersion) error.value = e.message || '目标线不可编辑，请重新选择' }
  finally { if (version === targetVersion) targetLoading.value = false }
}
async function move() {
  if (saving.value || !target.value || targetLoading.value || listLoading.value) return
  const current = generation
  saving.value = true; error.value = ''
  try {
    const data = await moveStorylineNode(props.bookId, props.noteId, props.node.node_id, {
      expected_version: props.version, target_note_id: target.value.note_id, target_expected_version: target.value.version,
      ...(connectAfter.value ? { connect_after: connectAfter.value } : {}) })
    if (current !== generation) return
    visible.value = false; ElMessage.success(data.source_deleted ? '节点已移动，源线已进入“已删除”' : '节点已移到目标剧情线')
    emit('moved', data)
  } catch (e) { if (current === generation) error.value = e.message || '移动失败，请刷新两条剧情线后重试' }
  finally { if (current === generation) saving.value = false }
}
onBeforeUnmount(() => { generation++; listVersion++; targetVersion++ })
</script>
<style scoped>
.move-control { margin-bottom:16px; }
.node-summary { padding:12px 16px; background:var(--card-soft); line-height:1.8; }
.hint { color:var(--muted); font-size:12px; line-height:1.8; margin:14px 0; }
.search { display:flex; gap:8px; width:100%; }
.el-select { width:100%; }
.el-alert { margin:12px 0; }
</style>
