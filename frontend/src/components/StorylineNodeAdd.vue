<template>
  <div>
    <el-button :disabled="disabled || !chapters.length || nodes.length >= 40" @click="open">添加事件节点</el-button>
    <el-dialog v-model="visible" title="添加事件节点" width="min(640px, 95vw)" append-to-body :close-on-click-modal="false">
      <p class="hint">选择已保存事件作为依据。添加后，点击外层“保存修改”一起保存。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <el-form label-position="top">
        <el-form-item label="章节"><el-select v-model="chapter" filterable @change="changeChapter"><el-option v-for="c in chapters" :key="c.idx" :label="`第 ${c.idx + 1} 章 · ${c.title}`" :value="c.idx + 1" /></el-select></el-form-item>
        <el-form-item label="事件"><el-select v-model="selectedId" filterable :loading="loading" @change="selectEvent"><el-option v-for="event in events" :key="event.note_id" :label="event.title" :value="event.note_id" :disabled="alreadyUsed(event.note_id)" /></el-select></el-form-item>
        <p v-if="!loading && !events.length" class="hint">本章暂无事件，请先新建事件或选择其他章节。</p>
        <el-button v-if="loadError" text @click="load(false)">重新加载事件</el-button>
        <el-button v-if="nextOffset !== null" text :loading="loading" @click="load(true)">加载更多事件</el-button>
        <div v-if="selectedEvent" class="evidence"><p>{{ selectedEvent.claim }}</p><blockquote v-for="(e, i) in selectedEvent.evidence" :key="i">第 {{ e.chapter_number }} 章：{{ e.quote }}</blockquote></div>
        <el-form-item label="节点结论"><el-input v-model="conclusion" type="textarea" :rows="3" maxlength="500" show-word-limit /></el-form-item>
        <el-form-item label="接在哪个节点之后（先后关系）"><el-select v-model="connectAfter" clearable placeholder="留空时保留为独立节点"><el-option v-for="node in predecessors" :key="node.node_id" :label="`第 ${node.chapter_number} 章 · ${node.conclusion}`" :value="node.node_id" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :disabled="!selectedEvent" @click="add">加入编辑草稿</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { computed, ref, onBeforeUnmount } from 'vue'
import request from '@/utils/request'
import { addedEventNode } from '@/utils/manualNotes'
const props = defineProps({ bookId: Number, chapters: { type: Array, default: () => [] }, nodes: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['add'])
const visible = ref(false), chapter = ref(null), events = ref([]), selectedId = ref(''), conclusion = ref(''), connectAfter = ref('')
const loading = ref(false), error = ref(''), loadError = ref(false), nextOffset = ref(null)
const selectedEvent = computed(() => events.value.find(e => e.note_id === selectedId.value))
const predecessors = computed(() => props.nodes.filter(n => n.chapter_number <= chapter.value))
let generation = 0
function alreadyUsed(id) { return props.nodes.some(n => n.evidence.some(e => e.note_id === id)) }
function open() { chapter.value = props.chapters.at(-1)?.idx + 1; visible.value = true; changeChapter() }
function changeChapter() { selectedId.value = ''; conclusion.value = ''; connectAfter.value = ''; events.value = []; nextOffset.value = null; load(false) }
function selectEvent() { conclusion.value = selectedEvent.value?.claim || '' }
async function load(more) {
  const version = ++generation
  loading.value = true; error.value = ''; loadError.value = false
  try {
    const r = await request({ url: `/books/${props.bookId}/chapter-events/${chapter.value}`, params: { offset: more ? nextOffset.value : 0 }, silent: true })
    if (version !== generation) return
    if (!r.data.ok) throw new Error(r.data.data?.message || '事件加载失败')
    events.value = more ? [...events.value, ...r.data.data.items] : r.data.data.items
    nextOffset.value = r.data.data.next_offset
  } catch (e) { if (version === generation) { error.value = e.message || '事件加载失败'; loadError.value = true } }
  finally { if (version === generation) loading.value = false }
}
function add() {
  try {
    const result = addedEventNode(selectedEvent.value, chapter.value, conclusion.value, props.nodes, `manual_${crypto.randomUUID().replaceAll('-', '')}`, connectAfter.value)
    emit('add', result); visible.value = false
  } catch (e) { error.value = e.message }
}
onBeforeUnmount(() => { generation++ })
</script>
<style scoped>
.el-select { width:100%; }
.hint { color:var(--muted); font-size:12px; line-height:1.8; margin-bottom:16px; }
.evidence { padding:12px 16px; background:var(--card-soft); margin-bottom:16px; line-height:1.8; }
blockquote { margin:8px 0; padding-left:12px; border-left:2px solid var(--gold); font-size:12px; }
</style>
