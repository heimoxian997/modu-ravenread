<template>
  <div class="page detail-page" v-loading="loading">
    <template v-if="book">
      <div class="detail-top">
        <el-button text class="back-button" @click="router.push('/')">
          <el-icon><ArrowLeft /></el-icon>
          返回书库
        </el-button>
        <div class="page-header detail-header">
          <div class="title-block">
            <div class="title-line">
              <span class="book-mark">{{ book.title.slice(0, 1) }}</span>
              <div>
                <h1 class="page-title">{{ book.title }}</h1>
                <p class="book-byline">
                  {{ book.author || '作者未填写' }}
                  <span class="dot">·</span>
                  {{ formatCharCount(book.char_count) }}
                  <span class="dot">·</span>
                  {{ book.chapter_count }} 章
                </p>
              </div>
            </div>
          </div>
          <div class="header-actions">
            <el-button type="primary" @click="router.push(`/books/${bookId}/read`)">
              <el-icon><Reading /></el-icon>
              开始阅读
            </el-button>
            <el-button @click="router.push(`/books/${bookId}/storylines`)">剧情线</el-button>
            <el-button @click="router.push(`/books/${bookId}/corrections`)">纠错审核</el-button>
            <el-button @click="openEdit">
              <el-icon><Edit /></el-icon>
              编辑信息
            </el-button>
            <el-button type="danger" plain @click="removeBook">
              <el-icon><Delete /></el-icon>
              删除书籍
            </el-button>
          </div>
        </div>
      </div>

      <div class="stats-strip">
        <div>
          <span>总字数</span>
          <strong>{{ formatCharCount(book.char_count) }}</strong>
        </div>
        <div>
          <span>章节</span>
          <strong>{{ book.chapter_count }}</strong>
        </div>
        <div>
          <span>正文</span>
          <strong>{{ book.main_chapter_count }}</strong>
        </div>
        <div>
          <span>番外</span>
          <strong>{{ book.extra_chapter_count }}</strong>
        </div>
        <div>
          <span>章均字数</span>
          <strong>{{ formatNumber(book.avg_word_count) }}</strong>
        </div>
        <div>
          <span>导入时间</span>
          <strong class="date-value">{{ formatDateTime(book.create_time) }}</strong>
        </div>
      </div>

      <div class="layer-grid">
      <section class="panel chunk-summary" v-loading="chunkLoading">
        <div class="chunk-summary-main">
          <div class="chunk-icon"><el-icon><Grid /></el-icon></div>
          <div>
            <div class="chunk-title-line">
              <h2>正文切块</h2>
              <el-tag
                :type="chunkOverview.built ? 'success' : 'info'"
                size="small"
                effect="plain"
              >
                {{ chunkOverview.built ? '已切块' : '未切块' }}
              </el-tag>
              <span class="chunk-offline">本机 · 不调用模型</span>
            </div>
            <p>
              按章节把正文切成父块 / 子块，为后续四层索引准备原文偏移。
            </p>
          </div>
        </div>

        <div class="chunk-metrics">
          <div>
            <span>父块</span>
            <strong>{{ formatNumber(chunkOverview.parent_count) }}</strong>
          </div>
          <div>
            <span>子块</span>
            <strong>{{ formatNumber(chunkOverview.child_count) }}</strong>
          </div>
          <div>
            <span>覆盖章节</span>
            <strong>
              {{ chunkOverview.chapter_count }} / {{ book.main_chapter_count }}
            </strong>
          </div>
        </div>

        <div class="chunk-actions">
          <el-button
            v-if="!chunkOverview.built"
            type="primary"
            :loading="buildingChunks"
            @click="buildChunks"
          >
            <el-icon><Grid /></el-icon>
            生成切块
          </el-button>
          <template v-else>
            <el-button :loading="buildingChunks" @click="buildChunks">
              重新切块
            </el-button>
            <el-button class="detail-gold" plain @click="goChunks">
              查看切块
            </el-button>
            <el-button type="primary" @click="goIndex">
              索引与检索
            </el-button>
          </template>
        </div>
      </section>

      <section class="panel chunk-summary summary-layer" v-loading="summaryLoading">
        <div class="chunk-summary-main">
          <div class="chunk-icon"><el-icon><Document /></el-icon></div>
          <div>
            <div class="chunk-title-line">
              <h2>章节摘要</h2>
              <el-tag :type="summaryStatus.type" size="small" effect="plain">
                {{ summaryStatus.label }}
              </el-tag>
              <span class="chunk-offline">按章调用当前模型</span>
            </div>
            <p>
              展示章节摘要与完整跟读概览；已有摘要可人工编辑。
            </p>
          </div>
        </div>

        <div class="chunk-metrics">
          <div>
            <span>已生成</span>
            <strong>{{ formatNumber((summaryOverview.available_done ?? summaryOverview.done)) }}</strong>
            <small>结构化 {{ summaryOverview.done }} · 完整跟读 {{ summaryOverview.follow_done || 0 }}（按章去重）</small>
          </div>
          <div>
            <span>总章</span>
            <strong>{{ formatNumber(summaryOverview.total) }}</strong>
          </div>
          <div>
            <span>失败</span>
            <strong>{{ formatNumber(summaryOverview.failed) }}</strong>
          </div>
          <div>
            <span>下一章</span>
            <strong>{{ (summaryOverview.available_done !== undefined ? summaryOverview.next_available_number : summaryOverview.next_chapter_number) || '—' }}</strong>
          </div>
        </div>

        <div class="chunk-actions">
          <el-button type="primary" class="detail-gold" @click="goSummaries">
            查看与编辑
          </el-button>
          <el-button plain @click="goSummaries">
            继续生成
          </el-button>
        </div>
      </section>

      <section class="panel chunk-summary entity-layer" v-loading="entityLoading">
        <div class="chunk-summary-main">
          <div class="chunk-icon"><el-icon><User /></el-icon></div>
          <div>
            <div class="chunk-title-line">
              <h2>人物与世界</h2>
              <el-tag :type="entityStatusTag.type" size="small" effect="plain">
                {{ entityStatusTag.label }}
              </el-tag>
              <span class="chunk-offline">人物卡 / 世界观 · 可人工修正</span>
            </div>
            <p>
              检索人物快照与世界观事实；修正保存在独立 override 表，重跑实体层不丢失。
            </p>
          </div>
        </div>

        <div class="chunk-metrics">
          <div>
            <span>人物变化</span>
            <strong>{{ formatNumber(entityStatus.character_snapshots) }}</strong>
          </div>
          <div>
            <span>世界观</span>
            <strong>{{ formatNumber(entityStatus.lore_facts) }}</strong>
          </div>
          <div>
            <span>已索引</span>
            <strong>
              {{ entityStatus.character_indexed }} / {{ entityStatus.lore_indexed }}
            </strong>
          </div>
        </div>

        <div class="chunk-actions">
          <el-button type="primary" class="detail-gold" @click="goEntities">
            查看与编辑
          </el-button>
        </div>
      </section>
      </div>

      <div class="detail-grid">
        <main class="detail-main">
          <section class="panel metadata-panel">
            <div class="section-head">
              <h2>书籍信息</h2>
              <el-tag size="small" effect="plain">{{ book.encoding }}</el-tag>
            </div>
            <div class="metadata-grid">
              <div class="metadata-block wide">
                <span class="meta-label">简介 / 文案</span>
                <p :class="{ 'empty-text': !book.synopsis }">
                  {{ book.synopsis || '暂未填写。可在“编辑信息”中补充，后续会用于 AI 伴读。' }}
                </p>
              </div>
              <div class="metadata-block">
                <span class="meta-label">主角</span>
                <div v-if="book.protagonists?.length" class="protagonist-tags">
                  <el-tag
                    v-for="name in book.protagonists"
                    :key="name"
                    type="success"
                    effect="plain"
                  >
                    {{ name }}
                  </el-tag>
                </div>
                <p v-else class="empty-text">未填写</p>
              </div>
              <div class="metadata-block">
                <span class="meta-label">源文件</span>
                <p class="file-name">{{ book.filename }}</p>
              </div>
              <div class="metadata-block">
                <span class="meta-label">章节结构</span>
                <p>
                  正文 {{ book.main_chapter_count }} 章 / 番外
                  {{ book.extra_chapter_count }} 章
                  <span v-if="book.front_matter_chars" class="muted">
                    · 前言 {{ formatNumber(book.front_matter_chars) }} 字
                  </span>
                </p>
              </div>
            </div>
          </section>

          <section class="panel chapter-panel">
            <div class="section-head chapter-head">
              <div>
                <h2>章节</h2>
                <p class="muted">点击任意章节，在右侧查看原文片段。</p>
              </div>
              <el-pagination
                v-if="filteredChapters.length > pageSize"
                v-model:current-page="page"
                small
                background
                layout="prev, pager, next"
                :page-size="pageSize"
                :total="filteredChapters.length"
              />
            </div>

            <el-tabs v-model="tab" class="chapter-tabs" @tab-change="handleTabChange">
              <el-tab-pane :label="`正文 ${book.main_chapter_count}`" name="main">
                <ChapterList
                  :chapters="mainPageRows"
                  :selected-idx="previewChapter?.idx"
                  empty-text="没有正文章节"
                  @select="selectChapter"
                />
              </el-tab-pane>
              <el-tab-pane :label="`番外 ${book.extra_chapter_count}`" name="extra">
                <ChapterList
                  :chapters="extraPageRows"
                  :selected-idx="previewChapter?.idx"
                  empty-text="没有番外章节"
                  @select="selectChapter"
                />
              </el-tab-pane>
            </el-tabs>
          </section>
        </main>

        <aside class="preview-panel panel">
          <div v-if="previewChapter" class="preview-head">
            <div>
              <el-tag
                :type="previewChapter.kind === 'extra' ? 'warning' : 'success'"
                size="small"
                effect="plain"
              >
                {{ previewChapter.kind === 'extra' ? '番外' : '正文' }}
              </el-tag>
              <strong>{{ previewChapter.title }}</strong>
            </div>
            <span class="muted">{{ formatNumber(previewChapter.word_count) }} 字</span>
          </div>
          <div v-loading="previewLoading" class="preview-body">
            <pre v-if="previewContent" class="preview-text">{{ previewContent }}</pre>
            <el-empty
              v-else-if="!previewLoading"
              description="选择章节查看正文"
              :image-size="66"
            />
          </div>
          <div v-if="previewChapter" class="preview-foot">
            第 {{ previewChapter.idx + 1 }} 章 · 偏移
            {{ formatNumber(previewChapter.start) }} –
            {{ formatNumber(previewChapter.end) }}
          </div>
        </aside>
      </div>
    </template>

    <el-empty
      v-else-if="!loading"
      description="没有找到这本书"
      :image-size="110"
    >
      <el-button type="primary" @click="router.push('/')">返回书库</el-button>
    </el-empty>

    <el-dialog
      v-model="editVisible"
      title="编辑书籍信息"
      width="720px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <BookForm v-model="editForm" :disabled="saving" />
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">
          保存修改
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import BookForm from '@/components/BookForm.vue'
import ChapterList from '@/components/ChapterList.vue'
import {
  deleteBookApi,
  getBookApi,
  getChapterApi,
  listChaptersApi,
  updateBookApi
} from '@/api/book'
import { buildChunksApi, getChunkOverviewApi } from '@/api/chunk'
import { getEntityIndexStatusApi } from '@/api/entity'
import { getSummaryOverviewApi } from '@/api/summary'
import {
  formatCharCount,
  formatDateTime,
  formatNumber,
  parseProtagonists
} from '@/utils/text'

const route = useRoute()
const router = useRouter()
const bookId = computed(() => Number(route.params.id))

const loading = ref(false)
const book = ref(null)
const chapters = ref([])
const tab = ref('main')
const page = ref(1)
const pageSize = 100
const previewChapter = ref(null)
const previewContent = ref('')
const previewLoading = ref(false)
const editVisible = ref(false)
const saving = ref(false)
const editForm = ref({
  title: '',
  author: '',
  synopsis: '',
  protagonistsText: ''
})
const chunkOverview = ref({
  built: false,
  chapter_count: 0,
  parent_count: 0,
  child_count: 0,
  chapters: [],
  warnings: []
})
const chunkLoading = ref(false)
const buildingChunks = ref(false)
const summaryLoading = ref(false)
const summaryOverview = ref({
  total: 0,
  done: 0,
  failed: 0,
  pending: 0,
  next_chapter_number: null
})
const entityLoading = ref(false)
const entityStatus = ref({
  available: true,
  total_main: 0,
  chapters_processed: 0,
  complete: false,
  character_snapshots: 0,
  character_indexed: 0,
  character_needs_rebuild: false,
  lore_facts: 0,
  lore_indexed: 0,
  lore_needs_rebuild: false
})
let previewSeq = 0

const entityStatusTag = computed(() => {
  if (!entityStatus.value.available) return { label: '不可用', type: 'danger' }
  if (
    entityStatus.value.character_needs_rebuild ||
    entityStatus.value.lore_needs_rebuild
  ) {
    return { label: '需重建', type: 'warning' }
  }
  if (!entityStatus.value.character_indexed && !entityStatus.value.lore_indexed) {
    return { label: '未建立', type: 'info' }
  }
  if (!entityStatus.value.complete) return { label: '部分索引', type: 'warning' }
  return { label: '已建立', type: 'success' }
})

const summaryStatus = computed(() => {
  if (summaryOverview.value.failed > 0) {
    return { label: '有失败', type: 'danger' }
  }
  if (!summaryOverview.value.total) {
    return { label: '未开始', type: 'info' }
  }
  if ((summaryOverview.value.available_done ?? summaryOverview.value.done) >= summaryOverview.value.total) {
    return { label: '已生成', type: 'success' }
  }
  if ((summaryOverview.value.available_done ?? summaryOverview.value.done) > 0) {
    return { label: '进行中', type: 'warning' }
  }
  return { label: '未开始', type: 'info' }
})

const mainChapters = computed(() =>
  chapters.value.filter((chapter) => chapter.kind === 'main')
)
const extraChapters = computed(() =>
  chapters.value.filter((chapter) => chapter.kind === 'extra')
)
const filteredChapters = computed(() =>
  tab.value === 'main' ? mainChapters.value : extraChapters.value
)

function rowsFor(items) {
  const start = (page.value - 1) * pageSize
  return items.slice(start, start + pageSize)
}

const mainPageRows = computed(() => rowsFor(mainChapters.value))
const extraPageRows = computed(() => rowsFor(extraChapters.value))

watch(tab, () => {
  page.value = 1
})

function handleTabChange() {
  page.value = 1
}

async function load() {
  loading.value = true
  summaryLoading.value = true
  entityLoading.value = true
  try {
    const [detailResponse, chaptersResponse, summaryResponse, entityResponse] =
      await Promise.all([
        getBookApi(bookId.value),
        listChaptersApi(bookId.value),
        getSummaryOverviewApi(bookId.value).catch(() => ({
          data: {
            total: 0,
            done: 0,
            failed: 0,
            pending: 0,
            next_chapter_number: null
          }
        })),
        getEntityIndexStatusApi(bookId.value).catch(() => ({
          data: {
            available: true,
            total_main: 0,
            chapters_processed: 0,
            complete: false,
            character_snapshots: 0,
            character_indexed: 0,
            character_needs_rebuild: false,
            lore_facts: 0,
            lore_indexed: 0,
            lore_needs_rebuild: false
          }
        }))
      ])
    book.value = detailResponse.data
    chapters.value = chaptersResponse.data || []
    summaryOverview.value = summaryResponse.data
    entityStatus.value = {
      ...entityStatus.value,
      ...(entityResponse.data || {})
    }
    await loadChunkOverview()
    const firstMain =
      chapters.value.find((chapter) => chapter.kind === 'main') || chapters.value[0]
    if (firstMain) {
      await selectChapter(firstMain)
    }
  } catch {
    book.value = null
  } finally {
    loading.value = false
    summaryLoading.value = false
    entityLoading.value = false
  }
}

async function loadChunkOverview() {
  chunkLoading.value = true
  try {
    const response = await getChunkOverviewApi(bookId.value)
    chunkOverview.value = response.data
  } catch {
    chunkOverview.value = {
      built: false,
      chapter_count: 0,
      parent_count: 0,
      child_count: 0,
      chapters: [],
      warnings: []
    }
  } finally {
    chunkLoading.value = false
  }
}

async function buildChunks() {
  if (buildingChunks.value) return
  if (chunkOverview.value.built) {
    try {
      await ElMessageBox.confirm(
        '重新切块会替换本书现有的全部父块与子块，是否继续？',
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
  buildingChunks.value = true
  try {
    const response = await buildChunksApi(bookId.value)
    await loadChunkOverview()
    const stats = response.data
    ElMessage.success(
      `切块完成：${stats.parent_count} 个父块，${stats.child_count} 个子块`
    )
  } catch {
    // 请求层已提示
  } finally {
    buildingChunks.value = false
  }
}

function goChunks() {
  router.push(`/books/${bookId.value}/chunks`)
}

function goIndex() {
  router.push(`/books/${bookId.value}/index`)
}

function goSummaries() {
  router.push(`/books/${bookId.value}/summaries`)
}

function goEntities() {
  router.push(`/books/${bookId.value}/entities`)
}

async function selectChapter(chapter) {
  previewChapter.value = chapter
  const sequence = ++previewSeq
  previewLoading.value = true
  try {
    const response = await getChapterApi(bookId.value, chapter.idx)
    if (sequence === previewSeq) {
      previewContent.value = response.data.content || ''
    }
  } catch {
    if (sequence === previewSeq) previewContent.value = ''
  } finally {
    if (sequence === previewSeq) previewLoading.value = false
  }
}

function openEdit() {
  editForm.value = {
    title: book.value.title || '',
    author: book.value.author || '',
    synopsis: book.value.synopsis || '',
    protagonistsText: (book.value.protagonists || []).join('，')
  }
  editVisible.value = true
}

async function saveEdit() {
  const title = String(editForm.value.title || '').trim()
  if (!title) {
    ElMessage.error('书名不能为空')
    return
  }
  saving.value = true
  try {
    const response = await updateBookApi(bookId.value, {
      title,
      author: String(editForm.value.author || '').trim() || null,
      synopsis: String(editForm.value.synopsis || '').trim() || null,
      protagonists: parseProtagonists(editForm.value.protagonistsText)
    })
    book.value = response.data
    editVisible.value = false
    ElMessage.success('书籍信息已更新')
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

async function removeBook() {
  try {
    await ElMessageBox.confirm(
      `删除《${book.value.title}》后，书籍、章节和源文件都会从本机移除，且无法恢复。`,
      '确认删除书籍',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }
  try {
    await deleteBookApi(bookId.value)
    ElMessage.success('书籍已删除')
    router.replace('/')
  } catch {
    // 请求层已提示
  }
}

onMounted(load)
</script>

<style scoped>
.detail-page {
  max-width: 1440px;
}

.detail-top {
  margin-bottom: 18px;
}

.back-button {
  margin-bottom: 10px;
  color: var(--muted);
}

.detail-header {
  align-items: center;
  margin-bottom: 0;
}

.title-line {
  display: flex;
  align-items: center;
  gap: 16px;
}

.book-mark {
  display: grid;
  place-items: center;
  flex: none;
  width: 58px;
  height: 76px;
  border-radius: 5px 10px 10px 5px;
  background: linear-gradient(145deg, var(--gold), var(--gold));
  color: #241c10;
  font-family: 'STKaiti', 'KaiTi', serif;
  font-size: 28px;
  font-weight: 700;
  box-shadow: 4px 4px 0 var(--line);
}

.book-byline {
  margin-top: 7px;
  color: var(--muted);
  font-size: 12px;
}

.dot {
  margin: 0 7px;
  color: var(--muted);
}

.header-actions {
  display: flex;
  gap: 9px;
}

.stats-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin-bottom: 18px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--card);
}

.stats-strip > div {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 15px 18px;
  border-right: 1px solid var(--line);
}

.stats-strip > div:last-child {
  border-right: 0;
}

.stats-strip span {
  color: var(--muted);
  font-size: 11px;
}

.stats-strip strong {
  font-size: 17px;
}

.stats-strip .date-value {
  font-size: 12px;
  font-weight: 500;
}

.layer-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 18px;
}

.chunk-summary {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) auto auto;
  align-items: center;
  gap: 24px;
  margin-bottom: 18px;
  padding: 17px 20px;
}

.layer-grid .chunk-summary {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 14px;
  margin-bottom: 0;
  padding: 17px 18px;
}

.layer-grid .chunk-metrics {
  display: flex;
  justify-content: flex-start;
  padding: 0;
}

.layer-grid .chunk-actions {
  justify-content: flex-start;
}

.summary-layer .chunk-summary-main {
  align-items: flex-start;
}

.summary-layer .chunk-metrics strong {
  font-size: 18px;
}

.chunk-summary-main {
  display: flex;
  align-items: center;
  gap: 13px;
  min-width: 0;
}

.chunk-icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: var(--gold-soft-bg);
  color: var(--amber-deep);
}

.chunk-title-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.chunk-title-line h2 {
  font-size: 16px;
}

.chunk-offline {
  color: var(--muted);
  font-size: 10px;
}

.chunk-summary-main p {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}

.chunk-metrics {
  display: flex;
  align-items: stretch;
  gap: 22px;
  padding: 0 6px;
}

.chunk-metrics > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
  align-items: center;
}

.chunk-metrics span {
  color: var(--muted);
  font-size: 10px;
  white-space: nowrap;
}

.chunk-metrics strong {
  font-size: 16px;
  white-space: nowrap;
}

.chunk-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(350px, 0.75fr);
  gap: 18px;
  align-items: start;
}

.detail-main {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
}

.metadata-panel {
  padding: 20px 22px;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.section-head h2 {
  font-size: 17px;
}

.section-head .muted {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
}

.metadata-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 17px 24px;
}

.metadata-block.wide {
  grid-column: 1 / -1;
}

.meta-label {
  display: block;
  margin-bottom: 6px;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.06em;
}

.metadata-block p {
  color: var(--text);
  font-size: 13px;
  line-height: 1.9;
  white-space: pre-wrap;
}

.metadata-block .empty-text {
  color: var(--muted);
}

.file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.protagonist-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chapter-panel {
  padding: 20px 22px 14px;
}

.chapter-head {
  align-items: center;
  margin-bottom: 8px;
}

.chapter-tabs :deep(.el-tabs__header) {
  margin-bottom: 10px;
}

.preview-panel {
  position: sticky;
  top: 88px;
  display: flex;
  flex-direction: column;
  min-height: 540px;
  max-height: calc(100vh - 110px);
  overflow: hidden;
}

.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 17px;
  border-bottom: 1px solid var(--line);
}

.preview-head > div {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.preview-head strong {
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-head > .muted {
  flex: none;
  color: var(--muted);
  font-size: 10px;
}

.preview-body {
  flex: 1;
  min-height: 440px;
  overflow: auto;
  background: var(--card-soft);
}

.preview-text {
  margin: 0;
  padding: 22px;
  color: var(--text);
  font-family: 'Songti SC', 'SimSun', serif;
  font-size: 15px;
  line-height: 2;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.preview-foot {
  padding: 8px 17px;
  border-top: 1px solid var(--line-soft);
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
}

@media (max-width: 1180px) {
  .layer-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .chunk-summary {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .chunk-metrics {
    display: none;
  }

  .detail-grid {
    grid-template-columns: 1fr;
  }

  .preview-panel {
    position: static;
    max-height: 620px;
  }

  .stats-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .stats-strip > div:nth-child(3) {
    border-right: 0;
  }
}

@media (max-width: 900px) {
  .layer-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .chunk-summary {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .chunk-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .stats-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .stats-strip > div {
    border-bottom: 1px solid var(--line);
  }

  .detail-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .metadata-grid {
    grid-template-columns: 1fr;
  }
}
</style>
