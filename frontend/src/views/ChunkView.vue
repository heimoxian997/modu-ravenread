<template>
  <div class="page chunk-page" v-loading="loading">
    <div class="detail-top">
      <el-button text class="back-button" @click="goDetail">
        <el-icon><ArrowLeft /></el-icon>
        返回书籍详情
      </el-button>

      <div class="page-header chunk-header">
        <div>
          <div class="eyebrow">四层索引 · 准备</div>
          <h1 class="page-title">{{ book?.title || '正文切块' }}</h1>
          <p class="page-subtitle">
            父块按段落聚合，子块在父块内按句界切分并保留重叠。所有内容只在本机处理。
          </p>
        </div>
        <div class="header-actions">
          <template v-if="!overview.built">
            <el-button type="primary" :loading="building" @click="buildChunks">
              <el-icon><Grid /></el-icon>
              生成切块
            </el-button>
          </template>
          <template v-else>
            <el-button type="primary" plain @click="goIndex">
              <el-icon><DataLine /></el-icon>
              向量索引
            </el-button>
            <el-button :loading="building" @click="buildChunks">
              <el-icon><Refresh /></el-icon>
              重新切块
            </el-button>
            <el-button
              type="danger"
              plain
              :loading="clearing"
              @click="clearChunks"
            >
              <el-icon><Delete /></el-icon>
              清除切块
            </el-button>
          </template>
        </div>
      </div>
    </div>

    <el-alert
      class="offline-tip"
      type="info"
      :closable="false"
      show-icon
      title="当前只做本地正文切块"
      description="本页不生成嵌入、不写向量库、不调用任何模型，也不会联网。"
    />

    <div class="chunk-stats">
      <div class="chunk-stat-card">
        <span>父块</span>
        <strong>{{ formatNumber(overview.parent_count) }}</strong>
        <small>父级块</small>
      </div>
      <div class="chunk-stat-card">
        <span>子块</span>
        <strong>{{ formatNumber(overview.child_count) }}</strong>
        <small>子级块</small>
      </div>
      <div class="chunk-stat-card">
        <span>覆盖章节</span>
        <strong>{{ overview.chapter_count }}</strong>
        <small>正文 {{ overview.chapters.length }} 章</small>
      </div>
      <div class="chunk-stat-card">
        <span>子块平均</span>
        <strong>{{ formatNumber(overview.avg_child_chars) }}</strong>
        <small>字 / 块</small>
      </div>
      <div class="chunk-stat-card">
        <span>最长子块</span>
        <strong>{{ formatNumber(overview.max_child_chars) }}</strong>
        <small>上限 600 字</small>
      </div>
    </div>

    <div v-if="overview.warnings.length" class="chunk-warnings">
      <el-alert
        v-for="warning in overview.warnings"
        :key="`${warning.chapter_idx}-${warning.code}`"
        type="warning"
        :closable="false"
        show-icon
        :title="
          warning.chapter_idx === null
            ? warning.message
            : `第 ${warning.chapter_idx + 1} 章：${warning.message}`
        "
      />
    </div>

    <div v-if="!overview.built && !loading" class="panel empty-panel">
      <el-empty description="这本书还没有正文切块" :image-size="110">
        <p class="empty-copy">
          生成后会按章节建立父块和子块，只写入本机 SQLite，不改动原书与章节表。
        </p>
        <el-button type="primary" :loading="building" @click="buildChunks">
          生成切块
        </el-button>
      </el-empty>
    </div>

    <div v-else-if="overview.built" class="chunk-layout">
      <aside class="panel chapter-side">
        <div class="side-head">
          <div>
            <h2>正文章节</h2>
            <p>{{ overview.chapters.length }} 章</p>
          </div>
          <el-tag size="small" effect="plain">已切 {{ overview.chapter_count }}</el-tag>
        </div>

        <div class="chapter-scroll">
          <button
            v-for="chapter in overview.chapters"
            :key="chapter.idx"
            type="button"
            class="chapter-item"
            :class="{ 'is-selected': chapter.idx === selectedIdx }"
            @click="selectChapter(chapter.idx)"
          >
            <span class="chapter-order">{{ String(chapter.idx + 1).padStart(2, '0') }}</span>
            <span class="chapter-info">
              <span class="chapter-title">{{ chapter.title }}</span>
              <span class="chapter-counts">
                父 {{ chapter.parent_count }} · 子 {{ chapter.child_count }}
              </span>
            </span>
          </button>
          <div v-if="!overview.chapters.length" class="chapter-empty">
            没有正文章节
          </div>
        </div>
      </aside>

      <main class="chunk-main">
        <div class="panel content-head">
          <div>
            <div class="content-title-line">
              <h2>{{ chapterChunks.title || selectedChapter?.title || '选择章节' }}</h2>
              <el-tag v-if="selectedChapter" size="small" effect="plain">
                第 {{ selectedChapter.idx + 1 }} 章
              </el-tag>
            </div>
            <p>
              父块 {{ selectedChapter?.parent_count || 0 }} 个 · 子块
              {{ selectedChapter?.child_count || 0 }} 个
            </p>
          </div>
          <span class="muted">点击子块标题展开原文</span>
        </div>

        <div v-loading="chapterLoading" class="parent-list">
          <div v-if="!chapterChunks.parents.length && !chapterLoading" class="panel empty-panel">
            <el-empty description="该章没有切块，可能是空章" :image-size="82" />
          </div>

          <article
            v-for="parent in chapterChunks.parents"
            :key="parent.id"
            class="panel parent-card"
          >
            <div class="parent-head">
              <div class="parent-title">
                <el-tag type="success" effect="plain">父块 #{{ parent.seq }}</el-tag>
                <strong>父块 {{ parent.seq + 1 }}</strong>
                <span class="muted">
                  {{ formatNumber(parent.char_end - parent.char_start) }} 字
                </span>
              </div>
              <span class="mono offset">
                {{ formatNumber(parent.char_start) }} – {{ formatNumber(parent.char_end) }}
              </span>
            </div>

            <el-collapse v-model="openChildren" class="child-collapse">
              <el-collapse-item
                v-for="child in parent.children"
                :key="child.id"
                :name="`${parent.id}-${child.id}`"
              >
                <template #title>
                  <div class="child-title">
                    <el-tag size="small" type="warning" effect="plain">
                      子块 #{{ child.seq }}
                    </el-tag>
                    <span>{{ formatNumber(child.char_end - child.char_start) }} 字</span>
                    <span class="mono child-offset">
                      {{ formatNumber(child.char_start) }} – {{ formatNumber(child.char_end) }}
                    </span>
                  </div>
                </template>
                <pre class="child-text">{{ child.text }}</pre>
              </el-collapse-item>
            </el-collapse>
          </article>
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getBookApi } from '@/api/book'
import {
  buildChunksApi,
  clearChunksApi,
  getChapterChunksApi,
  getChunkOverviewApi
} from '@/api/chunk'
import { formatNumber } from '@/utils/text'

const route = useRoute()
const router = useRouter()
const bookId = computed(() => Number(route.params.id))

const book = ref(null)
const loading = ref(false)
const building = ref(false)
const clearing = ref(false)
const chapterLoading = ref(false)
const selectedIdx = ref(null)
const openChildren = ref([])
const overview = ref(emptyOverview())
const chapterChunks = ref(emptyChapterChunks())
let chapterSeq = 0

const selectedChapter = computed(() =>
  overview.value.chapters.find((chapter) => chapter.idx === selectedIdx.value)
)

function emptyOverview() {
  return {
    built: false,
    chapter_count: 0,
    parent_count: 0,
    child_count: 0,
    avg_child_chars: 0,
    max_child_chars: 0,
    min_child_chars: 0,
    chapters: [],
    warnings: []
  }
}

function emptyChapterChunks() {
  return {
    chapter_idx: null,
    title: '',
    parents: []
  }
}

async function load() {
  loading.value = true
  try {
    const [bookResponse, overviewResponse] = await Promise.all([
      getBookApi(bookId.value),
      getChunkOverviewApi(bookId.value)
    ])
    book.value = bookResponse.data
    overview.value = overviewResponse.data

    if (overview.value.chapters.length) {
      const requestedIndex = Number(route.query.chapter)
      const requested = Number.isInteger(requestedIndex)
        ? overview.value.chapters.find((chapter) => chapter.idx === requestedIndex)
        : null
      const current = overview.value.chapters.find(
        (chapter) => chapter.idx === selectedIdx.value
      )
      selectedIdx.value =
        requested?.idx ?? current?.idx ?? overview.value.chapters[0].idx
      if (overview.value.built) {
        await loadChapter(selectedIdx.value)
      }
    }
  } catch {
    book.value = null
  } finally {
    loading.value = false
  }
}

async function refreshOverview() {
  const response = await getChunkOverviewApi(bookId.value)
  overview.value = response.data
  if (!overview.value.chapters.length) {
    selectedIdx.value = null
    chapterChunks.value = emptyChapterChunks()
    return
  }
  const current = overview.value.chapters.find(
    (chapter) => chapter.idx === selectedIdx.value
  )
  selectedIdx.value = current?.idx ?? overview.value.chapters[0].idx
  if (overview.value.built) {
    await loadChapter(selectedIdx.value)
  } else {
    chapterChunks.value = emptyChapterChunks()
  }
}

async function selectChapter(idx) {
  if (idx === selectedIdx.value) return
  selectedIdx.value = idx
  await loadChapter(idx)
}

async function loadChapter(idx) {
  const sequence = ++chapterSeq
  chapterLoading.value = true
  openChildren.value = []
  try {
    const response = await getChapterChunksApi(bookId.value, idx)
    if (sequence === chapterSeq) {
      chapterChunks.value = response.data
    }
  } catch {
    if (sequence === chapterSeq) {
      chapterChunks.value = {
        ...emptyChapterChunks(),
        chapter_idx: idx,
        title: selectedChapter.value?.title || ''
      }
    }
  } finally {
    if (sequence === chapterSeq) {
      chapterLoading.value = false
    }
  }
}

async function buildChunks() {
  if (building.value) return
  if (overview.value.built) {
    try {
      await ElMessageBox.confirm(
        '重新切块会替换本书现有全部父块与子块，是否继续？',
        '重新生成切块',
        {
          type: 'warning',
          confirmButtonText: '重新切块',
          cancelButtonText: '取消'
        }
      )
    } catch {
      return
    }
  }
  building.value = true
  try {
    const response = await buildChunksApi(bookId.value)
    await refreshOverview()
    ElMessage.success(
      `切块完成：${response.data.parent_count} 个父块，${response.data.child_count} 个子块`
    )
  } catch {
    // 请求层已提示
  } finally {
    building.value = false
  }
}

async function clearChunks() {
  if (clearing.value) return
  try {
    await ElMessageBox.confirm(
      '清除后本书全部父块、子块会从本机数据库删除，但书籍和章节不会受影响。',
      '确认清除切块',
      {
        type: 'warning',
        confirmButtonText: '清除切块',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }
  clearing.value = true
  try {
    const response = await clearChunksApi(bookId.value)
    await refreshOverview()
    ElMessage.success(`已清除 ${response.data.deleted} 条切块记录`)
  } catch {
    // 请求层已提示
  } finally {
    clearing.value = false
  }
}

function goDetail() {
  router.push(`/books/${bookId.value}`)
}

function goIndex() {
  router.push(`/books/${bookId.value}/index`)
}

onMounted(load)
</script>

<style scoped>
.chunk-page {
  max-width: 1440px;
}

.detail-top {
  margin-bottom: 18px;
}

.back-button {
  margin-bottom: 10px;
  color: var(--muted);
}

.chunk-header {
  align-items: center;
  margin-bottom: 0;
}

.eyebrow {
  margin-bottom: 7px;
  color: var(--amber);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
}

.header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
}

.offline-tip {
  margin-bottom: 14px;
}

.chunk-stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.chunk-stat-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 15px 18px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: var(--card);
}

.chunk-stat-card > span {
  color: var(--muted);
  font-size: 11px;
}

.chunk-stat-card strong {
  font-size: 21px;
}

.chunk-stat-card small {
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.05em;
}

.chunk-warnings {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}

.empty-panel {
  min-height: 280px;
}

.empty-copy {
  max-width: 520px;
  margin: -4px auto 18px;
  color: var(--muted);
  line-height: 1.8;
}

.chunk-layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.chapter-side {
  position: sticky;
  top: 84px;
  overflow: hidden;
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--line);
}

.side-head h2 {
  font-size: 15px;
}

.side-head p {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.chapter-scroll {
  max-height: calc(100vh - 210px);
  overflow: auto;
  padding: 6px;
}

.chapter-item {
  display: flex;
  gap: 10px;
  width: 100%;
  padding: 10px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.chapter-item:hover {
  background: var(--card-soft);
}

.chapter-item.is-selected {
  background: var(--green-soft-bg);
}

.chapter-order {
  flex: none;
  width: 24px;
  padding-top: 2px;
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
}

.chapter-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.chapter-title {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chapter-counts {
  color: var(--muted);
  font-size: 10px;
}

.chapter-empty {
  padding: 24px;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}

.chunk-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.content-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
}

.content-title-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
}

.content-title-line h2 {
  font-size: 17px;
}

.content-head p {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
}

.content-head > .muted {
  color: var(--muted);
  font-size: 11px;
}

.parent-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 240px;
}

.parent-card {
  overflow: hidden;
}

.parent-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 17px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--card-soft);
}

.parent-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.parent-title strong {
  font-size: 13px;
}

.parent-title .muted {
  color: var(--muted);
  font-size: 10px;
}

.offset {
  color: var(--muted);
  font-size: 10px;
  white-space: nowrap;
}

.child-collapse {
  --el-collapse-header-bg-color: transparent;
  --el-collapse-content-bg-color: transparent;
  border-top: 0;
  border-bottom: 0;
  padding: 0 8px 4px;
}

.child-collapse :deep(.el-collapse-item__header) {
  min-height: 44px;
  height: auto;
  padding: 7px 9px;
  border-bottom-color: var(--line-soft);
  line-height: 1.5;
}

.child-collapse :deep(.el-collapse-item__wrap) {
  border-bottom-color: var(--line-soft);
}

.child-collapse :deep(.el-collapse-item__content) {
  padding: 0 12px 14px;
}

.child-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  padding-right: 10px;
  font-size: 11px;
}

.child-title > span:not(.el-tag) {
  color: var(--muted);
}

.child-offset {
  color: var(--muted) !important;
  font-size: 10px;
}

.child-text {
  max-height: 320px;
  margin: 0;
  overflow: auto;
  padding: 14px 16px;
  border-radius: 8px;
  background: var(--card-soft);
  color: var(--text);
  font-family: 'Songti SC', 'SimSun', serif;
  font-size: 14px;
  line-height: 1.95;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 1100px) {
  .chunk-stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .chunk-layout {
    grid-template-columns: 260px minmax(0, 1fr);
  }
}

@media (max-width: 820px) {
  .chunk-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .chunk-layout {
    grid-template-columns: 1fr;
  }

  .chapter-side {
    position: static;
  }

  .chapter-scroll {
    max-height: 280px;
  }

  .chunk-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
