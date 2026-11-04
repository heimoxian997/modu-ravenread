<template>
  <div class="create-actions">
    <el-button :disabled="!chapters.length" @click="open('event')">新建事件</el-button>
    <el-button type="primary" :disabled="!chapters.length" @click="open('line')">新建剧情线</el-button>
    <el-dialog v-model="visible" :title="mode === 'event' ? '新建事件' : '新建剧情线'" width="min(780px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" :before-close="close">
      <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-error" />
      <el-form v-if="draft" label-position="top" :disabled="saving">
        <template v-if="mode === 'event'">
          <el-form-item label="事件发生章"><el-select v-model="draft.chapter_number" @change="loadSource"><el-option v-for="c in chapters" :key="c.idx" :label="`第 ${c.idx + 1} 章 · ${c.title}`" :value="c.idx + 1" /></el-select></el-form-item>
          <el-form-item label="事件标题 / 主要行动"><el-input v-model="draft.title" maxlength="80" show-word-limit /></el-form-item>
          <el-form-item label="事件内容"><el-input v-model="draft.claim" type="textarea" :rows="3" maxlength="180" show-word-limit placeholder="记录本章发生的一件事" /></el-form-item>
          <el-form-item label="参与人物或原文称呼"><el-input v-model="draft.participants" placeholder="多个称呼用顿号分隔，最多 6 个" /></el-form-item>
          <el-form-item label="依据类型"><el-radio-group v-model="draft.basis"><el-radio value="explicit">原文明示</el-radio><el-radio value="interpretation">解释性判断</el-radio></el-radio-group></el-form-item>
          <el-form-item v-if="draft.basis === 'interpretation'" label="判断视角"><el-input v-model="draft.perspective" maxlength="80" placeholder="说明是谁的判断或叙述视角" /></el-form-item>
          <details class="source"><summary>展开本章原文，选取依据</summary><p v-if="sourceLoading">正在读取原文…</p><p v-else-if="sourceError">{{ sourceError }} <el-button text @click="loadSource">重试</el-button></p><pre v-else ref="sourceRef" @mouseup="captureQuote">{{ source }}</pre></details>
          <el-form-item label="原文依据（4–120 字）"><el-input v-model="draft.quote" type="textarea" :rows="3" maxlength="120" show-word-limit placeholder="粘贴本章原文，或在上方原文中拖选；保存时会核对出处" /></el-form-item>
        </template>
        <template v-else>
          <p class="form-hint">选择已保存事件作为节点。没有合适事件时，可以先取消并新建事件。</p>
          <el-form-item label="剧情线标题"><el-input v-model="draft.title" maxlength="80" show-word-limit /></el-form-item>
          <el-form-item label="类型"><el-radio-group v-model="draft.category"><el-radio value="plot">剧情发展</el-radio><el-radio value="relationship">人物关系</el-radio></el-radio-group></el-form-item>
          <el-form-item label="概述"><el-input v-model="draft.overview" type="textarea" :rows="2" maxlength="300" show-word-limit /></el-form-item>
          <el-form-item label="当前阶段"><el-input v-model="draft.stage" maxlength="100" /></el-form-item>
          <el-form-item label="关联人物或称呼"><el-input v-model="draft.entities" placeholder="用顿号分隔，最多 8 个；留空时使用所选事件的参与人物" /></el-form-item>
          <section v-for="(node, i) in draft.nodes" :key="node.key" class="node-form">
            <header><strong>节点 {{ i + 1 }}</strong><el-button text type="danger" :disabled="draft.nodes.length <= 1" @click="draft.nodes.splice(i, 1)">移除</el-button></header>
            <el-form-item label="章节"><el-select v-model="node.chapter_number" @change="changeNodeChapter(node)"><el-option v-for="c in chapters" :key="c.idx" :label="`第 ${c.idx + 1} 章 · ${c.title}`" :value="c.idx + 1" /></el-select></el-form-item>
            <el-form-item label="事件"><el-select v-model="node.eventId" :loading="eventLoading[node.chapter_number]" placeholder="选择本章事件" @change="selectEvent(node)"><el-option v-for="e in events[node.chapter_number] || []" :key="e.note_id" :label="e.title" :value="e.note_id" /></el-select></el-form-item>
            <p v-if="eventErrors[node.chapter_number]" role="alert">{{ eventErrors[node.chapter_number] }} <el-button text @click="loadEvents(node.chapter_number)">重试</el-button></p>
            <p v-else-if="!eventLoading[node.chapter_number] && !events[node.chapter_number]?.length" class="form-hint">本章暂无事件，请先新建事件或选择其他章节。</p>
            <el-button v-if="nextOffsets[node.chapter_number] != null" text :loading="eventLoading[node.chapter_number]" @click="loadEvents(node.chapter_number, true)">加载更多事件</el-button>
            <p v-if="node.event" class="form-hint">{{ node.event.claim }}</p>
            <el-form-item label="节点结论"><el-input v-model="node.conclusion" type="textarea" :rows="2" maxlength="500" show-word-limit /></el-form-item>
          </section>
          <el-button :disabled="draft.nodes.length >= 40" @click="addNode">添加事件节点</el-button>
          <div class="connect"><el-checkbox v-model="draft.connect">按章节顺序连接节点（表示先后关系）</el-checkbox></div>
        </template>
      </el-form>
      <template #footer><el-button :disabled="saving" @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">{{ mode === 'event' ? '保存事件' : '创建剧情线' }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/request'
import { createReadingNote } from '@/api/storylines'
import { eventPayload, storylinePayload } from '@/utils/manualNotes'
const props = defineProps({ bookId: Number, chapters: { type: Array, default: () => [] }, chapterNumber: Number })
const emit = defineEmits(['created'])
const visible = ref(false), mode = ref('event'), draft = ref(null), error = ref(''), saving = ref(false)
const source = ref(''), sourceRef = ref(null), sourceLoading = ref(false), sourceError = ref('')
const events = ref({}), nextOffsets = ref({}), eventLoading = ref({}), eventErrors = ref({})
let generation = 0, sourceVersion = 0, nodeKey = 0
function initialChapter() { return props.chapters.some(c => c.idx + 1 === props.chapterNumber) ? props.chapterNumber : props.chapters.at(-1)?.idx + 1 }
function open(value) {
  generation++; mode.value = value; error.value = ''; source.value = ''; events.value = {}; nextOffsets.value = {}; eventLoading.value = {}; eventErrors.value = {}
  draft.value = value === 'event' ? { chapter_number: initialChapter(), title: '', claim: '', participants: '', basis: 'explicit', perspective: '', quote: '' }
    : { title: '', category: 'plot', overview: '', stage: '', entities: '', nodes: [], connect: true }
  visible.value = true
  if (value === 'event') loadSource(); else addNode()
}
function close(done) { if (!saving.value) done() }
async function loadSource() {
  const version = ++sourceVersion, current = generation
  source.value = ''; sourceError.value = ''; sourceLoading.value = true; draft.value.quote = ''
  try {
    const r = await request({ url: `/books/${props.bookId}/chapters/${draft.value.chapter_number - 1}`, silent: true })
    if (current === generation && version === sourceVersion) source.value = r.data.content || ''
  } catch { if (current === generation && version === sourceVersion) sourceError.value = '原文读取失败' }
  finally { if (current === generation && version === sourceVersion) sourceLoading.value = false }
}
function captureQuote() {
  const selection = window.getSelection()
  if (!selection?.rangeCount || !sourceRef.value?.contains(selection.anchorNode) || !sourceRef.value?.contains(selection.focusNode)) return
  const quote = selection.toString().trim()
  if (quote.length >= 4 && quote.length <= 120) draft.value.quote = quote
}
async function loadEvents(chapter, more = false) {
  if (eventLoading.value[chapter]) return
  const current = generation
  eventLoading.value[chapter] = true; eventErrors.value[chapter] = ''
  try {
    const r = await request({ url: `/books/${props.bookId}/chapter-events/${chapter}`, params: { offset: more ? nextOffsets.value[chapter] : 0 }, silent: true })
    if (current !== generation) return
    if (!r.data.ok) throw new Error(r.data.data?.message || '事件读取失败')
    events.value[chapter] = more ? [...(events.value[chapter] || []), ...r.data.data.items] : r.data.data.items
    nextOffsets.value[chapter] = r.data.data.next_offset
  } catch (e) { if (current === generation) eventErrors.value[chapter] = e.message || '事件读取失败' }
  finally { if (current === generation) eventLoading.value[chapter] = false }
}
function addNode() {
  const chapter = initialChapter()
  draft.value.nodes.push({ key: ++nodeKey, chapter_number: chapter, eventId: '', event: null, conclusion: '' })
  if (!events.value[chapter]) loadEvents(chapter)
}
function changeNodeChapter(node) { node.eventId = ''; node.event = null; node.conclusion = ''; if (!events.value[node.chapter_number]) loadEvents(node.chapter_number) }
function selectEvent(node) { node.event = events.value[node.chapter_number]?.find(e => e.note_id === node.eventId); node.conclusion = node.event?.claim || '' }
async function save() {
  if (saving.value) return
  error.value = ''
  let payload
  try { payload = mode.value === 'event' ? eventPayload(draft.value) : storylinePayload(draft.value) }
  catch (e) { error.value = e.message; return }
  const current = generation, kind = mode.value
  saving.value = true
  try {
    const data = await createReadingNote(props.bookId, payload)
    if (current !== generation) return
    visible.value = false; ElMessage.success(kind === 'event' ? '事件已保存' : '剧情线已创建')
    emit('created', { kind, data, chapter: payload.chapter_number })
  } catch (e) { if (current === generation) error.value = e.message || '保存失败，请重试' }
  finally { if (current === generation) saving.value = false }
}
onBeforeUnmount(() => { generation++ })
</script>

<style scoped>
.create-actions { display:flex; gap:8px; flex-wrap:wrap; }
.create-actions :deep(.el-select) { width:100%; }
.form-error,.form-hint { margin-bottom:16px; }
.form-hint { color:var(--muted); font-size:12px; line-height:1.8; }
.source { margin-bottom:18px; }
.source summary { color:var(--gold); cursor:pointer; }
.source pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:260px; overflow:auto; padding:16px; background:var(--card-soft); font:14px/1.9 'Songti SC','SimSun',serif; }
.node-form { padding:18px; margin:16px 0; border:1px solid var(--line); border-radius:8px; }
.node-form header { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }
.connect { margin-top:16px; }
</style>
