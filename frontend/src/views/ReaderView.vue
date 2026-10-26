<template>
  <div class="reader" :data-theme="theme" :class="{ 'agent-visible': agentOpen }">
    <button v-if="sideOpen" class="catalog-backdrop" type="button" aria-label="关闭目录" @click="sideOpen = false" />
    <aside v-show="sideOpen" id="reader-catalog" class="side" aria-label="章节目录">
      <div class="side-head">
        <button class="icon-btn" type="button" title="返回书籍" @click="backToBook">
          <el-icon><ArrowLeft /></el-icon>
        </button>
        <div class="side-meta">
          <span class="catalog-label">墨读 · {{ sideMode === 'bookmarks' ? '本书书签' : '章节目录' }}</span>
          <strong :title="book?.title">{{ book?.title || '正在阅读' }}</strong>
        </div>
        <button class="icon-btn close-catalog" type="button" title="收起目录" aria-label="收起目录" @click="toggleCatalog"><Close /></button>
      </div>
      <div class="catalog-tabs" role="tablist" aria-label="阅读导航"><button type="button" role="tab" :aria-selected="sideMode === 'chapters'" @click="sideMode = 'chapters'">目录</button><button type="button" role="tab" :aria-selected="sideMode === 'bookmarks'" @click="showBookmarks">书签 <small>{{ bookmarkTotal }}</small></button></div>
      <div v-if="sideMode === 'chapters'" class="catalog-search"><Search aria-hidden="true" /><input v-model="chapterSearch" aria-label="搜索章节" placeholder="搜索标题或章号" /></div>
      <div class="catalog-summary"><span>{{ sideMode === 'chapters' ? `${chapters.length} 章` : `${bookmarkTotal} 条书签` }}</span><span>正在读第 {{ chapterIdx + 1 }} 章</span></div>
      <div v-if="sideMode === 'chapters'" ref="chapterListRef" class="chapter-list" role="tabpanel" aria-label="章节目录列表">
        <button
          v-for="item in filteredChapters"
          :key="item.idx"
          type="button"
          class="chapter-item"
          :class="{ active: item.idx === chapterIdx }"
          :aria-current="item.idx === chapterIdx ? 'page' : undefined"
          @click="openChapter(item.idx)"
        >
          <span class="num">{{ item.idx + 1 }}</span>
          <span class="name">{{ item.title }}</span>
          <span v-if="item.kind === 'extra'" class="tag">番外</span>
        </button>
        <p v-if="!filteredChapters.length && chapterSearch" class="catalog-empty">没有找到对应章节</p>
      </div>
      <div v-else class="bookmark-list" role="tabpanel" aria-label="本书书签">
        <button class="bookmark-add" type="button" :disabled="loading || !!readError || !current || bookmarkSaving" @click="addCurrentBookmark"><CollectionTag />{{ bookmarkSaving ? '正在保存…' : currentBookmarked ? '此处已有书签' : '添加当前阅读位置' }}</button>
        <p v-if="bookmarkError" class="bookmark-error" role="alert">{{ bookmarkError }}<button type="button" @click="loadBookmarks()">重试</button></p>
        <p v-if="bookmarkLoading && !bookmarks.length" class="catalog-empty">正在读取书签…</p>
        <div v-else-if="!bookmarks.length && !bookmarkError" class="bookmark-empty"><CollectionTag /><strong>把想回看的段落留在这里</strong><p>点击上方按钮，保存当前段落。书签只属于这本书。</p></div>
        <article v-for="mark in bookmarks" :key="mark.id" class="bookmark-card" :class="{ 'at-position': mark.chapter_idx === chapterIdx && mark.char_offset === currentOffset }">
          <button type="button" class="bookmark-jump" :disabled="loading" @click="jumpBookmark(mark)"><span class="bookmark-chapter">第 {{ mark.chapter_idx + 1 }} 章<span v-if="mark.chapter_kind === 'extra'"> · 番外</span></span><strong>{{ mark.label || mark.chapter_title }}</strong><span class="bookmark-excerpt">{{ mark.excerpt }}</span></button>
          <form v-if="editingBookmarkId === mark.id" class="bookmark-edit" @submit.prevent="saveBookmarkLabel(mark)"><input v-model="bookmarkLabel" maxlength="80" aria-label="书签备注" placeholder="给这条书签加个备注" /><div><button type="submit" :disabled="bookmarkBusyId === mark.id">保存</button><button type="button" @click="editingBookmarkId = null">取消</button></div></form>
          <footer><span>{{ new Date(mark.create_time).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) }}</span><div><button type="button" :disabled="bookmarkBusyId === mark.id" @click="editBookmark(mark)">备注</button><button type="button" :disabled="bookmarkBusyId === mark.id" :aria-label="`删除书签：${mark.label || mark.chapter_title}`" @click="removeBookmark(mark)">删除</button></div></footer>
        </article>
        <button v-if="bookmarkHasMore" class="bookmark-more" type="button" :disabled="bookmarkLoading" @click="loadBookmarks(true)">{{ bookmarkLoading ? '正在加载…' : '加载更多书签' }}</button>
      </div>
      <div class="catalog-foot"><span class="reading-dot" />阅读位置自动保存</div>
    </aside>

    <main class="stage">
      <header class="topbar">
        <button class="icon-btn" type="button" title="目录" aria-label="切换目录" :aria-expanded="sideOpen" aria-controls="reader-catalog" @click="toggleCatalog">
          <el-icon><Fold /></el-icon>
        </button>
        <div class="crumb"><span>{{ book?.title || '墨读' }}</span><small>{{ loading ? '正在翻章…' : current?.title || '正在打开' }}</small></div>
        <div class="tools">
          <button class="icon-btn bookmark-entry" :class="{ active: currentBookmarked }" type="button" :disabled="loading || !!readError || !current || bookmarkSaving" :title="currentBookmarked ? '此处已有书签，查看书签' : '添加当前段落书签'" :aria-label="currentBookmarked ? '查看当前书签' : '添加当前段落书签'" @click="addCurrentBookmark"><CollectionTag /></button>
          <button class="tool-btn story-entry" type="button" title="查看剧情线" @click="openStorylines"><Share /><span>剧情线</span></button>
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
          <div ref="settingsRef" class="settings-wrap">
            <button class="tool-btn" type="button" :class="{ active: settingsOpen }" :aria-expanded="settingsOpen" aria-controls="reader-settings" @click="settingsOpen = !settingsOpen"><span class="type-mark">Aa</span><span class="settings-label">阅读设置</span></button>
            <section v-if="settingsOpen" id="reader-settings" class="reading-settings" role="dialog" aria-label="阅读设置">
              <header><h2>阅读设置</h2><button type="button" class="reset-settings" @click="resetPreferences">重置排版</button></header>
              <div class="setting-row"><span>字号</span><div class="font-stepper"><button type="button" aria-label="减小字号" :disabled="fontIndex === 0" @click="stepFont(-1)">A−</button><output aria-live="polite">{{ fontPx }} <small>px</small></output><button type="button" aria-label="增大字号" :disabled="fontIndex === FONTS.length - 1" @click="stepFont(1)">A+</button></div></div>
              <div class="setting-row"><span>正文字体</span><div class="setting-options"><button v-for="(label, value) in { serif: '宋体', sans: '黑体' }" :key="value" type="button" :aria-pressed="preferences.fontFamily === value" @click="setPreference('fontFamily', value)">{{ label }}</button></div></div>
              <div class="setting-row"><span>行距</span><div class="setting-options"><button v-for="(value, index) in LINE_HEIGHTS" :key="value" type="button" :aria-pressed="preferences.lineHeight === value" @click="setPreference('lineHeight', value)">{{ ['紧凑', '标准', '宽松'][index] }}</button></div></div>
              <div class="setting-row"><span>阅读宽度</span><div class="setting-options"><button v-for="(value, index) in PAGE_WIDTHS" :key="value" type="button" :aria-pressed="preferences.pageWidth === value" @click="setPreference('pageWidth', value)">{{ ['窄', '适中', '宽'][index] }}</button></div></div>
              <div class="setting-row"><span>外观</span><div class="setting-options"><button type="button" :aria-pressed="theme === 'paper'" @click="setTheme('paper')"><Sunny />日间</button><button type="button" :aria-pressed="theme === 'night'" @click="setTheme('night')"><Moon />夜间</button></div></div>
              <p>设置自动保存，日夜模式共用同一套排版。</p>
            </section>
          </div>
          <button class="icon-btn theme-toggle" type="button" :title="theme === 'night' ? '切换日间模式' : '切换夜间模式'" :aria-label="theme === 'night' ? '切换日间模式' : '切换夜间模式'" @click="toggleTheme"><Sunny v-if="theme === 'night'" /><Moon v-else /></button>
        </div>
      </header>

      <div ref="scrollRef" class="scroll" tabindex="0" aria-label="章节正文" :aria-busy="loading" @scroll.passive="onScroll">
        <div v-if="loading && !current" class="reader-state">正在翻开这一章…</div>
        <div v-else-if="readError" class="reader-state" role="alert"><p>{{ readError }}</p><button v-if="chapters.length" type="button" class="pager-btn" @click="loadChapter(Number(route.params.idx) || 0)">重新加载</button><button v-else type="button" class="pager-btn" @click="backToBook">返回书籍</button></div>
        <article v-else-if="current" class="reading-paper" :style="readingStyle">
          <header class="chapter-heading"><div class="chapter-kicker"><span>{{ current.kind === 'extra' ? '番外' : '正文' }}</span><span>{{ chapterWordCount.toLocaleString() }} 字</span></div><h1 class="chapter-title">{{ current.title }}</h1><div class="chapter-rule"><span>{{ String(chapterIdx + 1).padStart(2, '0') }}</span></div></header>
          <p
            v-for="item in paragraphs"
            :key="item.offset"
            class="para"
            :class="{ 'bookmark-highlight': highlightedOffset === item.offset }"
            :data-offset="item.offset"
          >
            {{ item.text }}
          </p>
          <p v-if="!loading && !paragraphs.length" class="empty">本章没有正文。</p>
          <div class="chapter-end"><span />本章完<span /></div>
        </article>
      </div>

      <footer class="pager">
        <button
          class="pager-btn"
          type="button"
          :disabled="loading || chapterIdx <= 0"
          @click="openChapter(chapterIdx - 1)"
        >
          ← 上一章
        </button>
        <span class="pager-info">
          {{ chapters.length ? chapterIdx + 1 : 0 }} / {{ chapters.length }}
          <em>· 本章 {{ percent }}%</em>
        </span>
        <button
          class="pager-btn"
          type="button"
          :disabled="loading || !chapters.length || chapterIdx >= chapters.length - 1"
          @click="openChapter(chapterIdx + 1)"
        >
          下一章 →
        </button>
      </footer>
      <div class="reading-progress" aria-hidden="true"><span :style="{ width: `${percent}%` }" /></div>
    </main>

    <aside v-if="agentOpen" class="agent-panel" aria-label="伴读">
      <header class="agent-head">
        <div class="agent-title">
          <strong>伴读</strong>
          <span>第 {{ chapterIdx + 1 }} 章 · 截至本章</span>
        </div>
        <div class="agent-head-actions">
          <button type="button" :disabled="agentLoading" @click="clearAgent">清空</button>
          <button type="button" aria-label="关闭伴读" @click="closeAgent">×</button>
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
import { ArrowLeft, ChatDotRound, Close, CollectionTag, Fold, Moon, Search, Share, Sunny } from '@element-plus/icons-vue'
import { createBookmark, deleteBookmark, listBookmarks, updateBookmark } from '@/api/bookmarks'
import { DEFAULT_READER_PREFERENCES, FONT_SIZES, LINE_HEIGHTS, PAGE_WIDTHS, READER_PREFERENCES_KEY, parseReaderPreferences } from '@/utils/readerPreferences'
import { getBookApi, getChapterApi, listChaptersApi } from '@/api/book'
import {
  chatAgentApi,
  clearAgentSessionApi,
  getAgentCapabilitiesApi,
  getAgentSessionApi,
  putReadingProgressApi
} from '@/api/agent'

const FONTS = FONT_SIZES
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
const readError = ref('')
const sideOpen = ref(true)
const chapterListRef = ref(null)
const theme = ref('paper')
const preferences = ref({ ...DEFAULT_READER_PREFERENCES })
const settingsOpen = ref(false)
const settingsRef = ref(null)
const chapterSearch = ref('')
const sideMode = ref('chapters')
const bookmarks = ref([])
const bookmarkTotal = ref(0)
const bookmarkLoading = ref(false)
const bookmarkSaving = ref(false)
const bookmarkHasMore = ref(false)
const bookmarkPage = ref(1)
const bookmarkError = ref('')
const bookmarkBusyId = ref(null)
const editingBookmarkId = ref(null)
const bookmarkLabel = ref('')
const currentOffset = ref(0)
const highlightedOffset = ref(null)
const percent = ref(0)
const scrollRef = ref(null)
const agentOpen = ref(false)
const agentAvailable = ref(false)
const agentInput = ref('')
const agentMessages = ref([])
const agentLoading = ref(false)
const agentSessionId = ref(null)
const agentScrollRef = ref(null)

const fontPx = computed(() => preferences.value.fontSize)
const fontIndex = computed(() => FONTS.indexOf(fontPx.value))
const chapterIdx = computed(() => current.value?.idx ?? 0)
const currentBookmarked = computed(() => bookmarks.value.some(mark => mark.chapter_idx === chapterIdx.value && mark.char_offset === currentOffset.value))
const filteredChapters = computed(() => {
  const query = chapterSearch.value.trim().toLowerCase()
  return chapters.value.filter(item => !query || item.title.toLowerCase().includes(query) || String(item.idx + 1) === query)
})
const chapterWordCount = computed(() => paragraphs.value.reduce((sum, item) => sum + item.text.length, 0))
const readingStyle = computed(() => ({
  '--reading-size': `${fontPx.value}px`, '--reading-leading': preferences.value.lineHeight,
  '--reading-width': `${preferences.value.pageWidth}px`,
  '--reading-family': preferences.value.fontFamily === 'sans' ? 'var(--read-sans)' : 'var(--read-serif)'
}))

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
  if (!node || !current.value || loading.value || readError.value) return
  const offset = pickTopOffset()
  currentOffset.value = offset
  store(
    progressKey(bookId.value),
    JSON.stringify({
      chapterIdx: current.value.idx,
      offset,
      percent: percent.value,
      atTop: node.scrollTop <= 24,
      at: Date.now()
    })
  )
  putReadingProgressApi(bookId.value, {
    chapter_idx: current.value.idx,
    char_offset: offset
  }).catch(() => {
    /* 后端未启动或同步失败不影响阅读 */
  })
}

function pickTopOffset() {
  const node = scrollRef.value
  if (!node) return current.value?.char_start ?? 0
  const line = node.getBoundingClientRect().top + 24
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
let layoutVersion = 0
let chapterRequestVersion = 0
let bookmarkRequestVersion = 0
let pendingBookmark = null
let highlightTimer = null
let disposed = false

async function loadBookmarks(append = false) {
  const version = ++bookmarkRequestVersion
  const requestedBook = bookId.value
  const page = append ? bookmarkPage.value + 1 : 1
  bookmarkLoading.value = true; bookmarkError.value = ''
  try {
    const response = await listBookmarks(requestedBook, page)
    if (disposed || version !== bookmarkRequestVersion || bookId.value !== requestedBook) return
    const data = response.data
    const combined = append ? [...bookmarks.value, ...data.items] : data.items
    bookmarks.value = [...new Map(combined.map(mark => [mark.id, mark])).values()]
    bookmarkTotal.value = data.total; bookmarkHasMore.value = data.has_more; bookmarkPage.value = page
  } catch (error) {
    if (!disposed && version === bookmarkRequestVersion && bookId.value === requestedBook) bookmarkError.value = error?.message || '书签暂时无法读取'
  } finally { if (version === bookmarkRequestVersion) bookmarkLoading.value = false }
}

async function showBookmarks() {
  sideMode.value = 'bookmarks'
  await preserveReadingPosition(() => { sideOpen.value = true })
  await loadBookmarks()
}

async function addCurrentBookmark() {
  if (loading.value || readError.value || !current.value || bookmarkSaving.value) return
  const requestBook = bookId.value
  const idx = chapterIdx.value
  const offset = pickTopOffset()
  currentOffset.value = offset
  const existing = bookmarks.value.find(mark => mark.chapter_idx === idx && mark.char_offset === offset)
  if (existing) { await showBookmarks(); return }
  bookmarkSaving.value = true
  try {
    const response = await createBookmark(requestBook, { chapter_idx: idx, char_offset: offset })
    if (disposed || bookId.value !== requestBook) return
    await loadBookmarks()
    if (disposed || bookId.value !== requestBook) return
    const mark = response.data.item
    if (!bookmarks.value.some(item => item.id === mark.id)) bookmarks.value.unshift(mark)
    if (chapterIdx.value === idx) {
      sideMode.value = 'bookmarks'; settingsOpen.value = false
      await preserveReadingPosition(() => { sideOpen.value = true })
    }
    ElMessage.success(response.data.created ? '已添加书签' : '此处已有书签')
  } catch (error) { if (!disposed && bookId.value === requestBook) ElMessage.error(error?.message || '书签保存失败') }
  finally { if (!disposed) bookmarkSaving.value = false }
}

function highlightBookmark(offset) {
  clearTimeout(highlightTimer)
  highlightedOffset.value = offset
  highlightTimer = setTimeout(() => { highlightedOffset.value = null }, 2400)
}

function jumpBookmark(mark) {
  if (loading.value) return
  settingsOpen.value = false
  if (scrollTimer) { clearTimeout(scrollTimer); scrollTimer = null }
  saveProgress()
  if (window.matchMedia('(max-width: 980px)').matches) sideOpen.value = false
  if (mark.chapter_idx === chapterIdx.value) {
    scrollToOffset(mark.char_offset); updatePercent(); saveProgress(); highlightBookmark(mark.char_offset)
  } else {
    pendingBookmark = { chapterIdx: mark.chapter_idx, offset: mark.char_offset }
    router.replace({ name: 'book-reader', params: { id: bookId.value, idx: mark.chapter_idx } })
  }
}

function editBookmark(mark) { editingBookmarkId.value = mark.id; bookmarkLabel.value = mark.label }
async function saveBookmarkLabel(mark) {
  const requestedBook = bookId.value
  bookmarkBusyId.value = mark.id
  try {
    const response = await updateBookmark(requestedBook, mark.id, bookmarkLabel.value.trim())
    if (!disposed && bookId.value === requestedBook) {
      bookmarks.value = bookmarks.value.map(item => item.id === mark.id ? response.data : item)
      editingBookmarkId.value = null
    }
  } catch (error) { if (!disposed) ElMessage.error(error?.message || '备注保存失败') }
  finally { if (!disposed) bookmarkBusyId.value = null }
}
async function removeBookmark(mark) {
  const requestedBook = bookId.value
  bookmarkBusyId.value = mark.id
  try {
    await deleteBookmark(requestedBook, mark.id)
    if (disposed || bookId.value !== requestedBook) return
    bookmarks.value = bookmarks.value.filter(item => item.id !== mark.id)
    bookmarkTotal.value = Math.max(0, bookmarkTotal.value - 1)
    if (editingBookmarkId.value === mark.id) editingBookmarkId.value = null
    await loadBookmarks()
  } catch (error) { if (!disposed) ElMessage.error(error?.message || '书签删除失败') }
  finally { if (!disposed) bookmarkBusyId.value = null }
}

async function preserveReadingPosition(change) {
  const version = ++layoutVersion
  const node = scrollRef.value
  const offset = pickTopOffset()
  const target = node?.querySelector(`[data-offset="${offset}"]`)
  const distance = target && node ? target.getBoundingClientRect().top - node.getBoundingClientRect().top : null
  const idx = current.value?.idx
  change()
  await nextTick()
  await new Promise(resolve => requestAnimationFrame(resolve))
  if (version !== layoutVersion || current.value?.idx !== idx || !node) return
  const restored = node.querySelector(`[data-offset="${offset}"]`)
  if (restored && distance !== null) node.scrollTop += restored.getBoundingClientRect().top - node.getBoundingClientRect().top - distance
  updatePercent()
  if (current.value && !loading.value) saveProgress()
}

function setPreference(key, value) {
  void preserveReadingPosition(() => {
    preferences.value = { ...preferences.value, [key]: value }
    store(READER_PREFERENCES_KEY, JSON.stringify(preferences.value))
  })
}

function resetPreferences() {
  void preserveReadingPosition(() => {
    preferences.value = { ...DEFAULT_READER_PREFERENCES }
    store(READER_PREFERENCES_KEY, JSON.stringify(preferences.value))
  })
}

function toggleCatalog() {
  settingsOpen.value = false
  void preserveReadingPosition(() => { sideOpen.value = !sideOpen.value })
}

function stepFont(delta) {
  const next = Math.min(FONTS.length - 1, Math.max(0, fontIndex.value + delta))
  if (next === fontIndex.value) return
  setPreference('fontSize', FONTS[next])
}

function toggleTheme() {
  setTheme(theme.value === 'night' ? 'paper' : 'night')
}

function setTheme(value) {
  theme.value = value
  store(THEME_KEY, theme.value)
}

function backToBook() {
  router.push(`/books/${bookId.value}`)
}

function openStorylines() {
  saveProgress()
  router.push({ path: `/books/${bookId.value}/storylines`, query: { from: 'reader', chapter: String(chapterIdx.value) } })
}

function openChapter(idx) {
  if (!chapters.value.length) return
  settingsOpen.value = false
  if (window.matchMedia('(max-width: 980px)').matches) sideOpen.value = false
  const next = Math.min(chapters.value.length - 1, Math.max(0, idx))
  if (next === current.value?.idx) return
  pendingBookmark = null
  highlightedOffset.value = null
  if (scrollTimer) { clearTimeout(scrollTimer); scrollTimer = null }
  saveProgress()
  router.replace({ name: 'book-reader', params: { id: bookId.value, idx: next } })
}

async function loadChapter(idx, restoreOffset = 0) {
  const requestVersion = ++chapterRequestVersion
  const requestBook = bookId.value
  loadingIndex = idx
  loading.value = true
  readError.value = ''
  try {
    const response = await getChapterApi(requestBook, idx)
    if (requestVersion !== chapterRequestVersion || bookId.value !== requestBook) return
    current.value = response.data
  } catch (error) {
    if (requestVersion === chapterRequestVersion && bookId.value === requestBook) readError.value = error?.message || '这一章暂时无法打开，请重试。'
    return
  } finally {
    if (requestVersion === chapterRequestVersion) { loading.value = false; loadingIndex = null }
  }
  await nextTick()
  await new Promise((resolve) => requestAnimationFrame(resolve))
  if (requestVersion !== chapterRequestVersion || bookId.value !== requestBook) return
  if (restoreOffset) {
    scrollToOffset(restoreOffset)
  } else {
    scrollRef.value?.scrollTo({ top: 0 })
    percent.value = 0
  }
  updatePercent()
  saveProgress()
  agentMessages.value = []
  if (agentOpen.value) await loadAgentSession()
}

function onDocumentPointer(event) {
  if (settingsOpen.value && !settingsRef.value?.contains(event.target)) settingsOpen.value = false
}

function onReaderKey(event) {
  if (event.key === 'Escape') {
    if (settingsOpen.value) settingsOpen.value = false
    else if (agentOpen.value) closeAgent()
    else if (window.matchMedia('(max-width: 980px)').matches) sideOpen.value = false
    return
  }
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || settingsOpen.value || loading.value) return
  if (event.target.closest?.('input,textarea,select,button,a,[contenteditable="true"]')) return
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault()
    openChapter(chapterIdx.value + (event.key === 'ArrowLeft' ? -1 : 1))
  }
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
  settingsOpen.value = false
  await preserveReadingPosition(() => { agentOpen.value = !agentOpen.value })
  if (agentOpen.value) {
    await loadAgentSession()
    await scrollAgent()
  }
}

function closeAgent() {
  void preserveReadingPosition(() => { agentOpen.value = false })
}

async function loadAgentSession() {
  const requestBook = bookId.value
  const requestChapter = chapterIdx.value
  try {
    const response = await getAgentSessionApi(requestBook, requestChapter)
    if (bookId.value !== requestBook || chapterIdx.value !== requestChapter) return
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
    if (bookId.value !== requestBook || chapterIdx.value !== requestChapter) return
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
  const requestBook = bookId.value
  const requestChapter = chapterIdx.value
  agentInput.value = ''
  agentMessages.value.push({ role: 'user', content: text })
  agentLoading.value = true
  await scrollAgent()
  try {
    const response = await chatAgentApi(requestBook, {
      message: text,
      chapter_idx: chapterIdx.value,
      char_offset: pickTopOffset(),
      session_id: agentSessionId.value || undefined
    })
    if (bookId.value !== requestBook || chapterIdx.value !== requestChapter) return
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
  preferences.value = parseReaderPreferences(readStored(READER_PREFERENCES_KEY, null), readStored(FONT_KEY, null))
  sideOpen.value = !window.matchMedia('(max-width: 980px)').matches
  document.addEventListener('pointerdown', onDocumentPointer)
  document.addEventListener('keydown', onReaderKey)

  loading.value = true
  try {
    const [bookResponse, chapterResponse] = await Promise.all([
      getBookApi(bookId.value),
      listChaptersApi(bookId.value)
    ])
    book.value = bookResponse.data
    chapters.value = chapterResponse.data || []
    if (!chapters.value.length) {
      loading.value = false
      readError.value = '这本书还没有可读章节，请返回书籍详情检查解析结果。'
      return
    }
    const progress = savedProgress()
    const fromRoute = route.params.idx === undefined ? null : Number(route.params.idx)
    const start = fromRoute !== null && Number.isInteger(fromRoute) ? fromRoute : progress?.chapterIdx ?? 0
    const idx = Math.min(chapters.value.length - 1, Math.max(0, start))
    if (idx !== Number(route.params.idx)) router.replace({ name: 'book-reader', params: { id: bookId.value, idx } })
    const restore = progress && progress.chapterIdx === idx && !(progress.atTop === true || (progress.atTop === undefined && progress.percent === 0)) ? Number(progress.offset || 0) : 0
    await loadChapter(idx, restore)
    await Promise.all([loadAgentAvailability(), loadBookmarks()])
  } catch (error) {
    loading.value = false
    readError.value = error?.message || '暂时无法打开这本书，请检查后端连接。'
  }
})

watch(
  () => route.params.idx,
  (value) => {
    if (value === undefined) return
    const idx = Number(value)
    if (!Number.isInteger(idx)) return
    if (idx === loadingIndex || idx === current.value?.idx) return
    const bookmark = pendingBookmark?.chapterIdx === idx ? pendingBookmark : null
    pendingBookmark = null
    void loadChapter(idx, bookmark?.offset || 0).then(() => {
      if (bookmark && current.value?.idx === idx && !readError.value) highlightBookmark(bookmark.offset)
    })
  }
)

watch([chapterIdx, sideOpen, sideMode, filteredChapters], () => {
  if (!sideOpen.value || sideMode.value !== 'chapters') return
  const list = chapterListRef.value
  const selected = list?.querySelector('[aria-current="page"]')
  if (!selected) return
  const listRect = list.getBoundingClientRect()
  const selectedRect = selected.getBoundingClientRect()
  // 只滚动目录，避免影响正文的阅读位置。
  list.scrollTop += selectedRect.top - listRect.top - (list.clientHeight - selectedRect.height) / 2
}, { flush: 'post' })

watch(bookId, () => {
  bookmarkRequestVersion++
  bookmarks.value = []; bookmarkTotal.value = 0; bookmarkHasMore.value = false; bookmarkError.value = ''
  pendingBookmark = null
  void loadBookmarks()
  agentOpen.value = false
  void loadAgentAvailability()
})

onBeforeUnmount(() => {
  disposed = true
  bookmarkRequestVersion++
  clearTimeout(highlightTimer)
  chapterRequestVersion++
  layoutVersion++
  document.removeEventListener('pointerdown', onDocumentPointer)
  document.removeEventListener('keydown', onReaderKey)
  if (scrollTimer) clearTimeout(scrollTimer)
  saveProgress()
})
</script>

<style scoped>

.reader {
  --read-serif: 'Noto Serif SC', 'Songti SC', 'STSong', 'SimSun', serif;
  --read-sans: 'Microsoft YaHei', 'PingFang SC', 'Noto Sans SC', sans-serif;
  --scroll-thumb: var(--read-line); --scroll-track: var(--read-bg);
  display: flex; width: 100%; height: 100vh; height: 100dvh; margin: 0; padding: 0;
  overflow: hidden; font-family: var(--read-sans); line-height: 1.5;
  background: var(--read-bg); color: var(--read-ink);
}
.reader[data-theme='paper'] {
  --read-bg: #e9e8df; --read-panel: #f2f1e9; --read-page: #faf9f2;
  --read-ink: #343b33; --read-soft: #71796b; --read-line: #d8ddcf;
  --read-accent: #48694d; --read-active: #e3eadb; --read-hover: #e9ecdf;
  --read-selection: #d0dfc6; --read-selection-ink: #253824;
  --read-shadow: 0 12px 40px #263d2410; color-scheme: light;
}
.reader[data-theme='night'] {
  --read-bg: #141b1b; --read-panel: #1b2423; --read-page: #202a28;
  --read-ink: #d5dfd2; --read-soft: #9ba99b; --read-line: #35433a;
  --read-accent: #b7c9a4; --read-active: #304235; --read-hover: #28362d;
  --read-selection: #476044; --read-selection-ink: #eff5e7;
  --read-shadow: 0 12px 40px #00000016; color-scheme: dark;
}
.reader ::selection { background: var(--read-selection); color: var(--read-selection-ink); }
.reader ::-webkit-scrollbar { width: 7px; height: 7px; }
.reader ::-webkit-scrollbar-track { background: var(--read-bg); }
.reader ::-webkit-scrollbar-thumb { background: var(--read-line); border-radius: 8px; }
.reader button { -webkit-tap-highlight-color: transparent; }
.reader button:focus-visible, .reader input:focus-visible, .reader textarea:focus-visible {
  outline: 2px solid var(--read-accent); outline-offset: 3px;
}
.reader svg { width: 17px; height: 17px; flex-shrink: 0; }
.catalog-backdrop { display: none; }
.side { display: flex; flex-direction: column; width: 272px; flex: 0 0 272px; min-height: 0; background: var(--read-panel); border-right: 1px solid var(--read-line); }
.side-head { display: flex; align-items: center; gap: 10px; min-height: 95px; padding: 22px 14px; }
.side-meta { display: flex; flex: 1; flex-direction: column; gap: 5px; min-width: 0; }
.side-meta strong { overflow: hidden; font-family: var(--read-serif); font-size: 17px; font-weight: 500; white-space: nowrap; text-overflow: ellipsis; }
.catalog-label { color: var(--read-soft); font-size: 10px; letter-spacing: .08em; }
.catalog-tabs { display: flex; gap: 4px; margin: 0 16px 14px; padding: 3px; border: 1px solid var(--read-line); border-radius: 7px; }
.catalog-tabs button { flex: 1; padding: 7px; border: 0; border-radius: 4px; background: transparent; color: var(--read-soft); font-size: 12px; cursor: pointer; }
.catalog-tabs button[aria-selected='true'] { background: var(--read-active); color: var(--read-accent); }
.catalog-tabs small { margin-left: 5px; font-size: 10px; font-variant-numeric: tabular-nums; }
.close-catalog { flex-shrink: 0; }
.catalog-search { display: flex; align-items: center; gap: 9px; margin: 0 16px 18px; padding: 9px 11px; border: 1px solid var(--read-line); border-radius: 7px; background: var(--read-page); color: var(--read-soft); }
.catalog-search svg { width: 14px; height: 14px; }
.catalog-search input { width: 100%; min-width: 0; padding: 0; border: 0; background: transparent; color: var(--read-ink); font: 12px var(--read-sans); outline: none; }
.catalog-search input::placeholder { color: var(--read-soft); }
.catalog-search:focus-within { border-color: var(--read-accent); }
.catalog-summary { display: flex; justify-content: space-between; padding: 0 20px 12px; color: var(--read-soft); font-size: 10px; }
.chapter-list { flex: 1; min-height: 0; overflow-y: auto; padding: 0 12px 18px; }
.chapter-item { display: flex; align-items: baseline; gap: 10px; width: 100%; padding: 13px 12px; border: 0; border-radius: 6px; background: transparent; color: inherit; font-family: var(--read-sans); font-size: 12px; text-align: left; cursor: pointer; }
.chapter-item:hover { background: var(--read-hover); }
.chapter-item.active { background: var(--read-active); color: var(--read-accent); box-shadow: inset 2px 0 0 var(--read-accent); }
.chapter-item .num { flex: 0 0 25px; color: var(--read-soft); font-size: 10px; font-variant-numeric: tabular-nums; }
.chapter-item .name { flex: 1; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.chapter-item .tag { font-size: 9px; color: var(--read-soft); }
.catalog-empty { padding: 24px 12px; color: var(--read-soft); font-size: 12px; text-align: center; }
.catalog-foot { display: flex; align-items: center; gap: 8px; padding: 16px 22px; border-top: 1px solid var(--read-line); color: var(--read-soft); font-size: 10px; }
.bookmark-list { flex: 1; min-height: 0; overflow-y: auto; padding: 0 14px 18px; }
.bookmark-add, .bookmark-more { display: flex; justify-content: center; align-items: center; gap: 7px; width: 100%; padding: 10px; border: 1px dashed var(--read-line); border-radius: 6px; background: transparent; color: var(--read-accent); font: 11px var(--read-sans); cursor: pointer; }
.bookmark-add:hover, .bookmark-more:hover { background: var(--read-hover); }
.bookmark-add:disabled, .bookmark-more:disabled { opacity: .55; cursor: default; }
.bookmark-card { margin-top: 12px; padding: 0 12px; border: 1px solid var(--read-line); border-radius: 7px; background: var(--read-page); }
.bookmark-card.at-position { border-color: var(--read-accent); }
.bookmark-jump { display: flex; flex-direction: column; gap: 8px; width: 100%; padding: 14px 0 12px; border: 0; background: transparent; color: var(--read-ink); text-align: left; cursor: pointer; }
.bookmark-chapter { font-size: 10px; color: var(--read-accent); }
.bookmark-jump strong { font: 13px/1.7 var(--read-sans); overflow-wrap: anywhere; }
.bookmark-excerpt { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; font: 12px/1.85 var(--read-serif); color: var(--read-soft); overflow-wrap: anywhere; }
.bookmark-card footer { display: flex; justify-content: space-between; align-items: center; padding: 9px 0; border-top: 1px solid var(--read-line); color: var(--read-soft); font-size: 9px; }
.bookmark-card footer button, .bookmark-edit button, .bookmark-error button { padding: 3px 6px; border: 0; border-radius: 3px; background: transparent; color: var(--read-soft); font: 10px var(--read-sans); cursor: pointer; }
.bookmark-card footer button:hover { background: var(--read-hover); color: var(--read-accent); }
.bookmark-edit { padding-bottom: 10px; }
.bookmark-edit input { width: 100%; padding: 7px; border: 1px solid var(--read-line); border-radius: 4px; color: var(--read-ink); background: var(--read-panel); font: 12px var(--read-sans); }
.bookmark-edit > div { display: flex; justify-content: end; gap: 5px; margin-top: 6px; }
.bookmark-edit button[type='submit'] { color: var(--read-accent); }
.bookmark-empty { display: flex; flex-direction: column; align-items: center; gap: 15px; padding: 32px 14px; text-align: center; }
.bookmark-empty > svg { width: 26px; height: 26px; color: var(--read-accent); }
.bookmark-empty strong { font: 13px/1.8 var(--read-serif); }
.bookmark-empty p, .bookmark-error { color: var(--read-soft); font: 11px/1.9 var(--read-sans); }
.bookmark-error { padding: 12px 0; }
.bookmark-error button { color: var(--read-accent); }
.bookmark-more { margin-top: 14px; }
.reading-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--read-accent); }
.stage { position: relative; display: flex; flex: 1; flex-direction: column; min-width: 0; min-height: 0; }
.topbar { position: relative; z-index: 40; display: flex; align-items: center; gap: 14px; flex: 0 0 66px; padding: 0 24px; background: var(--read-panel); border-bottom: 1px solid var(--read-line); }
.crumb { display: flex; flex: 1; flex-direction: column; gap: 3px; min-width: 0; }
.crumb > span { overflow: hidden; color: var(--read-ink); font-size: 12px; white-space: nowrap; text-overflow: ellipsis; }
.crumb > small { overflow: hidden; font-size: 10px; color: var(--read-soft); white-space: nowrap; text-overflow: ellipsis; }
.tools { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.icon-btn, .tool-btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 34px; border: 1px solid var(--read-line); border-radius: 6px; background: transparent; color: var(--read-ink); font-family: var(--read-sans); font-size: 11px; cursor: pointer; }
.icon-btn { width: 34px; padding: 7px; }
.tool-btn { padding: 0 11px; }
.icon-btn:hover, .tool-btn:hover, .tool-btn.active { background: var(--read-hover); }
.bookmark-entry.active { color: var(--read-accent); background: var(--read-active); }
.icon-btn:disabled { opacity: .45; cursor: default; }
.tools .theme-toggle { border-color: transparent; }
.type-mark { font-family: Georgia, serif; font-size: 17px; }
.settings-wrap { position: relative; }
.reading-settings { position: absolute; right: 0; top: calc(100% + 14px); width: 326px; max-width: calc(100vw - 28px); padding: 22px; border: 1px solid var(--read-line); border-radius: 12px; box-shadow: var(--read-shadow), 0 8px 30px #00000012; background: var(--read-panel); color: var(--read-ink); }
.reading-settings header { display: flex; justify-content: space-between; align-items: center; gap: 20px; padding-bottom: 18px; border-bottom: 1px solid var(--read-line); }
.reading-settings h2 { font-family: var(--read-serif); font-size: 17px; font-weight: 500; }
.reset-settings { padding: 3px 0; border: 0; background: transparent; color: var(--read-soft); font-size: 10px; cursor: pointer; }
.reset-settings:hover { color: var(--read-accent); }
.setting-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 19px; }
.setting-row > span { font-size: 11px; color: var(--read-soft); }
.setting-options, .font-stepper { display: flex; align-items: center; gap: 3px; padding: 3px; border: 1px solid var(--read-line); border-radius: 6px; }
.setting-options button, .font-stepper button { display: inline-flex; justify-content: center; align-items: center; gap: 5px; min-width: 45px; padding: 6px 7px; border: 0; border-radius: 3px; background: transparent; color: var(--read-soft); font-size: 11px; cursor: pointer; }
.setting-options button[aria-pressed='true'] { background: var(--read-active); color: var(--read-accent); }
.setting-options button:hover, .font-stepper button:hover:not(:disabled) { background: var(--read-hover); }
.font-stepper button { font: 15px Georgia, serif; }
.font-stepper button:disabled { opacity: .35; cursor: not-allowed; }
.font-stepper output { min-width: 55px; color: var(--read-ink); text-align: center; font-size: 14px; font-variant-numeric: tabular-nums; }
.font-stepper small { font-size: 9px; color: var(--read-soft); }
.reading-settings > p { margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--read-line); color: var(--read-soft); font-size: 10px; line-height: 1.8; }
.scroll { flex: 1; min-height: 0; overflow-y: auto; outline: none; scrollbar-gutter: stable; }
.scroll:focus-visible { box-shadow: inset 0 0 0 2px var(--read-line); }
.reading-paper { width: calc(100% - 64px); max-width: var(--reading-width, 780px); margin: 32px auto 40px; padding: 50px 56px 64px; border: 1px solid var(--read-line); border-radius: 10px; box-shadow: var(--read-shadow); background: var(--read-page); color: var(--read-ink); font-family: var(--reading-family, var(--read-serif)); font-size: var(--reading-size, 20px); line-height: var(--reading-leading, 1.95); }
.chapter-heading { margin-bottom: 2em; }
.chapter-kicker { display: flex; justify-content: space-between; color: var(--read-soft); font: 10px/1.5 var(--read-sans); letter-spacing: .08em; }
.chapter-title { margin: 30px 0 26px; font-family: var(--read-serif); font-size: 1.45em; line-height: 1.6; font-weight: 500; letter-spacing: .04em; text-align: center; overflow-wrap: anywhere; }
.chapter-rule { display: flex; align-items: center; gap: 16px; color: var(--read-accent); font: 11px/1.5 Georgia, serif; }
.chapter-rule::before, .chapter-rule::after { content: ''; height: 1px; flex: 1; background: var(--read-line); }
.para { margin: 0 0 .85em; text-indent: 2em; text-align: justify; letter-spacing: .015em; overflow-wrap: break-word; }
.para.bookmark-highlight { background: var(--read-active); outline: 6px solid var(--read-active); border-radius: 2px; }
.empty { color: var(--read-soft); font-size: 14px; text-align: center; }
.reader-state { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 20px; min-height: 360px; padding: 40px; color: var(--read-soft); font-size: 14px; text-align: center; }
.chapter-end { display: flex; align-items: center; justify-content: center; gap: 20px; margin-top: 3.5em; color: var(--read-soft); font: 11px/1.5 var(--read-sans); letter-spacing: .25em; }
.chapter-end > span { width: 28px; height: 1px; background: var(--read-line); }
.pager { display: flex; align-items: center; gap: 18px; flex: 0 0 60px; padding: 0 24px; background: var(--read-panel); border-top: 1px solid var(--read-line); }
.pager-btn { height: 32px; padding: 0 14px; border: 1px solid var(--read-line); border-radius: 6px; background: transparent; color: var(--read-ink); font-family: var(--read-sans); font-size: 11px; cursor: pointer; }
.pager-btn:hover:not(:disabled) { background: var(--read-hover); }
.pager-btn:disabled { opacity: .35; cursor: not-allowed; }
.pager-info { flex: 1; text-align: center; color: var(--read-soft); font-size: 11px; font-variant-numeric: tabular-nums; }
.pager-info em { font-style: normal; margin-left: 5px; }
.reading-progress { height: 2px; flex: 0 0 2px; background: var(--read-line); }
.reading-progress > span { display: block; height: 100%; background: var(--read-accent); }
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


.reader .agent-panel { background: var(--read-panel); font-family: var(--read-sans); }
@media (min-width: 1400px) { .reader.agent-visible .stage { margin-right: 380px; } }
@media (max-width: 980px) {
  .side { position: fixed; z-index: 100; inset: 0 auto 0 0; width: min(300px, calc(100vw - 48px)); box-shadow: var(--read-shadow); }
  .catalog-backdrop { display: block; position: fixed; z-index: 90; inset: 0; border: 0; background: #00000055; }
  .topbar { padding: 0 18px; }
  .reading-paper { margin-top: 24px; }
}
@media (max-width: 600px) {
  .topbar { gap: 10px; padding: 0 12px; flex-basis: 58px; }
  .tools { gap: 5px; }
  .tools .tool-btn { padding: 0 8px; }
  .settings-label, .story-entry, .agent-entry-label { display: none; }
  .crumb > span { font-size: 11px; }
  .reading-settings { right: -40px; width: 310px; padding: 20px; }
  .reading-paper { width: calc(100% - 24px); margin: 14px auto 24px; padding: 30px 22px 48px; border-radius: 7px; }
  .chapter-title { margin: 24px 0; font-size: 1.3em; }
  .pager { gap: 8px; padding: 0 12px; flex-basis: 56px; }
  .pager-btn { padding: 0 10px; }
  .pager-info { font-size: 10px; }
}
@media (prefers-reduced-motion: reduce) { .reader *, .reader *::before, .reader *::after { scroll-behavior: auto !important; transition: none !important; } }

</style>
