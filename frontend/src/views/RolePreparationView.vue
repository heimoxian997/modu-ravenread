<template>
  <div class="page role-page" v-loading="busy.page">
    <el-button text class="back-link" @click="router.push(`/books/${bookId}`)">← 返回书籍</el-button>
    <header class="page-header">
      <div>
        <h1 class="page-title">人物资料</h1>
        <p class="page-subtitle">{{ book?.title || '从小说正文整理人物' }}<span class="scope-label">全书正文 · 不含番外</span></p>
      </div>
      <el-button :disabled="busy.page" @click="refresh">刷新</el-button>
    </header>
    <el-alert v-if="errors.page" :title="errors.page" type="error" :closable="false" show-icon />
    <el-alert v-if="errors.lists" class="page-alert" :title="errors.lists" type="error" :closable="false" show-icon />
    <el-alert v-if="batchError && !currentJob" class="page-alert" :title="batchError" type="error" :closable="false" show-icon />
    <div v-if="book" class="role-workspace">
      <aside class="role-sidebar">
        <section class="panel prepare-panel" aria-labelledby="prepare-heading">
          <h2 id="prepare-heading">① 准备批次</h2>
          <p class="section-note">先按章节整理原文批次。这一步不调用模型，也不改变阅读进度。</p>
          <form @submit.prevent="prepareBatches">
            <label for="role-name">人物名称</label>
            <el-autocomplete id="role-name" v-model="form.name" :fetch-suggestions="suggestions" :debounce="250"
              placeholder="选择人物，或输入正文中的姓名" clearable maxlength="100">
              <template #default="{ item }"><span>{{ item.value }}</span><small v-if="item.mention_count" class="suggestion-count">涉及 {{ item.mention_count }} 章</small></template>
            </el-autocomplete>
            <p class="form-note">按索引中涉及的章数排序，资料较多的人物优先。</p>
            <label>准备范围</label>
            <el-radio-group v-model="form.mode" class="scope-control" aria-label="准备范围">
              <el-radio-button value="targeted">相关场景</el-radio-button>
              <el-radio-button value="full">完整正文</el-radio-button>
            </el-radio-group>
            <p class="form-note">{{ form.mode === 'full' ? '检查每个正文章节，准备时间和调用量可能较多。' : '从姓名、已知称呼及附近场景开始；可能遗漏隐含身份或代词场景。' }}</p>
            <p class="form-note">批次按章整理，长章在章内切分。抽取时可选择生成批数。</p>
            <el-alert v-if="errors.form" :title="errors.form" type="error" :closable="false" show-icon />
            <el-button class="preview-button" type="primary" native-type="submit" :loading="busy.preview || busy.start" :disabled="!canPreview || !!activeJob">准备批次</el-button>
          </form>
          <p v-if="activeJob" class="active-notice">有资料正在准备。<button type="button" @click="selectJob(activeJob)">查看进度 →</button></p>
        </section>

        <section class="panel history-panel" aria-labelledby="saved-heading">
          <div class="section-heading"><h2 id="saved-heading">已准备的人物</h2><span>{{ roleTotal }}</span></div>
          <p v-if="!roles.length" class="empty-note">准备完成后，资料会留在这里。</p>
          <div v-for="role in roles" :key="role.role_id" class="history-row"><button type="button" class="history-item" :class="{ selected: selectedRoleId === role.role_id }"
            :aria-pressed="selectedRoleId === role.role_id" @click="selectRole(role)">
            <strong>{{ role.name }}</strong><span>{{ role.has_profile ? `资料版本 ${role.version}` : '尚未生成资料' }}</span>
          </button><el-button text type="danger" size="small" :disabled="batchBusy || jobs.some((j) => j.role_id === role.role_id && ACTIVE_ROLE_JOBS.includes(j.status))" :aria-label="`删除人物 ${role.name}`" @click="confirmDelete('role', role)">删除</el-button></div>
          <el-pagination v-if="roleTotal > 20" v-model:current-page="rolePage" :page-size="20" :total="roleTotal" layout="prev, next" @current-change="refreshLists" />
        </section>

        <section class="panel history-panel" aria-labelledby="jobs-heading">
          <div class="section-heading"><h2 id="jobs-heading">准备记录</h2><span>{{ jobTotal }}</span></div>
          <p v-if="!jobs.length" class="empty-note">还没有准备记录。</p>
          <div v-for="job in jobs" :key="job.id" class="history-row"><button type="button" class="history-item" :class="{ selected: currentJob?.id === job.id }"
            :aria-pressed="currentJob?.id === job.id" @click="selectJob(job)">
            <strong>{{ job.character }}</strong><span>{{ roleStatusLabel(job.status) }} · {{ job.calls }} 次调用</span>
          </button><el-button text type="danger" size="small" :disabled="batchBusy || ACTIVE_ROLE_JOBS.includes(job.status)" :aria-label="`删除准备记录 ${job.id}`" @click="confirmDelete('job', job)">删除</el-button></div>
          <el-pagination v-if="jobTotal > 20" v-model:current-page="jobPage" :page-size="20" :total="jobTotal" layout="prev, next" @current-change="refreshLists" />
        </section>
      </aside>

      <main class="role-detail">
        <section v-if="currentJob" class="panel task-panel" aria-label="准备进度" aria-live="polite">
          <div class="task-heading"><div><span class="eyebrow">{{ currentJob.character }}</span><h2>{{ currentJob.stage === 'synthesize' && currentJob.status === 'running' ? '正在整理人物资料' : roleStatusLabel(currentJob.status) }}</h2></div><el-tag :type="roleStatusType(currentJob.status)" effect="plain">{{ roleStatusLabel(currentJob.status) }}</el-tag></div>
          <el-progress v-if="currentJob.stage !== 'synthesize'" :percentage="currentJob.progress" :status="currentJob.status === 'done' ? 'success' : currentJob.status === 'failed' ? 'exception' : undefined" :stroke-width="6" />
          <p v-else class="form-note">③ 人物资料合成 · 分组进度与校验明细见下方，已保存结果可续用。</p>
          <div class="task-detail"><span>已完成 {{ currentJob.completed_batches }} / {{ currentJob.batch_count }} 批</span><span>已调用 {{ currentJob.calls }} 次</span></div>
          <p v-if="currentJob.run_attempted" class="form-note">本次已处理 {{ currentJob.run_attempted }} 批：成功 {{ currentJob.run_successes }}，失败 {{ currentJob.run_failures }}。</p>
          <p v-if="currentJob.request_pending" class="form-note">{{ currentJob.status === 'pausing' ? '正在取消本次等待' : '正在等待模型回复' }} · 已等待 {{ currentJob.request_elapsed_seconds }} 秒，单次最多 {{ currentJob.request_timeout_seconds }} 秒。</p>
          <p v-if="currentJob.last_usage?.total_tokens != null" class="form-note">最近一次请求：{{ tokenCount(currentJob.last_usage.prompt_tokens) }} 输入 token，{{ tokenCount(currentJob.last_usage.completion_tokens) }} 输出 token；共 {{ number(currentJob.last_usage.total_tokens) }} token。</p>
          <p v-if="currentJob.error === 'model_rate_limited' && ['requests', 'tokens', 'requests_and_tokens'].includes(currentJob.last_rate_limit_type)" class="form-note">{{ { requests: '服务提示触发请求频率限制。', tokens: '服务提示触发 token 速率限制，可减小每批输入或输出。', requests_and_tokens: '服务提示同时涉及请求频率和 token 速率，请核对服务限额。' }[currentJob.last_rate_limit_type] }}</p>
          <p v-if="currentJob.status === 'pausing'" class="form-note">正在停止本次等待，已完成批次会保留。模型服务端可能已经开始生成，本次请求仍可能计费。</p>
          <el-alert v-if="currentJob.error" :title="roleErrorMessage(currentJob.error)" :type="currentJob.status === 'failed' ? 'error' : 'info'" :closable="false" show-icon />
          <el-alert v-if="errors.task" :title="errors.task" type="error" :closable="false" show-icon />
          <div class="task-actions">
            <el-button v-if="['queued', 'running'].includes(currentJob.status)" :loading="busy.control" @click="control('pause')">暂停任务</el-button>
            <template v-if="staged && currentJob.stage !== 'synthesize' && currentJob.has_remaining_run && RESUMABLE_ROLE_JOBS.includes(currentJob.status) && !RESTART_ROLE_ERRORS.includes(currentJob.error)">
              <el-button type="primary" :loading="busy.control" :disabled="!canResume" @click="control('resume')">继续上次任务</el-button>
            </template>
            <el-button v-if="errors.task" @click="pollJob">重试获取进度</el-button>
            <span v-if="currentJob.status === 'done'" class="completion-note">资料已保存，可以在下方对照原文。</span>
          </div>
        </section>

        <section v-if="currentJob && staged" class="panel batch-panel" aria-label="分批抽取">
          <div class="section-heading"><h2>② 分批抽取</h2><span>{{ batchTotal }} 批 · 按章整理</span></div>
          <p class="section-note">一批返回并保存后才发下一批，不额外等待。失败项可单独编辑或重试；不会自动合成人物资料。使用设置中当前激活的模型服务。</p>
          <div class="batch-controls">
            <el-radio-group v-model="runMode" :disabled="batchActive || batchBusy" aria-label="抽取范围"><el-radio-button value="count">生成批数</el-radio-button><el-radio-button value="until">生成到批号</el-radio-button></el-radio-group>
            <el-input-number v-model="runAmount" :min="1" :max="runMode === 'until' ? currentJob.batch_count : 10000" :precision="0" :disabled="batchActive || batchBusy" aria-label="生成批数或截止批号" />
            <el-button type="primary" :disabled="!canRunBatches" :loading="batchBusy" @click="executeBatch('extract')">{{ runMode === 'count' ? `生成 ${runAmount || 0} 批` : `生成到第 ${runAmount || 0} 批` }}</el-button>
            <el-button :disabled="!canRetryFailed" :loading="batchBusy" @click="executeBatch('retry_failed')">重试全部失败批次（{{ failedBatchTotal }}）</el-button>
          </div>
          <p class="form-note">生成时跳过已成功和失败批次；失败项可一键按批号重试，每批只尝试一次，也可单独编辑。</p>
          <el-alert v-if="batchError" :title="batchError" type="error" :closable="false" show-icon />
          <el-table :data="batchItems" v-loading="batchLoading" row-key="number" class="batch-table">
            <el-table-column prop="number" label="批号" width="62" /><el-table-column label="章节" width="76"><template #default="{ row }">第 {{ row.chapter_number }} 章</template></el-table-column>
            <el-table-column label="正文量" width="90"><template #default="{ row }">{{ number(row.body_chars) }} 字</template></el-table-column>
            <el-table-column label="状态" min-width="90"><template #default="{ row }"><el-tag size="small" :type="row.status === 'done' ? 'success' : row.status === 'failed' ? 'danger' : 'info'" effect="plain">{{ batchStatus(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="条目 / 版本" width="96"><template #default="{ row }">{{ row.observation_count }} / {{ row.revision }}</template></el-table-column>
            <el-table-column label="操作" min-width="150"><template #default="{ row }"><el-button text size="small" class="detail-gold" @click="viewBatch(row.number)">查看 / 编辑</el-button><el-button text size="small" :disabled="!canRunBatches" @click="extractOne(row)">{{ row.status === 'done' ? '重抽' : row.status === 'failed' ? '重试' : '抽取' }}</el-button></template></el-table-column>
          </el-table>
          <el-pagination v-if="batchTotal > 20" v-model:current-page="batchPage" :page-size="20" :total="batchTotal" layout="prev, pager, next" @current-change="loadBatches" />
          <RoleSynthesisPanel :book-id="bookId" :job="currentJob" @updated="synthesisUpdated" />
        </section>
        <el-alert v-else-if="currentJob" title="这是旧流程记录，可查看或删除。需要分批抽取时，请在左侧重新准备批次。" type="info" :closable="false" show-icon />

        <section class="panel dossier" v-loading="busy.profile" aria-label="人物资料">
          <el-alert v-if="errors.profile" class="profile-error" :title="errors.profile" type="error" :closable="false" show-icon />
          <template v-if="detail">
            <header class="dossier-heading">
              <div class="name-seal" aria-hidden="true">{{ detail.character.slice(0, 1) }}</div>
              <div class="dossier-title"><p class="eyebrow">人物资料 · 第 {{ profile.version }} 版</p><h2>{{ detail.character }}</h2><p>以正文结局状态为目标；已死亡人物采用最后明确的生前状态。</p></div>
              <el-button class="all-evidence" @click="showEvidence()">浏览原文观测</el-button>
            </header>
            <el-alert v-if="profile.stale" title="正文、准备规则或批次结果已有变化。这是旧资料版本；请核对来源，并在批次完成后重新合成。" type="warning" :closable="false" show-icon />
            <p class="candidate-note">这些是根据原文整理的资料候选。原文有出处，不代表每项理解都准确，可以点击依据核对。</p>
            <section v-for="section in sections" :key="section.key" class="dossier-section" :aria-labelledby="`section-${section.key}`">
              <h3 :id="`section-${section.key}`">{{ section.title }}</h3>
              <p v-if="!section.items.length" class="empty-field">暂未整理出有依据的内容。</p>
              <article v-for="(item, index) in section.items" :key="index" class="profile-statement">
                <template v-if="section.key === 'behavior_rules'"><h4>{{ item.situation }}</h4><p>{{ item.response }}</p><p v-for="(exception, i) in item.exceptions" :key="i" class="rule-exception">例外：{{ exception }}</p></template>
                <p v-else>{{ item.text }}</p>
                <button v-if="item.evidence_ids?.length" class="source-link" type="button" @click="showEvidence(item.evidence_ids)">查看依据 · {{ item.evidence_ids.length }} 处 ↗</button>
              </article>
            </section>
            <section class="dossier-section" aria-labelledby="voice-heading"><h3 id="voice-heading">原文中的表达</h3>
              <p v-if="!voiceSamples.length" class="empty-field">暂未整理出可用的本人台词。</p>
              <article v-for="sample in voiceSamples" :key="sample.observation_id" class="voice-sample"><blockquote v-for="(quote, i) in sample.evidence" :key="i"><span>第 {{ quote.chapter_number }} 章 · {{ quote.match_method === 'paragraph' ? '表达所在段落' : quote.match_method === 'opening_sentence' ? '按开头定位的原文句子' : '原文摘录' }}</span>{{ quote.quote }}</blockquote><button type="button" class="source-link" @click="showEvidence([sample.observation_id])">核对说话者与语境 ↗</button></article>
            </section>
            <section class="dossier-section limitations" aria-labelledby="limits-heading"><h3 id="limits-heading">还不能确定的部分</h3><p v-if="!detail.limitations?.length" class="empty-field">没有额外列出，但仍建议核对关键身份、状态和认知。</p><p v-for="(item, index) in detail.limitations" :key="index">{{ item }}</p></section>
          </template>
          <div v-else-if="!busy.profile" class="dossier-empty">
            <div class="empty-mark" aria-hidden="true">人</div><h2>{{ currentJob && currentJob.status !== 'done' ? '人物资料正在路上' : '先选一个想了解的人物' }}</h2>
            <p v-if="currentJob?.synthesis_state?.phase === 'merge' && currentJob.status !== 'done'">初步资料已整理完成，正在第 {{ currentJob.synthesis_state.level }} 层汇总。本层完成后可能还有后续汇总，最终人物资料保存后会展示在这里。</p>
            <p v-else>{{ currentJob && currentJob.status !== 'done' ? '先完成批次抽取，再预览合成计划，逐组合成；最终资料会展示在这里。' : '输入姓名准备批次，再决定抽取多少批。已有资料可以从左侧打开。' }}</p>
            <el-button v-if="errors.profile && selectedRoleId" @click="loadProfile(selectedRoleId)">重试读取资料</el-button>
          </div>
        </section>
      </main>
    </div>

    <RoleBatchEditor v-model="batchOpen" :detail="batchDetail" :editor="batchEditor" :active="batchActive" :busy="batchBusy" :error="batchDetailError" @save="saveBatch()" @save-json="saveBatch" />

    <el-drawer v-model="evidenceOpen" title="对照原文" size="min(620px, calc(100vw - 28px))" append-to-body class="role-evidence-drawer">
      <p class="evidence-intro">引文位置已经核对，说话者归属、人物是否知情和结论是否成立仍需结合语境判断。查看不会改变阅读进度。</p>
      <el-alert v-if="errors.evidence" :title="errors.evidence" type="error" :closable="false" show-icon />
      <el-skeleton v-if="busy.evidence" :rows="6" animated />
      <el-empty v-else-if="!evidence.length" description="没有找到对应的原文观测" />
      <article v-for="item in evidence" :key="item.observation_id" class="evidence-card">
        <div class="evidence-labels"><el-tag size="small" effect="plain">{{ observationKindLabel(item.kind) }}</el-tag><el-tag size="small" type="info" effect="plain">{{ knowledgeLabel(item.knowledge) }}</el-tag></div>
        <p class="evidence-claim">{{ item.claim }}</p>
        <blockquote v-for="(quote, index) in item.evidence" :key="index"><span>第 {{ quote.chapter_number }} 章<span v-if="quote.match_method === 'format_normalized'"> · 已按原文恢复格式</span><span v-else-if="quote.match_method === 'paragraph'"> · 原文段落</span><span v-else-if="quote.match_method === 'opening_sentence'"> · 已从开头定位到句末</span><span v-if="quote.location_warning"> · 开头未能可靠定位，保留完整段落</span></span>{{ quote.quote }}</blockquote>
      </article>
      <el-button v-if="errors.evidence" @click="showEvidence(evidenceIds, evidencePage)">重试读取依据</el-button>
      <el-pagination v-if="!evidenceIds.length && evidenceTotal > 20" v-model:current-page="evidencePage" :page-size="20" :total="evidenceTotal" layout="prev, pager, next" @current-change="(page) => showEvidence([], page)" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { rolePreparationApi } from '@/api/rolePreparation'
import RoleSynthesisPanel from '@/components/RoleSynthesisPanel.vue'
import { useRolePreparation } from '@/composables/useRolePreparation'
import { useRoleBatches } from '@/composables/useRoleBatches'
import RoleBatchEditor from '@/components/RoleBatchEditor.vue'
import { knowledgeLabel, observationKindLabel, roleErrorMessage, roleStatusLabel, roleStatusType, ACTIVE_ROLE_JOBS, RESUMABLE_ROLE_JOBS, RESTART_ROLE_ERRORS } from '@/utils/rolePreparation'

const route = useRoute(), router = useRouter()
const bookId = computed(() => Number(route.params.id))
const preparation = useRolePreparation(bookId, rolePreparationApi)
const {
  book, roles, jobs, rolePage, jobPage, roleTotal, jobTotal, profile, selectedRoleId,
  currentJob, voiceSamples, preview, errors, busy, form, evidence, evidenceTotal,
  evidencePage, evidenceOpen, evidenceIds, activeJob, canPreview, canStart, canResume,
  initialize, refreshLists, suggestions, previewForm, loadProfile, selectRole, selectJob, pollJob,
  start, control, showEvidence, refresh
} = preparation
const {
  items: batchItems, total: batchTotal, failedTotal: failedBatchTotal, page: batchPage, detail: batchDetail, editor: batchEditor,
  open: batchOpen, busy: batchBusy, loading: batchLoading, error: batchError, detailError: batchDetailError,
  runMode, amount: runAmount, staged, active: batchActive, canRun: canRunBatches, canRetryFailed, canMerge,
  load: loadBatches, view: viewBatch, save: saveBatch, execute: executeBatch, remove: removeRecord
} = useRoleBatches(bookId, rolePreparationApi, preparation)
async function prepareBatches() { await previewForm(); if (canStart.value) await start() }
async function synthesisUpdated(job) { await selectJob(job); await refreshLists() }
const batchStatus = (status) => ({ pending: '待抽取', running: batchActive.value ? '正在抽取' : '待重试', done: '已完成', failed: '待修复' })[status] || status
async function extractOne(row) {
  if (row.status === 'done') {
    try { await ElMessageBox.confirm('重新请求这一批并保存新版本；上次有效结果在新结果通过校验前仍会保留。', '重新抽取本批', { confirmButtonText: '重新抽取', cancelButtonText: '取消', type: 'warning' }) } catch { return }
  }
  await executeBatch('extract', row.number, row.status === 'done')
}
async function confirmDelete(kind, item) {
  const text = kind === 'job' ? '删除这次准备记录、批次草稿和进度。已合成的人物资料及证据会保留。' : `删除人物“${item.name}”的全部资料版本、准备记录和缓存。原著、摘要及伴读数据会保留。`
  try { await ElMessageBox.confirm(text, kind === 'job' ? '删除准备记录' : '删除人物资料', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }) } catch { return }
  await removeRecord(kind, item)
}
const detail = computed(() => profile.value?.available ? profile.value.profile : null)
const number = (value) => Number(value || 0).toLocaleString('zh-CN')
const tokenCount = (value) => value == null ? '未知' : number(value)
const sections = computed(() => [
  ['identity', '身份'], ['current_state', '当前状态'], ['behavior_rules', '如何作出选择'],
  ['expression', '如何表达'], ['relationships', '重要关系'], ['known_facts', '有依据知道的事'], ['beliefs', '人物自己的理解']
].map(([key, title]) => ({ key, title, items: detail.value?.[key] || [] })))
onMounted(initialize)
</script>

<style scoped>
.role-page { max-width: 1440px; }
.back-link { margin-bottom: 14px; }
.scope-label { display: inline-block; color: var(--gold); margin-left: 18px; font-size: 12px; }
.page-alert { margin-bottom: 20px; }
.role-workspace { display: grid; grid-template-columns: 320px minmax(0, 1fr); align-items: start; gap: 24px; }
.role-sidebar, .role-detail { display: grid; gap: 20px; min-width: 0; }
.history-row { display: flex; gap: 6px; align-items: center; }.history-row .history-item { min-width: 0; flex: 1; }.history-row > .el-button { flex-shrink: 0; }
.batch-panel { padding: 24px; min-width: 0; }.batch-panel .section-note { margin-bottom: 18px; }.batch-controls, .batch-budget { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }.batch-controls .el-input-number { width: 112px; }.batch-budget { margin: 16px 0; font-size: 12px; color: var(--muted); }.batch-budget .el-input-number { width: 112px; }.batch-table { margin-top: 16px; }.merge-action { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-top: 22px; margin-top: 20px; border-top: 1px solid var(--line); flex-wrap: wrap; }
.prepare-panel { padding: 24px; }
h2 { font-size: 17px; font-weight: 500; }
.section-note, .form-note, .empty-note { color: var(--muted); font-size: 12px; line-height: 1.8; }
.section-note { margin: 9px 0 22px; }
form > label { display: block; margin: 18px 0 9px; color: var(--text); font-size: 13px; }
.prepare-panel :deep(.el-autocomplete), .prepare-panel :deep(.el-select) { width: 100%; }
.scope-control { display: flex; }
.scope-control :deep(.el-radio-button) { flex: 1; }
.scope-control :deep(.el-radio-button__inner) { width: 100%; }
.form-note { margin: 9px 0 12px; }
.suggestion-count { margin-left: 12px; color: var(--muted); font-size: 11px; }
.form-note a, .active-notice button { color: var(--gold); }
.budget-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 20px; }
.budget-row label { font-size: 13px; }
.budget-row :deep(.el-input-number) { width: 112px; }
.preview-button, .preparation-preview > .el-button { width: 100%; margin-top: 14px; }
.preparation-preview { margin-top: 22px; padding-top: 20px; border-top: 1px solid var(--line); }
.preparation-preview h3 { font: 23px/1.6 'Noto Serif SC', 'SimSun', serif; }
.preparation-preview h3 small { font: 11px/1.6 'Microsoft YaHei', sans-serif; color: var(--muted); margin-left: 10px; }
.preparation-preview dl { margin: 15px 0; }
.preparation-preview dl > div { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; font-size: 12px; }
.preparation-preview dt { color: var(--muted); }.preparation-preview dd { margin: 0; color: var(--gold); font-variant-numeric: tabular-nums; }
.preparation-preview > p { font-size: 12px; color: var(--muted); line-height: 1.8; }
.active-notice { color: var(--muted); font-size: 12px; margin-top: 16px; line-height: 1.8; }
.active-notice button { border: 0; background: transparent; padding: 0; cursor: pointer; }
.history-panel { overflow: hidden; padding-bottom: 10px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; padding: 20px 24px 14px; }
.section-heading h2 { font-size: 14px; }.section-heading > span { color: var(--muted); font-size: 12px; }
.history-item { width: 100%; display: flex; flex-direction: column; gap: 7px; padding: 15px 24px; text-align: left; border: 0; border-left: 2px solid transparent; background: transparent; color: var(--text); cursor: pointer; overflow-wrap: anywhere; }
.history-item:hover { background: var(--surface-hover); }.history-item.selected { background: var(--green-soft-bg); border-left-color: var(--gold); }
.history-item strong { font-weight: 500; font-size: 14px; }.history-item > span { font-size: 11px; color: var(--muted); }
.empty-note { padding: 0 24px 14px; }.history-panel :deep(.el-pagination) { justify-content: flex-end; padding: 10px 18px 0; }
.task-panel { padding: 24px 28px; }.task-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 20px; }
.eyebrow { color: var(--gold); font-size: 12px; letter-spacing: .04em; }.task-heading h2 { margin-top: 7px; }
.task-detail { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; color: var(--muted); font-size: 12px; margin: 12px 0 16px; font-variant-numeric: tabular-nums; }
.task-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 16px; }.task-actions label { color: var(--muted); font-size: 12px; }.task-actions :deep(.el-input-number) { width: 120px; }.completion-note { color: var(--green-soft); font-size: 12px; }
.dossier { padding: 32px; min-height: 600px; overflow: hidden; }.dossier-heading { display: flex; align-items: center; gap: 18px; padding-bottom: 24px; border-bottom: 1px solid var(--line); }
.name-seal { display: grid; place-items: center; flex-shrink: 0; width: 62px; height: 78px; border: 1px solid var(--gold-dim); color: var(--gold); background: var(--gold-soft-bg); font: 35px/1 'Noto Serif SC', 'SimSun', serif; border-radius: 3px; }
.dossier-title { flex: 1; min-width: 0; }.dossier-title h2 { margin: 9px 0; font: 32px/1.3 'Noto Serif SC', 'SimSun', serif; overflow-wrap: anywhere; }.dossier-title > p:last-child { color: var(--muted); font-size: 11px; line-height: 1.8; }
.all-evidence { align-self: start; }.candidate-note { font-size: 12px; color: var(--muted); line-height: 1.9; margin: 20px 0 4px; }
.dossier > .el-alert { margin-top: 20px; }.dossier-section { display: grid; grid-template-columns: 110px minmax(0, 1fr); column-gap: 22px; padding: 25px 0; border-bottom: 1px solid var(--line-soft); }.dossier-section h3 { grid-column: 1; grid-row: 1 / 100; font-size: 13px; font-weight: 500; color: var(--gold); line-height: 1.9; }.dossier-section > :not(h3) { grid-column: 2; min-width: 0; }
.profile-statement + .profile-statement { margin-top: 19px; }.profile-statement p, .profile-statement h4, .limitations > p { font-size: 14px; line-height: 1.9; overflow-wrap: anywhere; white-space: pre-wrap; font-weight: 400; }.profile-statement h4 { color: var(--muted); font-size: 12px; margin-bottom: 5px; }.profile-statement .rule-exception { color: var(--muted); font-size: 12px; margin-top: 6px; }.source-link { border: 0; padding: 0; background: transparent; color: var(--green-soft); font-size: 11px; line-height: 1.8; margin-top: 8px; cursor: pointer; text-align: left; }.source-link:hover { color: var(--gold); }.empty-field { font-size: 12px; color: var(--muted); line-height: 1.9; }.limitations { border-bottom: 0; }.limitations > p + p { margin-top: 12px; }
.voice-sample blockquote { margin: 0; padding: 14px 18px; background: var(--card-soft); border-left: 2px solid var(--gold-dim); line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }.voice-sample blockquote > span { display: block; color: var(--gold); font-size: 11px; margin-bottom: 6px; }.voice-sample + .voice-sample { margin-top: 15px; }
.dossier-empty { min-height: 500px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }.empty-mark { width: 70px; height: 84px; display: grid; place-items: center; border: 1px solid var(--line); color: var(--gold-dim); font: 38px/1 'SimSun', serif; margin-bottom: 28px; }.dossier-empty h2 { font: 24px/1.6 'Noto Serif SC', 'SimSun', serif; }.dossier-empty p { max-width: 360px; color: var(--muted); line-height: 2; font-size: 13px; margin: 14px 0; }
.evidence-intro { color: var(--muted); font-size: 12px; line-height: 1.9; margin-bottom: 22px; }.evidence-card { padding: 22px 0; border-bottom: 1px solid var(--line-soft); }.evidence-labels { display: flex; gap: 9px; flex-wrap: wrap; }.evidence-claim { line-height: 1.9; margin: 14px 0; white-space: pre-wrap; overflow-wrap: anywhere; }.evidence-card blockquote { padding: 16px 18px; border-left: 2px solid var(--gold-dim); background: var(--card-soft); margin: 12px 0; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }.evidence-card blockquote span { display: block; color: var(--gold); font-size: 12px; margin-bottom: 7px; }
button:focus-visible, a:focus-visible { outline: 2px solid var(--gold); outline-offset: 3px; }
@media (max-width: 1100px) { .role-workspace { grid-template-columns: 280px minmax(0, 1fr); gap: 18px; }.prepare-panel { padding: 20px; }.dossier { padding: 24px; }.dossier-heading { flex-wrap: wrap; }.all-evidence { margin-left: 80px; }.dossier-section { grid-template-columns: 1fr; gap: 12px; }.dossier-section h3 { grid-column: 1; grid-row: auto; }.dossier-section > :not(h3) { grid-column: 1; } }
@media (max-width: 850px) { .role-page { padding: 22px 20px 40px; }.role-workspace { grid-template-columns: minmax(0, 1fr); }.role-sidebar { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; }.prepare-panel { grid-column: 1 / -1; }.scope-label { margin-left: 0; display: block; margin-top: 6px; }.task-panel { padding: 22px; }.dossier { padding: 24px; }.dossier-empty { min-height: 340px; }.all-evidence { margin-left: 0; } }
@media (max-width: 540px) { .role-page { padding: 20px 14px 36px; }.role-sidebar { grid-template-columns: minmax(0, 1fr); }.task-panel, .dossier { padding: 20px; }.name-seal { width: 48px; height: 64px; font-size: 29px; }.dossier-title h2 { font-size: 27px; }.task-heading { align-items: flex-start; } }
</style>
