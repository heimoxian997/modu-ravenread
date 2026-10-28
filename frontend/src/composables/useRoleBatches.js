import { computed, onScopeDispose, ref, watch } from 'vue'
import { ACTIVE_ROLE_JOBS, roleRequestError } from '../utils/rolePreparation.js'

export function useRoleBatches(bookId, api, root) {
  const items = ref([]), total = ref(0), failedTotal = ref(0), page = ref(1), detail = ref(null), editor = ref(null)
  const open = ref(false), busy = ref(false), loading = ref(false), error = ref(''), detailError = ref('')
  const runMode = ref('count'), amount = ref(2)
  let disposed = false, listSequence = 0, detailSequence = 0
  const snapshot = () => ({ book: bookId.value, job: root.currentJob.value?.id })
  const valid = (request) => !disposed && request.book === bookId.value && request.job === root.currentJob.value?.id
  const staged = computed(() => root.currentJob.value?.workflow === 'chapter_batches_v1')
  const active = computed(() => ACTIVE_ROLE_JOBS.includes(root.currentJob.value?.status))
  const canRun = computed(() => staged.value && !active.value && !busy.value && !root.busy.control)
  const canRetryFailed = computed(() => canRun.value && failedTotal.value > 0)
  const canMerge = computed(() => canRun.value && root.currentJob.value.batch_count > 0 &&
    root.currentJob.value.completed_batches === root.currentJob.value.batch_count && root.currentJob.value.status !== 'done')

  async function load() {
    const request = snapshot(), sequence = ++listSequence
    if (!staged.value) { items.value = []; total.value = 0; failedTotal.value = 0; return }
    loading.value = true; error.value = ''
    try {
      const result = await api.batches(request.book, request.job, page.value)
      if (valid(request) && sequence === listSequence) {
        items.value = result.items; total.value = result.total; failedTotal.value = result.failed_total || 0
      }
    } catch (err) { if (valid(request) && sequence === listSequence) error.value = roleRequestError(err) }
    finally { if (valid(request) && sequence === listSequence) loading.value = false }
  }

  async function view(number) {
    const request = snapshot(), sequence = ++detailSequence
    open.value = true; detail.value = null; editor.value = null; detailError.value = ''
    try {
      const result = await api.batch(request.book, request.job, number)
      if (!valid(request) || sequence !== detailSequence) return
      detail.value = result
      editor.value = structuredClone(result.result || result.draft_result || { character: result.character, observations: [], gaps: [] })
    } catch (err) { if (valid(request) && sequence === detailSequence) detailError.value = roleRequestError(err) }
  }

  async function save(override) {
    if (!detail.value || !editor.value || active.value || busy.value) return
    const request = snapshot(), sequence = detailSequence
    const number = detail.value.number
    busy.value = true; detailError.value = ''
    try {
      const result = await api.editBatch(request.book, request.job, number, {
        revision: detail.value.revision, result: JSON.parse(JSON.stringify(override === undefined ? editor.value : override))
      })
      if (!valid(request) || sequence !== detailSequence) return
      detail.value = result; editor.value = structuredClone(result.result)
      await root.pollJob(); await load()
      if (valid(request)) await root.loadProfile(root.currentJob.value.role_id)
    } catch (err) { if (valid(request) && sequence === detailSequence) detailError.value = roleRequestError(err) }
    finally { if (valid(request)) busy.value = false }
  }

  async function execute(action, number, force = false) {
    if (!canRun.value) return
    if (action === 'retry_failed' && !canRetryFailed.value) return
    if (action === 'synthesize' && !canMerge.value) return
    const request = snapshot()
    const payload = {}
    if (action === 'retry_failed') payload.retry_failed = true
    else if (action === 'extract') {
      if (number) { payload.batch_number = number; payload.force = force }
      else {
        if (!Number.isInteger(amount.value) || amount.value < 1 || amount.value > 10000) return
        payload[runMode.value === 'count' ? 'limit' : 'stop_at'] = amount.value
      }
    }
    busy.value = true; error.value = ''
    try {
      const result = await api[action === 'retry_failed' ? 'extract' : action](request.book, request.job, payload)
      if (!valid(request)) return
      open.value = false
      await root.selectJob(result); await root.refreshLists(); await load()
    } catch (err) { if (valid(request)) error.value = roleRequestError(err) }
    finally { if (valid(request)) busy.value = false }
  }

  async function remove(kind, item) {
    const book = bookId.value
    busy.value = true; error.value = ''
    try {
      await api[kind === 'job' ? 'deleteJob' : 'deleteRole'](book, kind === 'job' ? item.id : item.role_id)
      if (disposed || book !== bookId.value) return
      if (kind === 'role' && root.selectedRoleId.value === item.role_id) {
        await root.initialize()
      } else {
        if (kind === 'job' && root.currentJob.value?.id === item.id) {
          await root.selectRole({ role_id: item.role_id, name: item.character })
        }
        await root.refreshLists()
      }
    } catch (err) { if (!disposed && book === bookId.value) error.value = roleRequestError(err) }
    finally { if (!disposed && book === bookId.value) busy.value = false }
  }

  watch([bookId, () => root.currentJob.value?.id], () => {
    listSequence++; detailSequence++; page.value = 1; items.value = []; total.value = 0; failedTotal.value = 0
    detail.value = null; editor.value = null; open.value = false; busy.value = false; loading.value = false
    error.value = ''; detailError.value = ''; amount.value = 2
    load()
  }, { immediate: true })
  watch([() => root.currentJob.value?.completed_batches, () => root.currentJob.value?.run_attempted, () => root.currentJob.value?.status], load)
  onScopeDispose(() => { disposed = true; listSequence++; detailSequence++ })
  return { items, total, failedTotal, page, detail, editor, open, busy, loading, error, detailError, runMode, amount,
    staged, active, canRun, canRetryFailed, canMerge, load, view, save, execute, remove }
}
