<template>
  <section class="follow" aria-label="伴读模式">
    <button type="button" :disabled="disabled || !ready" :aria-pressed="enabled" @click="toggle">{{ enabled ? '暂停跟读' : '开启伴读模式' }}</button>
    <span role="status">{{ label }}</span>
    <details v-if="state.summaries?.length" class="results">
      <summary>第 {{ chapterIdx + 1 }} 章成果 · {{ state.event_count || 0 }} 条事件</summary>
      <p>正文覆盖 {{ state.covered }} / {{ state.total }} 字符。概括可能有误，具体事件以原文依据为准。</p>
      <p v-for="part in state.summaries" :key="part.start" class="overview">{{ part.summary }}</p>
      <p>{{ state.organization?.message || '正文处理后将自动整理剧情线' }}</p>
      <p v-if="state.organization?.total_events != null">已整理 {{ state.organization.processed_events || 0 }} / {{ state.organization.total_events }} 条事件；建议须在剧情线页确认。</p>
      <ul v-if="state.organization?.lines?.length"><li v-for="line in state.organization.lines" :key="line.proposal_id || line.note_id">{{ line.title }}</li></ul>
      <router-link :to="`/books/${bookId}/storylines?from=reader&chapter=${chapterIdx}`">查看事件依据与剧情线</router-link>
    </details>
    <template v-if="activity === 'jump'">
      <button type="button" @click="scheduler.acceptJump()">只跟读当前章</button>
      <button type="button" @click="pause">暂停伴读</button>
    </template>
    <button v-if="['failed', 'partial', 'cancelled'].includes(state.status)" type="button" @click="retryOrganization">继续 / 重试</button>
    <button v-if="['failed', 'partial'].includes(state.organization?.status)" type="button" @click="retryOrganization">继续剧情整理</button>
    <button v-else-if="state.status === 'completed' && !state.organization?.status" type="button" @click="retryOrganization">整理已有成果</button>
    <small v-if="!enabled">开启后使用当前模型按章跟读，会产生调用费用；当前章按整章已读。</small>
  </section>
</template>

<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import request from '@/utils/request'
import { createCompanionScheduler } from '@/utils/companionScheduler'
const props = defineProps({ bookId: Number, chapterIdx: Number, disabled: Boolean, ready: { type: Boolean, default: true } })
const enabled = ref(false)
const state = ref({ status: 'paused' })
const activity = ref('paused')
let retryRequested = false
let viewVersion = 0
const labels = { paused: '跟读已暂停', waiting: '等待章节停留…', running: '正在跟读本章…',
  completed: '本章处理完成', failed: '跟读未完成，可重试', partial: '本章部分已跟读，可继续',
  cancelled: '本章跟读已取消', jump: '已跳章，是否只跟读当前章？' }
const label = computed(() => (enabled.value && ['waiting', 'running', 'jump'].includes(activity.value) ? labels[activity.value] : '') || (state.value.status === 'loading' ? '正在加载本章成果…' : '') || state.value.error || (state.value.organization?.status === 'running' ? '正文已处理，正在整理剧情线…' : state.value.organization?.status === 'failed' ? '正文已处理，剧情整理待重试' : labels[state.value.status]) || '本章尚未跟读')
const cancel = (bookId, job) => request({ url: `/books/${bookId}/companion-read/${job.id}`, method: 'delete', params: { attempt_id: job.attempt_id }, silent: true }).catch(() => {})
const scheduler = createCompanionScheduler({
  publish(value) {
    if (value.bookId != null && (value.bookId !== props.bookId || value.chapterIdx !== props.chapterIdx || !props.ready)) return
    activity.value = value.status
    if (value.status === 'completed') state.value = value.result
    if (value.status === 'failed') state.value = { ...state.value, error: '跟读未完成，请重试' }
  },
  async run({ bookId, chapterIdx, signal }) {
    const retry = retryRequested
    retryRequested = false
    // Do not abort submission: its response gives the persisted ID needed to cancel.
    const response = await request({ url: `/books/${bookId}/companion-read`, method: 'post',
      data: { chapter_idx: chapterIdx, retry }, silent: true })
    let job = response.data
    const submittedJob = { ...job }
    const owned = job.started_here === true
    const stop = () => { if (owned) void cancel(bookId, submittedJob) }
    signal.addEventListener('abort', stop, { once: true })
    try {
      if (signal.aborted) { if (owned) await cancel(bookId, submittedJob); return job }
      while (['queued', 'running'].includes(job.status) && !signal.aborted) {
        await new Promise(resolve => setTimeout(resolve, 1000))
        if (signal.aborted) break
        const next = await request({ url: `/books/${bookId}/companion-read/${chapterIdx}`, silent: true, signal })
        job = next.data || { ...job, status: 'failed' }
        if (!signal.aborted && bookId === props.bookId && chapterIdx === props.chapterIdx) state.value = job
        if (job.id !== submittedJob.id || job.attempt_id !== submittedJob.attempt_id) {
          return { ...submittedJob, status: 'cancelled', error: '任务已由其他窗口更新，请重新查看' }
        }
      }
      return job
    } finally {
      signal.removeEventListener('abort', stop)
    }
  },
})
function pause() { viewVersion++; enabled.value = false; scheduler.stop() }
function toggle() {
  viewVersion++
  if (enabled.value) pause()
  else { enabled.value = true; scheduler.start(props.bookId, props.chapterIdx) }
}
function retry() { retryRequested = true; scheduler.retry() }
function retryOrganization() { retryRequested = true; if (enabled.value) scheduler.retry(); else { enabled.value = true; scheduler.start(props.bookId, props.chapterIdx) } }
watch(() => [props.bookId, props.chapterIdx, props.disabled, props.ready], async ([book, chapter, disabled, ready], old) => {
  state.value = { status: 'loading' }
  if (disabled || (old && old[0] !== book)) pause()
  const version = ++viewVersion
  if (disabled || !ready) return
  if (enabled.value) scheduler.navigate(book, chapter)
  try {
    const response = await request({ url: `/books/${book}/companion-read/${chapter}`, silent: true })
    if (version === viewVersion) state.value = response.data || { status: 'unread' }
  } catch { if (version === viewVersion) state.value = { status: 'failed', error: '本章成果加载失败，请重试' } }
}, { immediate: true })
onBeforeUnmount(() => { viewVersion++; scheduler.dispose() })
</script>

<style scoped>
.follow { display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:8px 20px; font-size:12px; }
.follow button { color:inherit; background:transparent; border:1px solid currentColor; border-radius:6px; padding:5px 9px; cursor:pointer; }
.follow small { opacity:.7; }
.results { flex-basis:100%; max-height:35vh; overflow:auto; border-top:1px solid #8884; padding-top:8px; }
.results summary { cursor:pointer; }
.overview { white-space:pre-wrap; line-height:1.7; }
.results a { color:inherit; text-decoration:underline; }
</style>
