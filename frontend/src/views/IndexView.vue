<template>
  <div class="page index-page" v-loading="loading">
    <template v-if="book">
      <div class="detail-top">
        <el-button text class="back-button" @click="goDetail">
          <el-icon><ArrowLeft /></el-icon>
          返回书籍详情
        </el-button>

        <div class="page-header index-header">
          <div>
            <div class="eyebrow">索引 · 文本向量</div>
            <h1 class="page-title">{{ book.title }}</h1>
            <p class="page-subtitle">
              使用本机 bge-m3 为正文子块生成 dense + sparse 向量，并在本地 Qdrant 中检索。
            </p>
          </div>
          <div class="header-actions">
            <el-tag :type="statusMeta.type" effect="plain" round>
              {{ statusMeta.label }}
            </el-tag>
            <el-button class="detail-gold" @click="goChunks">
              <el-icon><Grid /></el-icon>
              查看切块
            </el-button>
          </div>
        </div>
      </div>

      <el-alert
        class="info-tip"
        type="info"
        :closable="false"
        show-icon
        title="本地生成与检索"
        description="不调用 LLM，不上传书稿。首次检索会加载本地模型，可能需要 10–15 秒。"
      />

      <el-alert
        v-if="!chunkOverview.built"
        class="section-alert"
        type="warning"
        :closable="false"
        show-icon
        title="请先生成正文切块"
        description="向量索引基于章节子块建立，当前还没有可用于建库的子块。"
      >
        <template #default>
          <el-button size="small" type="warning" plain @click="goChunks">
            去生成切块
          </el-button>
        </template>
      </el-alert>

      <el-alert
        v-if="!status.available"
        class="section-alert"
        type="error"
        :closable="false"
        show-icon
        title="本地 Qdrant 不可用"
        :description="status.error || '请确认 data/qdrant 没有被其他进程占用。'"
      />

      <el-alert
        v-if="status.warning"
        class="section-alert"
        type="warning"
        :closable="false"
        show-icon
        :title="status.warning"
      />

      <el-alert
        v-if="status.job?.status === 'failed'"
        class="section-alert"
        type="error"
        :closable="false"
        show-icon
        title="上次建库失败"
        :description="status.job.error || status.job.message || '请重新生成向量。'"
      />

      <el-alert
        v-if="status.job?.status === 'interrupted'"
        class="section-alert"
        type="warning"
        :closable="false"
        show-icon
        title="上次任务被中断"
        description="服务重启或进程退出导致任务中断，可以重新生成向量。"
      />

      <section class="panel status-panel">
        <div class="panel-head">
          <div>
            <h2>向量索引</h2>
            <p>书内检索只使用当前书籍的向量点，不跨书混检。</p>
          </div>
          <span class="status-caption">
            {{
              !status.available
                ? '不可用'
                : isBuilding
                  ? '构建中'
                  : knownBuilt
                    ? '可检索'
                    : '尚未建立'
            }}
          </span>
        </div>

        <div class="metric-grid">
          <div class="metric-card">
            <span>向量点数</span>
            <strong>{{ formatNumber(status.point_count) }}</strong>
            <small>
              {{ isBuilding ? '正在写入' : status.built ? '已写入 Qdrant' : '等待生成' }}
            </small>
          </div>
          <div class="metric-card">
            <span>向量维度</span>
            <strong>{{ status.dim || '—' }}</strong>
            <small>dense 1024 维</small>
          </div>
          <div class="metric-card">
            <span>嵌入模型</span>
            <strong class="model-name">{{ status.model || 'BAAI/bge-m3' }}</strong>
            <small class="embedding-note">本地权重</small>
          </div>
          <div class="metric-card">
            <span>运行设备</span>
            <strong>{{ deviceLabel }}</strong>
            <small>{{ status.device === 'cuda' ? 'GPU 加速' : 'CPU 模式' }}</small>
          </div>
        </div>

        <div class="path-row">
          <span><b>collection</b>{{ status.collection || '—' }}</span>
          <span><b>Qdrant</b>{{ status.qdrant_path || '—' }}</span>
        </div>

        <div class="index-actions">
          <el-button
            type="primary"
            :loading="startingIndex"
            :disabled="!canBuild"
            @click="startIndex"
          >
            <el-icon><Refresh /></el-icon>
            {{ knownBuilt ? '重新生成' : '生成向量' }}
          </el-button>
          <el-button
            v-if="knownBuilt"
            type="danger"
            plain
            :loading="deletingIndex"
            :disabled="!canDelete"
            @click="deleteIndex"
          >
            <el-icon><Delete /></el-icon>
            删除索引
          </el-button>
          <span v-if="buildDisabledReason" class="disabled-reason">
            {{ buildDisabledReason }}
          </span>
        </div>

        <div v-if="isBuilding" class="progress-panel">
          <div class="progress-head">
            <div>
              <span class="progress-stage">{{ stageLabel }}</span>
              <span class="muted">{{ status.job?.message || '正在准备…' }}</span>
            </div>
            <strong>{{ progressPercent }}%</strong>
          </div>
          <el-progress
            :percentage="progressPercent"
            :show-text="false"
            :stroke-width="9"
            :status="status.job?.status === 'failed' ? 'exception' : undefined"
          />
        </div>
        <div v-else-if="status.job?.status === 'done'" class="last-run">
          上次任务完成于 {{ status.job.finish_time || '—' }}
          <span class="dot">·</span>
          {{ formatNumber(status.job.point_count) }} 个向量点
        </div>
      </section>

      <section class="panel search-panel">
        <div class="panel-head">
          <div>
            <h2>检索试跑</h2>
            <p>dense + sparse 各召回 20 条，RRF 融合后取 Top-K。</p>
          </div>
          <el-tag v-if="hasSearched" size="small" effect="plain">
            {{ searching ? '检索中' : `${results.length} 条结果` }}
          </el-tag>
        </div>

        <div class="search-bar">
          <el-input
            v-model="query"
            size="large"
            clearable
            placeholder="输入一个关于这本书的问题，例如：林晚是谁"
            :disabled="!status.available || !status.built || isBuilding"
            @keyup.enter="handleSearch"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
          <el-select v-model="topK" size="large" :disabled="isBuilding" style="width: 110px">
            <el-option :value="5" label="Top 5" />
            <el-option :value="10" label="Top 10" />
            <el-option :value="20" label="Top 20" />
          </el-select>
          <el-button
            type="primary"
            size="large"
            :loading="searching"
            :disabled="!canSearch"
            @click="handleSearch"
          >
            检索
          </el-button>
        </div>
        <div class="search-hint">
          首次检索会加载本地模型，可能稍慢。
          <span v-if="searchDisabledReason" class="disabled-inline">
            {{ searchDisabledReason }}
          </span>
        </div>

        <div v-loading="searching" class="result-wrap">
          <el-empty
            v-if="hasSearched && !searching && !results.length"
            description="没有命中"
            :image-size="72"
          />
          <div v-else-if="!hasSearched" class="search-placeholder">
            <el-icon :size="28"><Search /></el-icon>
            <span>输入问题后按回车或点击“检索”</span>
          </div>

          <div v-else class="result-list">
            <article
              v-for="(item, index) in results"
              :key="item.chunk_id"
              class="result-card"
            >
              <div class="result-head">
                <div class="result-heading">
                  <span class="rank">{{ index + 1 }}</span>
                  <div>
                    <strong>{{ item.chapter_title || '未命名章节' }}</strong>
                    <span class="chapter-no">第 {{ item.chapter_idx + 1 }} 章</span>
                  </div>
                </div>
                <el-button text type="primary" @click="goChapter(item)">
                  查看该章切块
                </el-button>
              </div>

              <div class="score-row">
                <span class="score score-rrf">
                  RRF <b>{{ item.rrf_score.toFixed(4) }}</b>
                </span>
                <span class="score">
                  dense <b>{{ item.dense_score.toFixed(4) }}</b>
                </span>
                <span class="score">
                  sparse <b>{{ item.sparse_score.toFixed(4) }}</b>
                </span>
                <span class="offset">
                  {{ formatNumber(item.char_start) }}–{{ formatNumber(item.char_end) }}
                </span>
              </div>

              <p class="result-text">{{ cleanText(item.text) }}</p>

              <div class="result-foot">
                <el-button
                  v-if="item.parent_text"
                  text
                  size="small"
                  @click="toggleParent(item.chunk_id)"
                >
                  {{ expandedParents[item.chunk_id] ? '收起父块' : '展开父块' }}
                </el-button>
              </div>

              <pre
                v-if="expandedParents[item.chunk_id] && item.parent_text"
                class="parent-text"
              >{{ cleanText(item.parent_text) }}</pre>
            </article>
          </div>
        </div>
      </section>
    </template>

    <div v-else-if="!loading" class="panel empty-panel">
      <el-empty description="没有找到这本书" :image-size="100">
        <el-button type="primary" @click="router.push('/')">返回书库</el-button>
      </el-empty>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getBookApi } from '@/api/book'
import { getChunkOverviewApi } from '@/api/chunk'
import {
  deleteTextIndexApi,
  getTextIndexStatusApi,
  searchTextIndexApi,
  startTextIndexApi
} from '@/api/textIndex'
import { formatNumber } from '@/utils/text'

const route = useRoute()
const router = useRouter()
const bookId = computed(() => Number(route.params.id))

const book = ref(null)
const loading = ref(false)
const startingIndex = ref(false)
const deletingIndex = ref(false)
const searching = ref(false)
const hasSearched = ref(false)
const query = ref('')
const topK = ref(5)
const results = ref([])
const expandedParents = ref({})
const status = ref(emptyStatus())
const knownBuilt = ref(false)
const chunkOverview = ref({ built: false, child_count: 0, parent_count: 0, chapters: [] })

let pollTimer = null
let pollInFlight = false
let disposed = false
let searchSeq = 0

const STAGE_LABELS = {
  preparing: '准备',
  loading: '加载模型',
  encoding: '编码子块',
  upserting: '写入向量库',
  done: '完成'
}

function emptyStatus() {
  return {
    built: false,
    available: true,
    point_count: 0,
    dim: 0,
    collection: '',
    qdrant_path: '',
    model: '',
    device: '',
    busy: false,
    job: null,
    warning: null,
    error: ''
  }
}

const currentJob = computed(() => status.value.job || null)
const isBuilding = computed(
  () =>
    status.value.busy ||
    ['queued', 'running'].includes(currentJob.value?.status)
)
const stageLabel = computed(
  () => STAGE_LABELS[currentJob.value?.stage] || '准备'
)
const progressPercent = computed(() =>
  Math.max(0, Math.min(100, Number(currentJob.value?.progress || 0)))
)
const deviceLabel = computed(() => {
  if (status.value.device === 'cuda') return 'CUDA'
  if (status.value.device === 'cpu') return 'CPU'
  return status.value.device || '—'
})

const statusMeta = computed(() => {
  if (!status.value.available) return { label: '不可用', type: 'danger' }
  if (isBuilding.value) {
    return currentJob.value?.status === 'queued'
      ? { label: '排队中', type: 'info' }
      : { label: '构建中', type: 'warning' }
  }
  if (currentJob.value?.status === 'failed') {
    return { label: '建库失败', type: 'danger' }
  }
  if (currentJob.value?.status === 'interrupted') {
    return { label: '已中断', type: 'warning' }
  }
  return status.value.built
    ? { label: '已建立', type: 'success' }
    : { label: '未建立', type: 'info' }
})

const canBuild = computed(
  () =>
    status.value.available &&
    chunkOverview.value.built &&
    !isBuilding.value &&
    !startingIndex.value &&
    !loading.value
)
const canDelete = computed(
  () =>
    status.value.available &&
    status.value.built &&
    !isBuilding.value &&
    !deletingIndex.value
)
const canSearch = computed(
  () =>
    status.value.available &&
    status.value.built &&
    !isBuilding.value &&
    Boolean(query.value.trim()) &&
    !searching.value
)
const buildDisabledReason = computed(() => {
  if (!status.value.available) return '本地 Qdrant 不可用，暂不能建库。'
  if (!chunkOverview.value.built) return '请先生成正文切块。'
  if (isBuilding.value) return '索引任务进行中，请等待完成。'
  return ''
})
const searchDisabledReason = computed(() => {
  if (!status.value.available) return '本地 Qdrant 不可用。'
  if (!chunkOverview.value.built) return '请先生成正文切块。'
  if (isBuilding.value) return '建库期间不能检索。'
  if (!status.value.built) return '请先生成向量索引。'
  return ''
})

async function load() {
  loading.value = true
  try {
    const [bookResponse, statusResponse, chunkResponse] = await Promise.all([
      getBookApi(bookId.value),
      getTextIndexStatusApi(bookId.value),
      getChunkOverviewApi(bookId.value)
    ])
    book.value = bookResponse.data
    status.value = statusResponse.data
    knownBuilt.value = Boolean(status.value.built)
    chunkOverview.value = chunkResponse.data
    if (isBuilding.value) schedulePolling()
  } catch {
    book.value = null
  } finally {
    loading.value = false
  }
}

function clearPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePolling() {
  clearPolling()
  if (disposed || !isBuilding.value) return
  pollTimer = setTimeout(pollStatus, 1000)
}

function applyStatus(nextStatus, notify = false) {
  const previous = status.value.job?.status
  const nextBuilding =
    nextStatus.busy ||
    ['queued', 'running'].includes(nextStatus.job?.status)
  status.value = nextStatus
  if (nextBuilding) {
    if (nextStatus.built) knownBuilt.value = true
  } else {
    knownBuilt.value = Boolean(nextStatus.built)
  }
  if (
    notify &&
    ['queued', 'running'].includes(previous) &&
    status.value.job?.status === 'done'
  ) {
    ElMessage.success('建库完成')
  }
}

async function pollStatus() {
  pollTimer = null
  if (pollInFlight) {
    schedulePolling()
    return
  }
  pollInFlight = true
  try {
    const response = await getTextIndexStatusApi(bookId.value, true)
    applyStatus(response.data, true)
  } catch {
    // 轮询失败静默，下一轮继续
  } finally {
    pollInFlight = false
  }
  if (!disposed && isBuilding.value) schedulePolling()
}

async function refreshStatus(silent = true) {
  const response = await getTextIndexStatusApi(bookId.value, silent)
  applyStatus(response.data, true)
  return response.data
}

async function startIndex() {
  if (!canBuild.value) return
  if (knownBuilt.value) {
    try {
      await ElMessageBox.confirm(
        '重新生成会替换这本书现有的全部向量点，是否继续？',
        '重新生成向量',
        {
          type: 'warning',
          confirmButtonText: '重新生成',
          cancelButtonText: '取消'
        }
      )
    } catch {
      return
    }
  }

  startingIndex.value = true
  try {
    await startTextIndexApi(bookId.value)
    await refreshStatus(true)
    ElMessage.success(knownBuilt.value ? '重建任务已启动' : '建库任务已启动')
    schedulePolling()
  } catch {
    try {
      await refreshStatus(true)
    } catch {
      // 请求层已经提示
    }
    schedulePolling()
  } finally {
    startingIndex.value = false
  }
}

async function deleteIndex() {
  if (!canDelete.value) return
  try {
    await ElMessageBox.confirm(
      '只会删除这本书的向量索引，不会影响书籍、章节和正文切块。',
      '删除向量索引',
      {
        type: 'warning',
        confirmButtonText: '删除索引',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }

  deletingIndex.value = true
  try {
    const response = await deleteTextIndexApi(bookId.value)
    clearPolling()
    results.value = []
    hasSearched.value = false
    expandedParents.value = {}
    await refreshStatus(true)
    ElMessage.success(`已删除 ${response.data.deleted} 个向量点`)
  } catch {
    try {
      await refreshStatus(true)
    } catch {
      // 请求层已经提示
    }
  } finally {
    deletingIndex.value = false
  }
}

async function handleSearch() {
  if (!canSearch.value) {
    if (!query.value.trim()) return
    return
  }
  const sequence = ++searchSeq
  searching.value = true
  hasSearched.value = true
  expandedParents.value = {}
  try {
    const response = await searchTextIndexApi(bookId.value, {
      query: query.value.trim(),
      top_k: topK.value
    })
    if (sequence === searchSeq) {
      results.value = response.data || []
    }
  } catch {
    if (sequence === searchSeq) {
      results.value = []
      try {
        await refreshStatus(true)
      } catch {
        // 请求层已经提示
      }
    }
  } finally {
    if (sequence === searchSeq) {
      searching.value = false
    }
  }
}

function cleanText(value) {
  return String(value || '')
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function toggleParent(chunkId) {
  expandedParents.value = {
    ...expandedParents.value,
    [chunkId]: !expandedParents.value[chunkId]
  }
}

function goDetail() {
  router.push(`/books/${bookId.value}`)
}

function goChunks() {
  router.push(`/books/${bookId.value}/chunks`)
}

function goChapter(item) {
  router.push({
    path: `/books/${bookId.value}/chunks`,
    query: { chapter: item.chapter_idx }
  })
}

onMounted(load)
onUnmounted(() => {
  disposed = true
  clearPolling()
})
</script>

<style scoped>
.index-page {
  max-width: 1320px;
}

.detail-top {
  margin-bottom: 16px;
}

.back-button {
  margin-bottom: 10px;
  color: var(--muted);
}

.index-header {
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
  align-items: center;
  gap: 9px;
}

.info-tip,
.section-alert {
  margin-bottom: 12px;
}

.section-alert :deep(.el-alert__description) {
  margin-bottom: 5px;
}

.status-panel,
.search-panel {
  padding: 20px 22px;
}

.status-panel {
  margin-bottom: 16px;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
}

.panel-head h2 {
  font-size: 17px;
}

.panel-head p {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.7;
}

.status-caption {
  color: var(--muted);
  font-size: 11px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.metric-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: var(--card);
}

.metric-card > span {
  color: var(--muted);
  font-size: 11px;
}

.metric-card strong {
  overflow: hidden;
  font-size: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-card small {
  color: var(--muted);
  font-size: 10px;
}

.metric-card .model-name {
  color: var(--green-soft);
  font-size: 15px;
}

.metric-card .embedding-note {
  color: #7fb3a5;
}

.path-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 24px;
  margin-top: 13px;
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
}

.path-row b {
  margin-right: 6px;
  color: var(--muted);
  font-family: 'Microsoft YaHei', sans-serif;
  font-weight: 400;
}

.index-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  margin-top: 16px;
}

.disabled-reason {
  color: var(--muted);
  font-size: 11px;
}

.progress-panel {
  margin-top: 17px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: var(--card-soft);
}

.progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 9px;
}

.progress-head > div {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.progress-stage {
  color: var(--amber-deep);
  font-size: 12px;
  font-weight: 700;
}

.progress-head .muted {
  overflow: hidden;
  color: var(--muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-head strong {
  color: var(--gold);
  font-size: 14px;
}

.last-run {
  margin-top: 14px;
  color: var(--muted);
  font-size: 11px;
}

.dot {
  margin: 0 6px;
  color: var(--muted);
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 9px;
}

.search-hint {
  margin-top: 8px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}

.disabled-inline {
  margin-left: 7px;
  color: var(--gold);
}

.result-wrap {
  min-height: 150px;
  margin-top: 16px;
}

.search-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  min-height: 150px;
  color: var(--muted);
  font-size: 12px;
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 720px;
  overflow: auto;
  padding-right: 3px;
}

.result-card {
  padding: 15px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--card);
}

.result-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.result-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.rank {
  display: grid;
  place-items: center;
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--gold-soft-bg);
  color: var(--amber-deep);
  font-size: 12px;
  font-weight: 700;
}

.result-heading > div {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
}

.result-heading strong {
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chapter-no {
  color: var(--muted);
  font-size: 10px;
}

.score-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.score {
  padding: 3px 7px;
  border-radius: 6px;
  background: var(--card-soft);
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
}

.score b {
  color: var(--muted);
  font-weight: 600;
}

.score-rrf {
  background: var(--gold-soft-bg);
  color: var(--gold);
}

.offset {
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
}

.result-text {
  display: -webkit-box;
  margin-top: 10px;
  overflow: hidden;
  color: var(--text);
  font-family: 'Songti SC', 'SimSun', serif;
  font-size: 14px;
  line-height: 1.85;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.result-foot {
  display: flex;
  justify-content: flex-end;
  min-height: 28px;
  margin-top: 4px;
}

.parent-text {
  max-height: 240px;
  margin: 0;
  overflow: auto;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--card-soft);
  color: var(--text);
  font-family: 'Songti SC', 'SimSun', serif;
  font-size: 13px;
  line-height: 1.9;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.empty-panel {
  min-height: 260px;
}

@media (max-width: 900px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .index-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .search-bar {
    flex-wrap: wrap;
  }

  .search-bar :deep(.el-input) {
    flex: 1 1 100%;
  }
}

@media (max-width: 600px) {
  .status-panel,
  .search-panel {
    padding: 16px;
  }

  .metric-grid {
    grid-template-columns: 1fr;
  }

  .result-head {
    flex-direction: column;
  }

  .result-list {
    max-height: none;
  }
}
</style>
