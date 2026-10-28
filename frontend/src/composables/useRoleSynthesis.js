import { computed, onScopeDispose, ref, watch } from 'vue'
import { ACTIVE_ROLE_JOBS, roleRequestError } from '../utils/rolePreparation.js'

export function useRoleSynthesis(bookId, job, api, updated) {
  const preview = ref(null), busy = ref(false), error = ref(''), amount = ref(1)
  const open = ref(false), response = ref(null), raw = ref('')
  let disposed = false, sequence = 0
  const scope = () => ({ book: bookId.value, job: job.value?.id, revision: job.value?.results_revision, sequence: ++sequence })
  const valid = (s) => !disposed && s.book === bookId.value && s.job === job.value?.id &&
    s.revision === job.value?.results_revision && s.sequence === sequence
  const ready = computed(() => job.value?.workflow === 'chapter_batches_v1' && job.value.batch_count > 0 &&
    job.value.completed_batches === job.value.batch_count && !ACTIVE_ROLE_JOBS.includes(job.value.status) && !busy.value)

  async function plan() {
    if (!ready.value) return
    const request = scope(); busy.value = true; error.value = ''; preview.value = null
    try {
      const result = await api.synthesisPreview(request.book, request.job)
      if (valid(request)) preview.value = result
    } catch (e) { if (valid(request)) error.value = roleRequestError(e) }
    finally { if (valid(request)) busy.value = false }
  }
  async function start(retry = false) {
    if (!ready.value || !preview.value || !Number.isInteger(amount.value) || amount.value < 1 || amount.value > 1000) return
    const request = scope(); busy.value = true; error.value = ''
    try {
      const result = await api.synthesize(request.book, request.job,
        { limit: amount.value, preview_key: preview.value.preview_key, retry_failed: retry })
      if (valid(request)) await updated(result)
    } catch (e) { if (valid(request)) error.value = roleRequestError(e) }
    finally { if (valid(request)) busy.value = false }
  }
  async function inspect() {
    if (busy.value) return
    const request = scope(); busy.value = true; error.value = ''; open.value = true; response.value = null; raw.value = ''
    try {
      const result = await api.synthesisResponse(request.book, request.job)
      if (valid(request)) { response.value = result; raw.value = result.raw_content || '' }
    } catch (e) { if (valid(request)) error.value = roleRequestError(e) }
    finally { if (valid(request)) busy.value = false }
  }
  async function save() {
    if (!ready.value || !response.value?.available) return
    const request = scope(); busy.value = true; error.value = ''
    try {
      const result = await api.editSynthesisResponse(request.book, request.job,
        { cache_key: response.value.cache_key, revision: response.value.revision, raw_content: raw.value })
      if (valid(request)) {
        response.value = result; preview.value = null
        await updated(await api.job(request.book, request.job))
      }
    } catch (e) { if (valid(request)) error.value = roleRequestError(e) }
    finally { if (valid(request)) busy.value = false }
  }
  watch([bookId, () => job.value?.id, () => job.value?.results_revision], () => {
    sequence++; preview.value = null; response.value = null; raw.value = ''; open.value = false
    busy.value = false; error.value = ''; amount.value = 1
  })
  onScopeDispose(() => { disposed = true; sequence++ })
  return { preview, busy, error, amount, open, response, raw, ready, plan, start, inspect, save }
}
