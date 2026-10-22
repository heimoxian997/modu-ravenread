<template>
  <div class="reader" :class="theme">
    <aside class="side" :class="{ folded: !sideOpen }">
      <div class="side-head">
        <button class="icon-btn" type="button" title="返回书籍" @click="backToBook">
          <el-icon><ArrowLeft /></el-icon>
        </button>
        <div class="side-meta">
          <strong>{{ book?.title || '正在阅读' }}</strong>
          <span>{{ chapters.length }} 章</span>
        </div>
      </div>
      <div class="chapter-list">
        <button
          v-for="item in chapters"
          :key="item.idx"
          type="button"
          class="chapter-item"
          :class="{ active: item.idx === chapterIdx }"
          @click="openChapter(item.idx)"
        >
          <span class="num">{{ item.idx + 1 }}</span>
          <span class="name">{{ item.title }}</span>
          <span v-if="item.kind === 'extra'" class="tag">番外</span>
        </button>
      </div>
    </aside>

    <main class="stage">
      <header class="topbar">
        <button class="icon-btn" type="button" title="目录" @click="sideOpen = !sideOpen">
          <el-icon><Fold /></el-icon>
        </button>
        <div class="crumb">{{ current?.title || '加载中' }}</div>
        <div class="tools">
          <button
            v-if="agentAvailable"
            class="tool-btn agent-entry"
            type="button"
            :class="{ active: agentOpen }"
            title="伴读"
            @click="toggleAgent"
          >
            <el-icon><ChatDotRound /></el-icon>
            <span class="agent-entry-label">伴读</span>
          </button>
          <button
            class="tool-btn"
            type="button"
            :disabled="fontIndex === 0"
            @click="stepFont(-1)"
          >
            A-
          </button>
          <button
            class="tool-btn"
            type="button"
            :disabled="fontIndex === FONTS.length - 1"
            @click="stepFont(1)"
          >
            A+
          </button>
          <button class="tool-btn" type="button" @click="toggleTheme">
            {{ theme === 'night' ? '日间' : '夜间' }}
          </button>
        </div>
      </header>

      <div ref="scrollRef" class="scroll" @scroll.passive="onScroll">
        <article class="paper" :style="{ fontSize: fontPx + 'px' }">
          <h1 class="chapter-title">{{ current?.title }}</h1>
          <p
            v-for="item in paragraphs"
            :key="item.offset"
            class="para"
            :data-offset="item.offset"
          >
            {{ item.text }}
          </p>
          <p v-if="!loading && !paragraphs.length" class="empty">本章没有正文。</p>
          <div class="chapter-end">本章完</div>
        </article>
      </div>

      <footer class="pager">
        <button
          class="pager-btn"
          type="button"
          :disabled="chapterIdx <= 0"
          @click="openChapter(chapterIdx - 1)"
        >
          上一章
        </button>
        <span class="pager-info">
          {{ chapters.length ? chapterIdx + 1 : 0 }} / {{ chapters.length }}
          <em v-if="percent">· {{ percent }}%</em>
        </span>
        <button
          class="pager-btn"
          type="button"
          :disabled="!chapters.length || chapterIdx >= chapters.length - 1"
          @click="openChapter(chapterIdx + 1)"
        >
          下一章
        </button>
      </footer>
    </main>

    <aside v-if="agentOpen" class="agent-panel" aria-label="伴读">
      <header class="agent-head">
        <div class="agent-title">
          <strong>伴读</strong>
          <span>第 {{ chapterIdx + 1 }} 章 · 截至本章</span>
        </div>
        <div class="agent-head-actions">
          <button type="button" :disabled="agentLoading" @click="clearAgent">清空</button>
          <button type="button" aria-label="关闭" @click="agentOpen = false">×</button>
        </div>
      </header>

      <div ref="agentScrollRef" class="agent-messages">
        <div v-if="!agentMessages.length" class="agent-welcome">
          <p>可以问这一段的原文、人物关系或前情。回答只使用你读到的内容。</p>
          <div class="agent-quick">
            <button type="button" @click="askQuick('这一段讲了什么？')">这一段</button>
            <button type="button" @click="askQuick('这一章开头讲了什么？')">本章开头</button>
            <button type="button" @click="askQuick('前情是什么？')">前情</button>
          </div>
        </div>

        <div
          v-for="(msg, index) in agentMessages"
          :key="index"
          class="agent-msg"
          :class="msg.role"
        >
          <div class="agent-bubble">{{ msg.content }}</div>
          <div
            v-if="msg.role === 'assistant' && msg.meta?.tool_trace?.length"
            class="agent-trace"
          >
            <details>
              <summary>查了 {{ msg.meta.tool_trace.length }} 个工具</summary>
              <div
                v-for="(tool, toolIndex) in msg.meta.tool_trace"
                :key="toolIndex"
                class="agent-tool"
              >
                <span>{{ tool.tool }}</span>
                <em>{{ tool.summary }}</em>
                <b v-if="!tool.ok">失败</b>
              </div>
            </details>
          </div>
          <div
            v-if="msg.role === 'assistant' && msg.meta?.citations?.length"
            class="agent-cites"
          >
            <button
              v-for="(cite, citeIndex) in msg.meta.citations"
              :key="citeIndex"
              type="button"
              class="agent-cite"
              @click="jumpCitation(cite)"
            >
              第{{ cite.chapter_idx + 1 }}章
            </button>
            <span
              v-if="msg.meta.citation_verified === false"
              class="agent-cite-warn"
            >
              引用未校验
            </span>
          </div>
          <div
            v-if="msg.role === 'assistant' && msg.meta?.budget_exhausted"
            class="agent-budget"
          >
            工具预算用完，已基于查到的结果回答
          </div>
        </div>

        <div v-if="agentLoading" class="agent-msg assistant">
          <div class="agent-bubble agent-loading">正在查这一章…</div>
        </div>
      </div>

      <div class="agent-compose">
        <textarea
          v-model="agentInput"
          rows="2"
          maxlength="2000"
          placeholder="问这一段、人物或前情…"
          @keydown.enter.exact.prevent="sendAgent()"
        />
        <div class="agent-compose-foot">
          <span>Enter 发送 · Shift+Enter 换行</span>
          <button
            class="agent-send"
            type="button"
            :disabled="agentLoading || !agentInput.trim()"
            @click="sendAgent()"
          >
            发送
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, ChatDotRound, Fold } from '@element-plus/icons-vue'
import { getBookApi, getChapterApi, listChaptersApi } from '@/api/book'
import {
  chatAgentApi,
  clearAgentSessionApi,
  getAgentCapabilitiesApi,
  getAgentSessionApi
} from '@/api/agent'

const FONTS = [16, 17, 18, 19, 21, 23]
const THEME_KEY = 'modu.reader.theme'
const FONT_KEY = 'modu.reader.font'
const progressKey = (id) => `modu.reader.progress.${id}`

const route = useRoute()
const router = useRouter()
const bookId = computed(() => Number(route.params.id))

const book = ref(null)
const chapters = ref([])
const current = ref(null)
const loading = ref(false)
const sideOpen = ref(true)
const theme = ref('paper')
const fontIndex = ref(2)
const percent = ref(0)
const scrollRef = ref(null)
const agentOpen = ref(false)
const agentAvailable = ref(false)
const agentInput = ref('')
const agentMessages = ref([])
const agentLoading = ref(false)
const agentSessionId = ref(null)
const agentScrollRef = ref(null)

const fontPx = computed(() => FONTS[fontIndex.value])
const chapterIdx = computed(() => current.value?.idx ?? 0)

const paragraphs = computed(() =>
  (current.value?.paragraphs || []).map((item) => ({
    offset: item.offset,
    text: item.text.replace(/^[\s\u3000]+/, '')
  }))
)

function readStored(key, fallback) {
  try {
    const value = localStorage.getItem(key)
    return value === null ? fallback : value
  } catch {
    return fallback
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, String(value))
  } catch {
    /* 隐私模式写入失败不影响阅读 */
  }
}

function savedProgress() {
  try {
    const raw = localStorage.getItem(progressKey(bookId.value))
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function saveProgress() {
  const node = scrollRef.value
  if (!node || !current.value) return
  store(
    progressKey(bookId.value),
    JSON.stringify({
      chapterIdx: current.value.idx,
      offset: pickTopOffset(),
      percent: percent.value,
      at: Date.now()
    })
  )
}

function pickTopOffset() {
  const node = scrollRef.value
  if (!node) return current.value?.char_start ?? 0
  const line = node.getBoundingClientRect().top + 72
  const items = node.querySelectorAll('.para')
  let offset = current.value?.char_start ?? 0
  for (const item of items) {
    if (item.getBoundingClientRect().top <= line) {
      offset = Number(item.dataset.offset || offset)
    } else {
      break
    }
  }
  return offset
}

function onScroll() {
  updatePercent()
  if (scrollTimer) return
  scrollTimer = setTimeout(() => {
    scrollTimer = null
    saveProgress()
  }, 400)
}

let scrollTimer = null
let loadingIndex = null

function stepFont(delta) {
  const next = Math.min(FONTS.length - 1, Math.max(0, fontIndex.value + delta))
  if (next === fontIndex.value) return
  fontIndex.value = next
  store(FONT_KEY, next)
}

function toggleTheme() {
  theme.value = theme.value === 'night' ? 'paper' : 'night'
  store(THEME_KEY, theme.value)
}

function backToBook() {
  router.push(`/books/${bookId.value}`)
}

function openChapter(idx) {
  if (!chapters.value.length) return
  const next = Math.min(chapters.value.length - 1, Math.max(0, idx))
  if (next === current.value?.idx) return
  router.replace({ name: 'book-reader', params: { id: bookId.value, idx: next } })
}

async function loadChapter(idx, restoreOffset = 0) {
  loadingIndex = idx
  loading.value = true
  try {
    const response = await getChapterApi(bookId.value, idx)
    current.value = response.data
  } finally {
    loading.value = false
    loadingIndex = null
  }
  await nextTick()
  await new Promise((resolve) => requestAnimationFrame(resolve))
  if (restoreOffset) {
    scrollToOffset(restoreOffset)
  } else {
    scrollRef.value?.scrollTo({ top: 0 })
    percent.value = 0
  }
  updatePercent()
}

function updatePercent() {
  const node = scrollRef.value
  if (!node) return
  const total = node.scrollHeight - node.clientHeight
  percent.value =
    total > 0 ? Math.min(100, Math.round((node.scrollTop / total) * 100)) : 100
}

function scrollToOffset(offset) {
  const node = scrollRef.value
  if (!node || !offset) return
  let target = null
  for (const item of node.querySelectorAll('.para')) {
    if (Number(item.dataset.offset || 0) <= offset) {
      target = item
    } else {
      break
    }
  }
  if (!target) return
  const top =
    target.getBoundingClientRect().top -
    node.getBoundingClientRect().top +
    node.scrollTop
  node.scrollTop = Math.max(0, top - 16)
}

async function loadAgentAvailability() {
  try {
    const response = await getAgentCapabilitiesApi(bookId.value)
    agentAvailable.value = Boolean(response.data?.enabled)
  } catch {
    agentAvailable.value = false
  }
  if (!agentAvailable.value) agentOpen.value = false
}

async function toggleAgent() {
  if (!agentAvailable.value) return
  agentOpen.value = !agentOpen.value
  if (agentOpen.value) {
    await loadAgentSession()
    await scrollAgent()
  }
}

async function loadAgentSession() {
  try {
    const response = await getAgentSessionApi(bookId.value)
    const data = response.data || {}
    agentSessionId.value = data.session_id || null
    agentMessages.value = (data.messages || [])
      .filter((item) => item.role === 'user' || item.role === 'assistant')
      .map((item) => ({
        role: item.role,
        content: item.content,
        meta: item.meta || {}
      }))
  } catch {
    agentSessionId.value = null
    agentMessages.value = []
  }
}

async function scrollAgent() {
  await nextTick()
  const node = agentScrollRef.value
  if (node) node.scrollTop = node.scrollHeight
}

async function sendAgent(textOverride) {
  if (agentLoading.value) return
  const text = String(textOverride ?? agentInput.value).trim()
  if (!text) return
  agentInput.value = ''
  agentMessages.value.push({ role: 'user', content: text })
  agentLoading.value = true
  await scrollAgent()
  try {
    const response = await chatAgentApi(bookId.value, {
      message: text,
      chapter_idx: chapterIdx.value,
      char_offset: pickTopOffset(),
      session_id: agentSessionId.value || undefined
    })
    const data = response.data || {}
    agentSessionId.value = data.session_id || agentSessionId.value
    if (data.reply) {
      agentMessages.value.push({
        role: 'assistant',
        content: data.reply,
        meta: {
          citations: data.citations || [],
          tool_trace: data.tool_trace || [],
          citation_verified: data.citation_verified,
          budget_exhausted: data.budget_exhausted,
          error: data.error
        }
      })
    } else {
      ElMessage.error(data.error || '伴读没有返回内容')
    }
  } catch (error) {
    ElMessage.error(error?.message || '伴读请求失败')
  } finally {
    agentLoading.value = false
    await scrollAgent()
  }
}

async function askQuick(text) {
  if (!agentAvailable.value) return
  if (!agentOpen.value) {
    agentOpen.value = true
    await loadAgentSession()
  }
  await sendAgent(text)
}

async function clearAgent() {
  if (agentLoading.value) return
  try {
    await clearAgentSessionApi(bookId.value)
    agentMessages.value = []
    agentSessionId.value = null
  } catch (error) {
    ElMessage.error(error?.message || '清空会话失败')
  }
}

function jumpCitation(citation) {
  const idx = Number(citation?.chapter_idx)
  if (!Number.isInteger(idx) || idx < 0) return
  if (idx === chapterIdx.value) return
  openChapter(idx)
}

onMounted(async () => {
  theme.value = readStored(THEME_KEY, 'paper') === 'night' ? 'night' : 'paper'
  const storedFont = Number(readStored(FONT_KEY, 2))
  fontIndex.value = Number.isInteger(storedFont)
    ? Math.min(FONTS.length - 1, Math.max(0, storedFont))
    : 2

  const [bookResponse, chapterResponse] = await Promise.all([
    getBookApi(bookId.value),
    listChaptersApi(bookId.value)
  ])
  book.value = bookResponse.data
  chapters.value = chapterResponse.data || []

  const progress = savedProgress()
  const fromRoute = route.params.idx === undefined ? null : Number(route.params.idx)
  const start =
    fromRoute !== null && Number.isInteger(fromRoute)
      ? fromRoute
      : progress?.chapterIdx ?? 0
  const idx = Math.min(chapters.value.length - 1, Math.max(0, start))
  if (idx !== Number(route.params.idx)) {
    router.replace({ name: 'book-reader', params: { id: bookId.value, idx } })
  }
  const restore =
    progress && progress.chapterIdx === idx ? Number(progress.offset || 0) : 0
  await loadChapter(idx, restore)
  await loadAgentAvailability()
})

watch(
  () => route.params.idx,
  (value) => {
    if (value === undefined) return
    const idx = Number(value)
    if (!Number.isInteger(idx)) return
    if (idx === loadingIndex || idx === current.value?.idx) return
    loadChapter(idx)
  }
)

watch(bookId, () => {
  agentOpen.value = false
  void loadAgentAvailability()
})

onBeforeUnmount(() => {
  if (scrollTimer) clearTimeout(scrollTimer)
  saveProgress()
})
</script>

<style scoped>
.reader {
  display: flex;
  height: 100vh;
  overflow: hidden;
  font-family: var(--read-serif);
}

.reader.paper {
  --read-bg: #f3ede1;
  --read-panel: #ece5d6;
  --read-ink: #2c2b26;
  --read-soft: #8b8577;
  --read-line: rgba(44, 43, 38, 0.12);
  --read-serif: 'Noto Serif SC', 'Songti SC', 'STSong', 'SimSun', serif;
  background: var(--read-bg);
  color: var(--read-ink);
}

.reader.night {
  --read-bg: #14171b;
  --read-panel: #1a1e23;
  --read-ink: #c8c3b5;
  --read-soft: #71756f;
  --read-line: rgba(200, 195, 181, 0.14);
  --read-serif: 'Noto Serif SC', 'Songti SC', 'STSong', 'SimSun', serif;
  background: var(--read-bg);
  color: var(--read-ink);
}

.side {
  display: flex;
  flex-direction: column;
  width: 268px;
  flex: 0 0 268px;
  background: var(--read-panel);
  border-right: 1px solid var(--read-line);
  transition: margin-left 0.22s ease;
}

.side.folded {
  margin-left: -268px;
}

.side-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 16px;
  border-bottom: 1px solid var(--read-line);
}

.side-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.side-meta strong {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.side-meta span {
  font-size: 12px;
  color: var(--read-soft);
}

.chapter-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.chapter-item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.chapter-item:hover {
  background: var(--read-line);
}

.chapter-item.active {
  background: var(--read-line);
  font-weight: 500;
}

.chapter-item .num {
  flex: 0 0 26px;
  color: var(--read-soft);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.chapter-item .name {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.chapter-item .tag {
  font-size: 11px;
  color: var(--read-soft);
}

.stage {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 56px;
  padding: 0 20px;
  border-bottom: 1px solid var(--read-line);
}

.crumb {
  flex: 1;
  font-size: 13px;
  color: var(--read-soft);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tools {
  display: flex;
  gap: 6px;
}

.icon-btn,
.tool-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--read-line);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}

.icon-btn {
  width: 30px;
  height: 30px;
}

.tool-btn {
  height: 30px;
  padding: 0 10px;
}

.icon-btn:hover,
.tool-btn:hover {
  background: var(--read-line);
}

.tool-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.scroll {
  flex: 1;
  overflow-y: auto;
}

.paper {
  max-width: 760px;
  margin: 0 auto;
  padding: 52px 40px 96px;
  line-height: 1.95;
}

.chapter-title {
  margin: 0 0 2em;
  font-size: 1.5em;
  font-weight: 500;
  text-align: center;
  letter-spacing: 0.04em;
}

.para {
  margin: 0 0 0.85em;
  text-indent: 2em;
  text-align: justify;
  letter-spacing: 0.01em;
  overflow-wrap: break-word;
}

.empty {
  color: var(--read-soft);
  text-align: center;
}

.chapter-end {
  margin-top: 3em;
  color: var(--read-soft);
  font-size: 0.8em;
  text-align: center;
  letter-spacing: 0.3em;
}

.pager {
  display: flex;
  align-items: center;
  gap: 16px;
  height: 60px;
  padding: 0 24px;
  border-top: 1px solid var(--read-line);
}

.pager-btn {
  height: 32px;
  padding: 0 16px;
  border: 1px solid var(--read-line);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}

.pager-btn:hover:not(:disabled) {
  background: var(--read-line);
}

.pager-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.pager-info {
  flex: 1;
  text-align: center;
  color: var(--read-soft);
  font-size: 12px;
}

.pager-info em {
  font-style: normal;
  margin-left: 4px;
}

.agent-entry {
  gap: 6px;
  padding: 0 11px;
}

.agent-entry.active {
  background: var(--read-line);
}

.agent-entry .el-icon {
  font-size: 15px;
}

.agent-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: 380px;
  max-width: 100%;
  background: var(--read-panel);
  border-left: 1px solid var(--read-line);
  box-shadow: -18px 0 40px rgba(0, 0, 0, 0.12);
  color: var(--read-ink);
  font-family: var(--read-serif);
}

.agent-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--read-line);
}

.agent-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.agent-title strong {
  font-size: 14px;
  font-weight: 500;
}

.agent-title span {
  color: var(--read-soft);
  font-size: 11px;
}

.agent-head-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.agent-head-actions button {
  padding: 4px 6px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--read-soft);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}

.agent-head-actions button:hover {
  background: var(--read-line);
  color: var(--read-ink);
}

.agent-head-actions button:last-child {
  font-size: 18px;
  line-height: 1;
}

.agent-head-actions button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.agent-messages {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
  padding: 16px;
}

.agent-welcome {
  color: var(--read-soft);
  font-size: 12px;
  line-height: 1.8;
}

.agent-welcome p {
  margin: 0 0 12px;
}

.agent-quick {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.agent-quick button {
  padding: 5px 9px;
  border: 1px solid var(--read-line);
  border-radius: 999px;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}

.agent-quick button:hover {
  background: var(--read-line);
}

.agent-msg {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  max-width: 100%;
}

.agent-msg.user {
  align-items: flex-end;
}

.agent-bubble {
  max-width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--read-line);
  border-radius: 9px;
  background: var(--read-bg);
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.agent-msg.user .agent-bubble {
  border-color: transparent;
  background: var(--read-line);
}

.agent-loading {
  color: var(--read-soft);
}

.agent-trace {
  width: 100%;
  color: var(--read-soft);
  font-size: 11px;
}

.agent-trace summary {
  cursor: pointer;
  user-select: none;
}

.agent-tool {
  display: flex;
  gap: 6px;
  padding: 3px 0 0 10px;
}

.agent-tool span {
  color: var(--read-ink);
}

.agent-tool em {
  flex: 1;
  overflow: hidden;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-tool b {
  color: #b54a3a;
  font-weight: 400;
}

.agent-cites {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.agent-cite {
  padding: 2px 6px;
  border: 1px solid var(--read-line);
  border-radius: 999px;
  background: transparent;
  color: var(--read-soft);
  font-family: inherit;
  font-size: 10px;
  cursor: pointer;
}

.agent-cite:hover {
  background: var(--read-line);
  color: var(--read-ink);
}

.agent-cite-warn {
  color: #b54a3a;
  font-size: 10px;
}

.agent-budget {
  color: var(--read-soft);
  font-size: 10px;
}

.agent-compose {
  padding: 10px 12px 12px;
  border-top: 1px solid var(--read-line);
}

.agent-compose textarea {
  box-sizing: border-box;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--read-line);
  border-radius: 7px;
  outline: none;
  background: var(--read-bg);
  color: var(--read-ink);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  resize: none;
}

.agent-compose textarea:focus {
  border-color: var(--read-soft);
}

.agent-compose-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
}

.agent-compose-foot span {
  color: var(--read-soft);
  font-size: 10px;
}

.agent-send {
  height: 28px;
  padding: 0 14px;
  border: 1px solid var(--read-line);
  border-radius: 6px;
  background: var(--read-line);
  color: var(--read-ink);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}

.agent-send:hover:not(:disabled) {
  filter: brightness(0.96);
}

.agent-send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

@media (max-width: 640px) {
  .agent-panel {
    width: 100%;
  }

  .agent-entry-label {
    display: none;
  }
}

@media (max-width: 900px) {
  .side {
    position: absolute;
    z-index: 5;
    height: 100%;
  }

  .side.folded {
    margin-left: -268px;
  }

  .paper {
    padding: 32px 22px 80px;
  }
}
</style>
