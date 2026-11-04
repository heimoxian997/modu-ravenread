<template>
  <div class="page storylines-page">
    <header class="story-page-heading">
      <div>
        <nav class="back-links" aria-label="返回导航">
          <el-button text class="back" @click="router.push(readerLocation)">{{ route.query.from === 'reader' ? '← 返回阅读器' : '继续阅读' }}</el-button>
          <el-button text @click="router.push(`/books/${bookId}`)">返回书籍</el-button>
        </nav>
        <h1>剧情线</h1>
        <p class="muted">把分散的章节，连成可以回看的来龙去脉。</p>
      </div>
      <div class="heading-actions"><span v-if="safeChapter" class="safe-boundary">截至第 {{ safeChapter }} 章</span><StorylineCreate :key="bookId" :book-id="bookId" :chapters="eventChapters" :chapter-number="eventChapter" @created="onCreated" /><StorylineDeleted :key="`deleted-${bookId}`" :book-id="bookId" @restored="onRestored" /><el-button :loading="loading" @click="refresh">刷新</el-button></div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="story-workspace">
      <aside class="story-index panel" aria-label="剧情线与章节导航">
        <div class="index-tabs" role="group" aria-label="浏览内容">
          <button :class="{ active: browseMode === 'lines' }" :aria-pressed="browseMode === 'lines'" @click="browseMode = 'lines'">剧情线</button>
          <button :class="{ active: browseMode === 'events' }" :aria-pressed="browseMode === 'events'" @click="browseMode = 'events'">事件</button>
        </div>
        <template v-if="browseMode === 'events'">
          <div class="index-caption"><span>已读正文章节</span><span>{{ eventChapters.length }} 章</span></div>
          <p v-if="chaptersError" class="index-empty">{{ chaptersError }}<el-button text @click="loadEventChapters">重试</el-button></p>
          <p v-else-if="!eventChapters.length" class="index-empty">{{ loading ? '正在读取章节…' : '暂无可查看的正文章节' }}</p>
          <button v-for="chapter in eventChapters" :key="chapter.idx" class="line-option" :class="{ active: eventChapter === chapter.idx + 1 }" :aria-pressed="eventChapter === chapter.idx + 1" @click="eventChapter = chapter.idx + 1">
            <span class="line-option-type">第 {{ chapter.idx + 1 }} 章</span><strong>{{ chapter.title }}</strong>
          </button>
        </template>
        <template v-else>
        <form class="story-search" @submit.prevent="refresh">
          <label for="story-search">查找人物或剧情</label>
          <div><el-input id="story-search" v-model="search" placeholder="人物、物品或事件" clearable /><el-button native-type="submit" :loading="loading" aria-label="搜索剧情线">查找</el-button></div>
        </form>
        <div class="index-caption"><span>伴读整理的线索</span><span>{{ lines.length }} 条{{ hasMore ? '+' : '' }}</span></div>
        <div v-if="loading && !lines.length" class="index-loading"><el-skeleton :rows="5" animated /></div>
        <div v-if="!loading && !lines.length" class="index-empty">
          <h2>{{ error ? '暂时无法读取' : activeSearch ? '没有找到相关剧情线' : '还没有整理好的剧情线' }}</h2>
          <p>{{ error ? '检查后端连接，或先打开阅读器建立阅读进度。' : activeSearch ? '换个人物名或事件关键词，也可以清空搜索查看全部。' : '与伴读讨论剧情后，让它把值得保留的事件串成剧情线，就会显示在这里。' }}</p>
          <el-button v-if="!activeSearch" text @click="router.push(readerLocation)">进入阅读器 →</el-button>
        </div>
        <button v-for="line in lines" :key="line.note_id" type="button" class="line-option" :class="{ active: selectedLineId === line.note_id }"
          :aria-pressed="selectedLineId === line.note_id" @click="selectLine(line.note_id)">
          <span class="line-option-type">{{ line.limited_view ? '已读线索' : line.category === 'relationship' ? '人物关系' : '剧情发展' }}<span v-if="line.needs_revision" class="review-dot">待核实</span></span>
          <strong>{{ line.title }}</strong><span class="line-option-overview">{{ line.overview }}</span>
          <span class="line-option-foot">{{ line.node_count }} 个节点 · {{ chapterRange(line.event_chapters) }}</span>
        </button>
        <el-button v-if="hasMore" class="more-lines" :loading="loading" @click="loadMore">继续浏览</el-button>
        </template>
      </aside>

      <main class="story-detail panel" v-loading="browseMode === 'lines' && detailLoading">
        <ChapterEvents v-if="browseMode === 'events'" :key="eventsVersion" :book-id="bookId" :safe-chapter="safeChapter" :chapter-number="eventChapter" :chapter-title="eventChapters.find(c => c.idx + 1 === eventChapter)?.title" @select-line="selectLine" />
        <template v-else>
        <template v-if="detail">
          <header class="line-heading"><div><span class="line-category">{{ detail.limited_view ? '已读线索' : detail.storyline.category === 'relationship' ? '人物关系' : '剧情发展' }}</span><h2>{{ detail.storyline.title }}</h2></div><el-radio-group v-model="view" size="small" aria-label="展示方式"><el-radio-button value="graph">剧情图</el-radio-button><el-radio-button value="timeline">章节列表</el-radio-button></el-radio-group></header>
          <div v-if="!detail.limited_view" class="line-actions"><StorylineEditor :book-id="bookId" :note-id="selectedLineId" :chapters="eventChapters" @saved="refresh" /><el-button type="danger" plain :loading="deleting" @click="removeLine">删除整条剧情线</el-button></div>
          <p class="line-overview">{{ detail.storyline.overview }}</p>
          <div class="line-summary"><span>{{ detail.storyline.stage }}</span><span>{{ detail.nodes.length }} 个节点 · {{ detail.edges.length }} 条连接</span></div>
          <div class="people"><span v-for="name in detail.storyline.entities" :key="name">{{ name }}</span></div>
          <el-alert v-if="detail.limited_view" class="revision-alert" type="info" :closable="false" show-icon
            title="这条线在后期整理。当前使用中性标题，仅显示已读章节的原文；后期结论、分支命名和因果解释暂不展开。" />
          <el-alert v-if="detail.storyline.needs_revision" class="revision-alert" type="warning" :closable="false" show-icon
            :title="`${detail.storyline.stale_node_count} 个节点的证据有更新或章节范围不一致，相关旧结论和连接已暂时隐藏。`" />
          <div v-if="view === 'graph'" class="graph-section">
            <div class="graph-legend"><span><i class="solid-line" />先后</span><span><i class="clue-line" />线索 / 揭示</span><span><i class="cause-line" />因果</span><span><i class="conflict-line" />反驳</span></div>
            <StorylineGraph :nodes="detail.nodes" :edges="detail.edges" :selected="selectedNodeId" @select="selectNode" />
            <p class="graph-help">按章节排列，箭头表示记录中的联系。点击节点后，下方显示完整结论与原文依据；图内可滚动。</p>
          </div>
          <div v-else class="chapter-events" aria-label="章节事件列表">
            <button v-for="node in detail.nodes" :key="node.node_id" type="button" :class="{ active: selectedNodeId === node.node_id }" @click="selectNode(node.node_id)">
              <span class="event-chapter">第 {{ node.chapter_number }} 章</span><span class="event-text"><small v-if="node.branch">{{ node.branch }}</small>{{ node.conclusion }}</span><span v-if="node.needs_revision" class="review-dot">待核实</span><span v-else class="event-arrow">↗</span>
            </button>
          </div>

          <section v-if="selectedNode" class="node-inspector" aria-label="节点详情与证据">
            <div class="inspector-heading"><h3>第 {{ selectedNode.chapter_number }} 章<span v-if="selectedNode.branch"> · {{ selectedNode.branch }}</span></h3><el-tag v-if="selectedNode.needs_revision" type="warning" size="small">待核实</el-tag></div>
            <p class="selected-conclusion">{{ selectedNode.conclusion }}</p>
            <StorylineNodeMove v-if="!detail.limited_view" :key="`${selectedLineId}:${detail.storyline.version}:${selectedNodeId}`" :book-id="bookId" :note-id="selectedLineId" :version="detail.storyline.version" :node="selectedNode" :node-count="detail.nodes.length" @moved="onMoved" />
            <div v-if="selectedConnections.length" class="node-connections"><h4>与这一步相连</h4><button v-for="connection in selectedConnections" :key="connection.key" type="button" @click="selectNode(connection.other.node_id)"><span>{{ connection.incoming ? '←' : '→' }} {{ relationLabels[connection.relation] }}{{ connection.basis === 'interpretation' ? ' · 推断' : '' }}</span><strong>第 {{ connection.other.chapter_number }} 章 · {{ connection.other.conclusion }}</strong><small v-if="connection.reason">{{ connection.reason }}</small></button></div>
            <div class="evidence-heading"><h4>原文依据</h4><span>{{ selectedNode.evidence.length }} 条证据记录</span></div>
            <el-skeleton v-if="evidenceLoading" :rows="3" animated />
            <el-alert v-if="evidenceError" :title="evidenceError" type="error" :closable="false" />
            <article v-for="note in evidence" :key="`${note.note_id}-${note.version}`" class="evidence-note">
              <header><strong>{{ note.title }}</strong><span>{{ note.basis === 'interpretation' ? '解释性判断' : '原文明示' }} · v{{ note.version }}</span></header>
              <p v-if="note.needs_revision || note.superseded_by_version || note.status !== 'active'" class="evidence-warning">{{ note.needs_revision ? note.scope_error || '记录需要重新核对。' : note.superseded_by_version ? `此记录已有第 ${note.superseded_by_version} 版，当前显示节点引用的历史版本。` : `记录${note.status === 'disputed' ? '存在争议' : '已撤回'}，不能作为确定依据。` }}</p>
              <p>{{ note.claim }}</p><small v-if="note.perspective" class="perspective">判断视角：{{ note.perspective }}</small>
              <blockquote v-for="(quote, index) in note.evidence" :key="index"><span>第 {{ quote.chapter_number }} 章</span>{{ quote.quote }}</blockquote>
              <footer v-if="note.projection === 'evidence_only'">仅展示第 {{ note.chapter_number }} 章原文，不包含后期笔记的判断。</footer>
              <footer v-else>截至第 {{ note.known_by_chapter }} 章可知<span v-if="note.chapter_number"> · 事件发生于第 {{ note.chapter_number }} 章</span><span v-else> · 早期多章记录</span></footer>
            </article>
          </section>
          <section v-if="detail.storyline.open_questions.length" class="open-questions"><h3>尚待解开的线索</h3><ul><li v-for="question in detail.storyline.open_questions" :key="question">{{ question }}</li></ul></section>
        </template>
        <div v-else class="detail-placeholder"><span class="thread-symbol" aria-hidden="true">⌁</span><h2>{{ detailError ? '这条线暂时无法展开' : '选择一条线，回看它如何展开' }}</h2><p>{{ detailError || '每一步都有章节，每个判断都可以回到证据。' }}</p><el-button v-if="detailError && selectedLineId" @click="selectLine(selectedLineId)">重新加载</el-button></div>
        </template>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StorylineGraph from '@/components/StorylineGraph.vue'
import ChapterEvents from '@/components/ChapterEvents.vue'
import StorylineEditor from '@/components/StorylineEditor.vue'
import StorylineCreate from '@/components/StorylineCreate.vue'
import StorylineNodeMove from '@/components/StorylineNodeMove.vue'
import StorylineDeleted from '@/components/StorylineDeleted.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getStorylineEvidence, getStoryline, listStorylines, deleteStoryline } from '@/api/storylines'
import request from '@/utils/request'
import { relationLabels } from '@/utils/storylineGraph'

const route = useRoute()
const router = useRouter()
const bookId = computed(() => Number(route.params.id))
const readerLocation = computed(() => {
  const chapter = route.query.chapter
  const idx = typeof chapter === 'string' && /^\d+$/.test(chapter) && Number.isSafeInteger(Number(chapter)) ? chapter : undefined
  return { name: 'book-reader', params: { id: bookId.value, ...(route.query.from === 'reader' && idx !== undefined ? { idx } : {}) } }
})
const search = ref(''), activeSearch = ref(''), lines = ref([]), cursor = ref(null), hasMore = ref(false), safeChapter = ref(null)
const browseMode = ref('lines'), eventChapters = ref([]), eventChapter = ref(null), chaptersError = ref('')
const eventsVersion = ref(0), deleting = ref(false)
let chapterGeneration = 0
const loading = ref(false), detailLoading = ref(false), evidenceLoading = ref(false)
const error = ref(''), detailError = ref(''), evidenceError = ref('')
const selectedLineId = ref(''), selectedNodeId = ref(''), detail = ref(null), evidence = ref([]), view = ref('graph')
let listController, detailController, evidenceController
let disposed = false
let refreshVersion = 0
const selectedNode = computed(() => detail.value?.nodes.find(node => node.node_id === selectedNodeId.value))
const selectedConnections = computed(() => {
  if (!detail.value || !selectedNode.value) return []
  const nodes = new Map(detail.value.nodes.map(node => [node.node_id, node]))
  return detail.value.edges.filter(edge => edge.source === selectedNodeId.value || edge.target === selectedNodeId.value).flatMap(edge => {
    const incoming = edge.target === selectedNodeId.value
    const other = nodes.get(incoming ? edge.source : edge.target)
    return other ? [{ ...edge, incoming, other, key: `${edge.source}-${edge.target}-${edge.relation}` }] : []
  })
})
const chapterRange = chapters => !chapters?.length ? '章节未标注' : Math.min(...chapters) === Math.max(...chapters) ? `第 ${chapters[0]} 章` : `第 ${Math.min(...chapters)}–${Math.max(...chapters)} 章`
const canceled = err => err.code === 'ERR_CANCELED' || err.name === 'CanceledError'

function clearDetail() {
  detailController?.abort(); evidenceController?.abort()
  detailLoading.value = false; evidenceLoading.value = false
  detail.value = null; evidence.value = []; selectedNodeId.value = ''; detailError.value = ''; evidenceError.value = ''
}

async function onCreated({ kind, data, chapter }) {
  if (kind === 'event') {
    eventsVersion.value++; eventChapter.value = chapter; browseMode.value = 'events'
  } else {
    search.value = ''; await refresh(); await selectLine(data.note_id)
  }
}

async function onRestored({ note_id }) {
  search.value = ''; await refresh(); await selectLine(note_id); eventsVersion.value++
}

async function onMoved({ target_note_id, node_id }) {
  search.value = ''; await refresh(); await selectLine(target_note_id); await selectNode(node_id); eventsVersion.value++
}

async function removeLine() {
  if (deleting.value || !detail.value || detail.value.limited_view) return
  const id = selectedLineId.value, version = detail.value.storyline.version
  try {
    await ElMessageBox.confirm(`删除“${detail.value.storyline.title}”及其全部节点和连接？原始事件与原文依据会保留，之后可在“已删除”中恢复。`, '删除整条剧情线', { type: 'warning', confirmButtonText: '删除剧情线', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' })
  } catch { return }
  deleting.value = true
  try {
    await deleteStoryline(bookId.value, id, version)
    ElMessage.success('剧情线已删除，可在“已删除”中恢复')
    await refresh(); eventsVersion.value++
  } catch (e) { ElMessage.error(e.message || '删除失败，请刷新后重试') }
  finally { deleting.value = false }
}

async function refresh() {
  const version = ++refreshVersion
  const previous = selectedLineId.value
  listController?.abort()
  clearDetail()
  selectedLineId.value = ''; lines.value = []; cursor.value = null; hasMore.value = false; safeChapter.value = null
  activeSearch.value = search.value.trim()
  await loadLines(false)
  if (!disposed && version === refreshVersion && lines.value.length) await selectLine(lines.value.some(line => line.note_id === previous) ? previous : lines.value[0].note_id, false)
}

async function loadLines(append) {
  const controller = new AbortController(); listController = controller
  loading.value = true; error.value = ''
  try {
    const result = await listStorylines(bookId.value, { ...(activeSearch.value ? { query: activeSearch.value } : {}), ...(append && cursor.value ? { cursor: cursor.value } : {}) }, controller.signal)
    if (controller.signal.aborted || disposed) return
    lines.value = append ? [...lines.value, ...result.items] : result.items
    cursor.value = result.cursor; hasMore.value = result.has_more; safeChapter.value = result.safe_chapter
  } catch (err) {
    if (canceled(err) || controller.signal.aborted || disposed) return
    error.value = err.message
    lines.value = []; hasMore.value = false; safeChapter.value = null; clearDetail(); selectedLineId.value = ''
  } finally { if (listController === controller) loading.value = false }
}
async function loadMore() { if (!loading.value) await loadLines(true) }

async function selectLine(id, activate = true) {
  if (activate) browseMode.value = 'lines'
  clearDetail(); selectedLineId.value = id; detailLoading.value = true
  const controller = new AbortController(); detailController = controller
  try {
    const result = await getStoryline(bookId.value, id, controller.signal)
    if (controller.signal.aborted || disposed) return
    if (safeChapter.value !== null && safeChapter.value !== result.safe_chapter) {
      await refresh(); return
    }
    detail.value = result; safeChapter.value = result.safe_chapter; detailLoading.value = false
    if (result.nodes.length) await selectNode(result.nodes[0].node_id)
  } catch (err) {
    if (!canceled(err) && !controller.signal.aborted && !disposed) detailError.value = err.message
  } finally { if (detailController === controller) detailLoading.value = false }
}

async function selectNode(id) {
  evidenceController?.abort(); selectedNodeId.value = id; evidence.value = []; evidenceError.value = ''; evidenceLoading.value = false
  const node = selectedNode.value
  if (!node) return
  const controller = new AbortController(); evidenceController = controller; evidenceLoading.value = true
  try {
    const result = await getStorylineEvidence(bookId.value, selectedLineId.value, id, controller.signal)
    if (controller.signal.aborted || disposed) return
    if (result.safe_chapter !== safeChapter.value) { await refresh(); return }
    if (!result.items.length) throw new Error('有证据已不在当前可见范围，请刷新剧情线。')
    evidence.value = result.items
  } catch (err) {
    if (!canceled(err) && !controller.signal.aborted && !disposed) evidenceError.value = err.message
  } finally { if (evidenceController === controller) evidenceLoading.value = false }
}

async function loadEventChapters() {
  const version = ++chapterGeneration
  eventChapters.value = []; chaptersError.value = ''
  if (!safeChapter.value) return
  try {
    const r = await request({ url: `/books/${bookId.value}/chapters`, silent: true })
    if (version !== chapterGeneration || disposed) return
    eventChapters.value = r.data.filter(c => c.kind === 'main' && c.idx < safeChapter.value)
    const preferred = eventChapter.value || Number(route.query.chapter) + 1
    eventChapter.value = eventChapters.value.some(c => c.idx + 1 === preferred) ? preferred : eventChapters.value.at(-1)?.idx + 1 || null
  } catch { if (version === chapterGeneration) chaptersError.value = '章节加载失败' }
}
watch(safeChapter, loadEventChapters)
watch(bookId, () => { chapterGeneration++; eventChapter.value = null; eventChapters.value = [] })

function onVisible() { if (!document.hidden) refresh() }
watch(bookId, refresh)
onMounted(() => { refresh(); window.addEventListener('focus', onVisible); document.addEventListener('visibilitychange', onVisible) })
onBeforeUnmount(() => { disposed = true; listController?.abort(); clearDetail(); window.removeEventListener('focus', onVisible); document.removeEventListener('visibilitychange', onVisible) })
</script>

<style scoped>
.storylines-page { --read-serif: 'Noto Serif SC', 'Songti SC', 'STSong', 'SimSun', serif; max-width: 1600px; }
.story-page-heading { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 24px; }
.back { margin-left: -12px; }
.back-links { display: flex; align-items: center; flex-wrap: wrap; }
h1, h2, h3, p { margin: 0; }
h1 { margin: 12px 0 10px; font-family: var(--read-serif); font-size: 34px; font-weight: 500; letter-spacing: .08em; }
.story-page-heading p { font-size: 13px; line-height: 1.8; }
.heading-actions { display: flex; align-items: center; gap: 16px; flex-wrap:wrap; }
.line-actions { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.safe-boundary { color: var(--gold); font-size: 12px; white-space: nowrap; }
.story-workspace { display: grid; grid-template-columns: 270px minmax(0,1fr); gap: 22px; margin-top: 20px; align-items: start; }
.index-tabs { display:flex; padding:8px; gap:6px; border-bottom:1px solid var(--line-soft); }
.index-tabs button { flex:1; padding:11px; border:0; border-radius:7px; background:transparent; color:var(--muted); cursor:pointer; font:inherit; }
.index-tabs button.active { background:var(--bg); color:var(--gold); box-shadow:inset 0 -2px var(--gold); }
.index-tabs button:focus-visible,.line-option:focus-visible { outline:2px solid var(--gold); outline-offset:-3px; }
.story-index { position: sticky; top: 24px; padding: 0; max-height: calc(100vh - 48px); overflow: auto; }
.story-search { padding: 20px 18px; border-bottom: 1px solid var(--line-soft); }
.story-search label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.story-search > div { display: flex; gap: 6px; }
.story-search :deep(.el-input) { min-width: 0; }
.story-search :deep(.el-button) { padding: 8px 10px; }
.index-caption { display: flex; justify-content: space-between; gap: 8px; padding: 17px 18px 8px; color: var(--muted); font-size: 11px; }
.index-loading { padding: 20px; }
.line-option { display: flex; flex-direction: column; gap: 10px; width: 100%; padding: 21px 18px; background: transparent; color: var(--text); border: 0; border-bottom: 1px solid var(--line-soft); text-align: left; cursor: pointer; }
.line-option:hover { background: var(--surface-hover); }
.line-option.active { background: var(--green-soft-bg); box-shadow: inset 3px 0 0 var(--gold); }
.line-option:focus-visible, .chapter-events button:focus-visible, .node-connections button:focus-visible { outline: 2px solid var(--gold); outline-offset: -3px; }
.line-option-type { display: flex; justify-content: space-between; color: var(--green-soft); font-size: 11px; }
.line-option strong { font-family: var(--read-serif); font-weight: 500; font-size: 16px; line-height: 1.65; }
.line-option-overview { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; color: var(--muted); font-size: 12px; line-height: 1.8; }
.line-option-foot { font-size: 10px; color: var(--muted); }
.review-dot { color: var(--gold); font-size: 11px; }
.more-lines { margin: 16px 18px; width: calc(100% - 36px); }
.index-empty { padding: 30px 20px; }
.index-empty h2 { font-size: 15px; font-weight: 500; margin-bottom: 12px; }
.index-empty p { color: var(--muted); font-size: 12px; line-height: 1.9; margin-bottom: 15px; }
.story-detail { padding: 28px; min-width: 0; }
.line-heading { display: flex; justify-content: space-between; align-items: start; gap: 20px; }
.line-category { color: var(--gold); font-size: 11px; letter-spacing: .15em; }
.line-heading h2 { margin-top: 10px; font-family: var(--read-serif); font-weight: 500; font-size: 24px; line-height: 1.6; }
.line-heading :deep(.el-radio-group) { flex-shrink: 0; margin-top: 4px; }
.line-overview { margin-top: 18px; font-size: 13px; line-height: 1.9; color: var(--muted); white-space: pre-wrap; }
.line-summary { display: flex; flex-wrap: wrap; gap: 10px 24px; margin-top: 16px; color: var(--muted); font-size: 11px; line-height: 1.8; }
.people { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 24px; }
.people span { border: 1px solid var(--line-soft); border-radius: 4px; padding: 4px 9px; font-size: 11px; color: var(--muted); }
.revision-alert { margin-bottom: 18px; }
.graph-legend { display: flex; flex-wrap: wrap; gap: 12px 20px; color: var(--muted); font-size: 10px; margin-bottom: 14px; }
.graph-legend > span { display: flex; gap: 6px; align-items: center; }
.graph-legend i { display: block; width: 20px; border-top: 1px solid var(--muted); }
.graph-legend .clue-line { border-color: var(--gold); border-top-style: dashed; }
.graph-legend .cause-line { border-color: var(--teal); }
.graph-legend .conflict-line { border-color: #c98b81; border-top-style: dashed; }
.graph-help { margin-top: 12px; color: var(--muted); font-size: 11px; line-height: 1.8; }
.chapter-events { border-top: 1px solid var(--line-soft); }
.chapter-events > button { width: 100%; display: flex; align-items: baseline; gap: 18px; padding: 20px 12px; background: transparent; border: 0; border-bottom: 1px solid var(--line-soft); color: var(--text); text-align: left; cursor: pointer; }
.chapter-events > button.active { background: var(--gold-soft-bg); }
.event-chapter { color: var(--gold); font-size: 12px; white-space: nowrap; }
.event-text { flex: 1; font-size: 13px; line-height: 1.9; }
.event-text small { display: block; color: var(--muted); font-size: 11px; }
.event-arrow { color: var(--muted); }
.node-inspector { margin-top: 30px; padding-top: 24px; border-top: 1px solid var(--line); }
.inspector-heading { display: flex; gap: 14px; align-items: center; }
.inspector-heading h3 { color: var(--gold); font-weight: 500; font-family: var(--read-serif); font-size: 19px; }
.selected-conclusion { margin: 14px 0 22px; font-size: 14px; line-height: 2; white-space: pre-wrap; }
h4 { margin: 0 0 12px; font-size: 12px; font-weight: 500; color: var(--muted); }
.node-connections { margin-bottom: 24px; }
.node-connections button { display: flex; flex-direction: column; gap: 6px; width: 100%; padding: 12px 14px; margin: 6px 0; color: var(--text); background: var(--card-soft); border: 1px solid var(--line-soft); border-radius: 6px; text-align: left; cursor: pointer; }
.node-connections button > span, .node-connections small { font-size: 11px; color: var(--muted); line-height: 1.8; }
.node-connections strong { font-size: 12px; font-weight: 400; line-height: 1.9; }
.evidence-heading { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.evidence-heading h4 { margin: 0; }
.evidence-heading > span { color: var(--muted); font-size: 11px; }
.evidence-note { padding: 18px; background: var(--card-soft); border: 1px solid var(--line-soft); border-radius: 8px; margin-top: 12px; }
.evidence-note header { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; }
.evidence-note header strong { font-size: 13px; font-weight: 500; }
.evidence-note header > span, .evidence-note footer, .perspective { font-size: 10px; color: var(--muted); line-height: 1.8; }
.evidence-note > p { margin: 12px 0; font-size: 12px; line-height: 1.9; white-space: pre-wrap; }
.evidence-note .evidence-warning { color: var(--gold); }
.evidence-note blockquote { margin: 14px 0; padding: 10px 14px; border-left: 2px solid var(--gold-dim); background: var(--gold-soft-bg); font-family: var(--read-serif); font-size: 14px; line-height: 2; white-space: pre-wrap; }
.evidence-note blockquote > span { display: block; font: 10px/1.8 sans-serif; color: var(--gold); }
.open-questions { border-top: 1px solid var(--line-soft); padding-top: 22px; margin-top: 26px; }
.open-questions h3 { font-size: 14px; font-weight: 500; }
.open-questions li { font-size: 12px; color: var(--muted); line-height: 2; }
.detail-placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 420px; text-align: center; padding: 30px; }
.thread-symbol { color: var(--gold); font: 64px var(--read-serif); margin-bottom: 18px; }
.detail-placeholder h2 { font-family: var(--read-serif); font-size: 22px; font-weight: 400; line-height: 1.8; }
.detail-placeholder p { font-size: 12px; color: var(--muted); margin: 14px 0; line-height: 1.9; }
@media (max-width: 1100px) { .story-workspace { grid-template-columns: 220px minmax(0,1fr); gap: 16px; } .story-detail { padding: 20px; } .line-heading { flex-direction: column; gap: 12px; } }
@media (max-width: 800px) { .story-workspace { grid-template-columns: minmax(0,1fr); } .story-index { position: static; max-height: 400px; } .story-page-heading { align-items: start; } .heading-actions { flex-direction: column; align-items: end; gap: 12px; padding-top: 12px; } .story-detail { padding: 18px; } h1 { font-size: 28px; } .line-heading h2 { font-size: 21px; } .chapter-events > button { gap: 12px; padding-inline: 0; } }
</style>
