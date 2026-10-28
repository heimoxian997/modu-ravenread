<template>
  <div class="synthesis-panel">
    <h2>③ 合成人物资料</h2>
    <p class="note">先预览分组，不调用模型。已保存组按任务复用，切换激活模型后从未完成组继续；每次按选择的组数串行处理，全部完成后再汇总。</p>
    <p v-if="state" class="progress-note">
      资料整理已保存 {{ state.group_completed }} / {{ state.group_total }} 组
      <span v-if="state.status === 'done'"> · 合成已完成</span>
      <span v-else-if="state.current > 0"> · {{ state.status === 'pending' && job.status === 'ready' ? '下一步（尚未请求）' : '本轮进度' }}：{{ state.phase === 'merge' ? `第 ${state.level} 层汇总` : '资料整理' }} 第 {{ state.current }} / {{ state.total }} 组</span>
      <span v-if="state.input_chars"> · 当前组输入 {{ number(state.input_chars) }} 字符</span>
      <span v-if="state.status === 'pending' && job.status === 'ready'"> · 本轮已停止，可继续</span>
    </p>
    <p v-if="state?.phase === 'merge' && state.status !== 'done'" class="note">当前是第 {{ state.level }} 层汇总，{{ state.current }} / {{ state.total }} 仅表示本层进度。各层继续收敛为一份资料，最终保存后会显示在下方；已完成结果会复用。</p>
    <p v-if="job.synthesis_usage?.calls" class="note">合成累计 {{ job.synthesis_usage.calls }} 次请求 · 已记录 {{ number(job.synthesis_usage.total_tokens) }} tokens（含失败请求）。</p>
    <el-alert v-if="state?.hint" :title="`校验明细：${state.hint}`" type="warning" :closable="false" />
    <p v-if="job.stage === 'synthesize' && job.status === 'failed' && !job.has_synthesis_response" class="note">这条旧记录未保存合成原始返回及字段明细，无法本地修复；抽取资料仍完整保留。请先预览，不要直接重复整条合成。</p>
    <el-alert v-if="error && !open" :title="error" type="error" :closable="false" />
    <div class="controls">
      <el-button :disabled="!ready" :loading="busy" @click="plan">{{ preview ? '刷新合成计划' : '预览合成计划' }}</el-button>
      <el-button v-if="job.has_synthesis_response" :disabled="busy" @click="inspect">查看合成返回 / 修订</el-button>
    </div>
    <template v-if="preview">
      <p class="plan-summary">后续请求使用 {{ preview.model }} · {{ preview.observation_count }} 条观测，先整理 {{ preview.group_count }} 组，已有 {{ preview.cached_groups }} 组可复用。</p>
      <p class="note">{{ preview.merge_required ? '整理后还需分层汇总，汇总组数取决于整理结果。' : '本次可直接整理为人物资料。' }}字符量不是精确 token 估算，实际用量以服务返回为准。</p>
      <div class="group-list"><span v-for="group in preview.groups" :key="group.number">第 {{ group.number }} 组 · {{ group.observation_count }} 条 · {{ number(group.input_chars) }} 字符<span v-if="group.cached"> · 已保存<span v-if="group.origin?.model">（{{ group.origin.model }}）</span></span></span></div>
      <div class="controls">
        <label>本次最多处理 <el-input-number v-model="amount" :min="1" :max="1000" :precision="0" :disabled="!ready" aria-label="本次合成组数" /> 组（含汇总）</label>
        <el-button type="primary" :disabled="!ready || job.status === 'done'" :loading="busy" @click="start(false)">处理 / 继续 {{ amount || 0 }} 组</el-button>
        <el-button v-if="state?.status === 'failed' && job.has_synthesis_response" :disabled="!ready" @click="start(true)">重新请求失败组（会调用模型）</el-button>
      </div>
    </template>
    <el-dialog v-model="open" title="合成返回与本地修订" width="min(960px, calc(100vw - 32px))" destroy-on-close>
      <p class="note">原始返回保存在本机角色资料中。修订后本地校验不调用模型；校验通过会缓存本组，再刷新计划继续后续组。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <p v-if="response?.hint" class="note">字段明细：{{ response.hint }}</p>
      <el-input v-model="raw" type="textarea" :rows="18" :disabled="busy || !ready" aria-label="合成返回 JSON" />
      <template #footer><el-button @click="open = false">关闭</el-button><el-button type="primary" :disabled="!ready || !response?.available" :loading="busy" @click="save">本地校验并保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, toRef } from 'vue'
import { rolePreparationApi } from '@/api/rolePreparation'
import { useRoleSynthesis } from '@/composables/useRoleSynthesis'
const props = defineProps({ bookId: Number, job: { type: Object, required: true } })
const emit = defineEmits(['updated'])
const state = computed(() => props.job.synthesis_state)
const { preview, busy, error, amount, open, response, raw, ready, plan, start, inspect, save } =
  useRoleSynthesis(toRef(props, 'bookId'), toRef(props, 'job'), rolePreparationApi, (job) => emit('updated', job))
const number = (value) => Number(value || 0).toLocaleString('zh-CN')
</script>

<style scoped>
.synthesis-panel { border-top: 1px solid var(--line-soft); margin-top: 24px; padding-top: 24px; }
h2 { font-size: 17px; color: var(--text); margin-bottom: 12px; }.note { color: var(--muted); font-size: 12px; line-height: 1.85; margin: 10px 0; }
.progress-note, .plan-summary { color: var(--gold); font-size: 13px; line-height: 1.9; margin: 14px 0; }
.controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 14px; }.controls > .el-button { margin-left: 0; }
.controls label { color: var(--muted); font-size: 12px; }.controls .el-input-number { width: 110px; margin: 0 6px; }
.group-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); max-height: 220px; overflow: auto; gap: 8px; color: var(--muted); font-size: 12px; line-height: 1.8; }
@media(max-width: 600px) { .group-list { grid-template-columns: 1fr; }.controls label { line-height: 2.6; } }
</style>
