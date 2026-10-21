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
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Fold } from '@element-plus/icons-vue'
import { getBookApi, getChapterApi, listChaptersApi } from '@/api/book'

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
