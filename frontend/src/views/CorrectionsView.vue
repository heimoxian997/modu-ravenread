<template>
  <div class="page corrections-page">
    <el-button text @click="router.push(`/books/${bookId}`)">← 返回书籍</el-button>
    <header class="review-header">
      <div>
        <p class="eyebrow">伴读发现的问题，由你确认</p>
        <h1>纠错审核</h1>
        <p class="muted">对照原文，确认修改后再应用。笔记由伴读直接修订；这里审核基础摘要、人物与世界观。</p>
      </div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="review-filter">
      <el-radio-group v-model="filter" @change="changeFilter">
        <el-radio-button value="pending">待处理</el-radio-button>
        <el-radio-button value="all">全部报告</el-radio-button>
      </el-radio-group>
      <span v-if="safeChapter" class="muted">仅显示截至第 {{ safeChapter }} 章可见的报告</span>
    </div>
    <el-empty v-if="!loading && !visible.length && !error" description="暂时没有需要审核的报告" />
    <article v-for="report in visible" :key="report.issue_id" class="panel review-card">
      <div class="report-heading">
        <div><span class="chapter-label">第 {{ report.source_chapter }} 章</span><h2>{{ targetLabel(report.target_type) }}</h2></div>
        <el-tag :type="statusType(report)">{{ statusLabel(report) }}</el-tag>
      </div>
      <p class="report-reason">{{ report.reason }}</p>
      <section class="evidence-section" aria-label="原文证据">
        <h3>原文依据</h3>
        <blockquote v-for="(item, index) in report.evidence" :key="index">
          <span>第 {{ item.chapter_number }} 章</span> {{ item.quote }}
        </blockquote>
      </section>
      <section class="comparison" aria-label="修改前后对照">
        <div class="comparison-title"><h3>{{ report.status === 'pending' ? '当前记录' : '修改前' }}</h3><h3>{{ report.status === 'applied' ? '已应用内容' : '建议修改' }} <small v-if="report.status === 'pending'">可以编辑</small></h3></div>
        <div v-for="field in report.allowed_fields" :key="field" class="field-row">
          <div class="before-field"><label>{{ fieldLabel(field) }}</label><p>{{ display(report.before[field]) }}</p></div>
          <div class="after-field">
            <label :for="`${report.issue_id}-${field}`">{{ fieldLabel(field) }}</label>
            <template v-if="report.status === 'pending'">
              <el-checkbox v-if="field === 'remove'" v-model="drafts[report.issue_id][field]">移除该章错误的称谓归属</el-checkbox>
              <el-select v-else-if="field === 'category'" v-model="drafts[report.issue_id][field]" :id="`${report.issue_id}-${field}`">
                <el-option v-for="(label, value) in categories" :key="value" :label="label" :value="value" />
              </el-select>
              <el-input v-else v-model="drafts[report.issue_id][field]" :id="`${report.issue_id}-${field}`" type="textarea" :autosize="{ minRows: 2, maxRows: 8 }" :placeholder="Array.isArray(report.before[field]) ? '每行一条；清空表示移除全部条目' : '填写修正后的内容'" />
            </template>
            <p v-else>{{ display(report.replacement?.[field] ?? report.before[field]) }}</p>
          </div>
        </div>
      </section>
      <p class="rebuild-note">{{ report.rebuild_layers.length ? '应用后重建对应实体层和索引；重建完成前，伴读会暂停使用受影响的派生结果。' : '此修改无需重建向量索引。' }}</p>
      <p class="muted downstream-note">{{ report.downstream_notice }}</p>
      <el-alert v-if="report.index_state === 'failed'" :title="report.error || '重建失败，可重试；已应用的源修改仍然保留。'" type="error" :closable="false" show-icon />
      <footer class="report-actions">
        <template v-if="report.status === 'pending'">
          <el-button @click="act(report, 'reject')" :disabled="!!busyId">拒绝建议</el-button>
          <el-button type="primary" @click="act(report, 'approve')" :loading="busyId === report.issue_id" :disabled="!!busyId || !Object.keys(patchFor(report)).length">{{ report.rebuild_layers.length ? '应用并重建' : '应用修改' }}</el-button>
        </template>
        <el-button v-if="report.status === 'applied' && ['pending', 'failed'].includes(report.index_state)" @click="act(report, 'rebuild')" :loading="busyId === report.issue_id" :disabled="!!busyId">{{ report.index_state === 'failed' ? '重试重建' : '开始重建' }}</el-button>
        <span v-if="report.index_state === 'rebuilding'" role="status" class="muted">正在重建，页面会自动刷新状态…</span>
      </footer>
    </article>
    <el-pagination v-if="total > 20" v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="load" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listCorrections, approveCorrection, rejectCorrection, rebuildCorrection } from '@/api/corrections'

const route = useRoute()
const router = useRouter()
const bookId = computed(() => Number(route.params.id))
const reports = ref([])
const drafts = ref({})
const loading = ref(false)
const error = ref('')
const filter = ref('pending')
const busyId = ref('')
const safeChapter = ref(null)
const page = ref(1)
const total = ref(0)
const visible = computed(() => reports.value.filter((r) => filter.value === 'all' || r.status === 'pending' || (r.status === 'applied' && !['done', 'not_required'].includes(r.index_state))))
const categories = { location: '地点', organization: '组织', item: '器物', technique: '功法', world_rule: '世界规则' }
const labels = { summary: '摘要', key_events: '关键事件', characters: '出场人物', cliffhanger: '章末悬念', identity: '身份', traits: '性格', traits_add: '新增性格', relations: '关系', relations_add: '新增关系', known_facts: '已知事实', known_facts_add: '新增事实', voice_sample: '代表台词', arc: '人物变化', category: '类型', aliases: '别名', details: '细节', entities: '关联人物或设定', remove: '称谓归属' }
const fieldLabel = (field) => labels[field] || field
const targetLabel = (type) => ({ summary: '章节摘要', character_delta: '人物逐章变化', character_override: '人物卡修订', lore: '世界观条目', alias: '称谓归属' })[type] || type
const display = (value) => Array.isArray(value) ? value.join('\n') || '（空）' : typeof value === 'boolean' ? value ? '移除' : '保留' : value || '（空）'
const statusLabel = (r) => r.status === 'pending' ? '待审核' : r.status === 'rejected' ? '已拒绝' : ({ pending: '已应用 · 待重建', rebuilding: '已应用 · 重建中', failed: '已应用 · 重建失败', done: '修改与重建完成', not_required: '修改已应用' })[r.index_state] || r.status
const statusType = (r) => r.index_state === 'failed' ? 'danger' : r.status === 'pending' ? 'warning' : r.status === 'rejected' ? 'info' : 'success'
let timer = null
let disposed = false

function patchFor(report) {
  const draft = drafts.value[report.issue_id] || {}
  const result = {}
  for (const field of report.allowed_fields) {
    const before = report.before[field]
    const value = Array.isArray(before) ? String(draft[field] ?? '').split('\n').map((s) => s.trim()).filter(Boolean) : draft[field]
    if (JSON.stringify(value) !== JSON.stringify(before ?? (typeof value === 'boolean' ? false : '')) && value !== undefined) result[field] = value
  }
  return result
}

async function load() {
  clearTimeout(timer)
  loading.value = true
  try {
    const data = await listCorrections(bookId.value, page.value, filter.value === 'pending' ? 'open' : undefined)
    if (disposed) return
    reports.value = data.items
    safeChapter.value = data.safe_chapter
    total.value = data.total ?? data.items.length
    error.value = ''
    for (const report of reports.value) {
      if (drafts.value[report.issue_id]) continue
      const value = { ...report.before, ...(report.replacement || {}) }
      drafts.value[report.issue_id] = Object.fromEntries(report.allowed_fields.map((field) => [field, Array.isArray(value[field]) ? value[field].join('\n') : value[field] ?? '']))
    }
    if (reports.value.some((r) => r.status === 'applied' && ['pending', 'rebuilding'].includes(r.index_state))) timer = setTimeout(load, 2500)
  } catch (err) {
    if (!disposed) { error.value = err.message; reports.value = []; safeChapter.value = null }
  } finally { loading.value = false }
}

function changeFilter() { page.value = 1; load() }

async function act(report, action) {
  if (action === 'approve') {
    try {
      await ElMessageBox.confirm('将按页面中的修改内容更新来源，并重建所需索引。不会自动重新抽取后续章节。', '确认应用修改', { confirmButtonText: '确认应用', cancelButtonText: '返回核对' })
    } catch { return }
  }
  busyId.value = report.issue_id
  try {
    if (action === 'approve') await approveCorrection(bookId.value, report.issue_id, patchFor(report))
    else if (action === 'reject') await rejectCorrection(bookId.value, report.issue_id)
    else await rebuildCorrection(bookId.value, report.issue_id)
    ElMessage.success(action === 'reject' ? '已拒绝建议' : action === 'rebuild' ? '已开始重建' : '修改已应用')
    await load()
    if (action === 'rebuild' && !disposed) { clearTimeout(timer); timer = setTimeout(load, 1000) }
  } catch (err) { ElMessage.error(err.message) }
  finally { busyId.value = '' }
}

onMounted(load)
onUnmounted(() => { disposed = true; clearTimeout(timer) })
</script>

<style scoped>
.review-header { display: flex; justify-content: space-between; gap: 24px; align-items: center; margin: 16px 0 28px; }
.eyebrow { color: var(--gold); margin-bottom: 10px; font-size: 12px; letter-spacing: .08em; }
h1 { font: 30px/1.4 'Noto Serif SC', 'SimSun', serif; margin-bottom: 12px; }
.review-header .muted { max-width: 700px; line-height: 1.8; }
.review-filter { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin: 24px 0; flex-wrap: wrap; }
.review-card { padding: 28px; margin-bottom: 24px; }
.report-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.report-heading > div { display: flex; align-items: center; gap: 14px; }
.chapter-label { color: var(--gold); font-size: 13px; }
h2 { font-size: 18px; font-weight: 500; }
h3 { font-size: 13px; font-weight: 500; color: var(--muted); }
h3 small { margin-left: 8px; font-weight: 400; }
.report-reason { line-height: 1.9; margin: 20px 0; overflow-wrap: anywhere; }
.evidence-section { background: var(--card-soft); padding: 18px 22px; border-left: 2px solid var(--gold-dim); margin-bottom: 24px; }
blockquote { margin: 12px 0 0; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }
blockquote span { color: var(--gold); margin-right: 8px; font-size: 12px; }
.comparison-title, .field-row { display: grid; grid-template-columns: 1fr 1fr; }
.comparison-title h3 { padding: 12px 16px; border-bottom: 1px solid var(--line); }
.field-row > div { padding: 16px; min-width: 0; }
.before-field { border-right: 1px solid var(--line-soft); background: var(--card-soft); }
.field-row { border-bottom: 1px solid var(--line-soft); }
.field-row label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.field-row p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.after-field p { color: var(--green-hi); }
.after-field :deep(.el-select) { width: 100%; }
.rebuild-note { color: var(--gold); line-height: 1.8; margin-top: 20px; font-size: 13px; }
.downstream-note { font-size: 12px; margin: 8px 0 16px; line-height: 1.8; }
.report-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 20px; }
@media (max-width: 720px) {
  .review-card { padding: 18px; }
  .review-header { align-items: flex-start; }
  .comparison-title { display: none; }
  .field-row { grid-template-columns: 1fr; }
  .before-field { border-right: none; }
  .report-heading > div { align-items: flex-start; flex-direction: column; gap: 6px; }
  .report-actions { flex-wrap: wrap; }
}
</style>
