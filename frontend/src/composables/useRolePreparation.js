import { computed, onScopeDispose, reactive, ref, watch } from 'vue'
import {
  ACTIVE_ROLE_JOBS, RESUMABLE_ROLE_JOBS, RESTART_ROLE_ERRORS, roleFormKey, roleRequestError
} from '../utils/rolePreparation.js'

// Injected API makes route changes, polling and late responses testable without a browser.
export function useRolePreparation(bookId, api, { autoPoll = true, pollMs = 2000 } = {}) {
  const book = ref(null), roles = ref([]), jobs = ref([])
  const rolePage = ref(1), jobPage = ref(1), roleTotal = ref(0), jobTotal = ref(0)
  const profile = ref(null), selectedRoleId = ref(null), currentJob = ref(null), voiceSamples = ref([])
  const preview = ref(null), previewKey = ref('')
  const errors = reactive({ page: '', form: '', profile: '', task: '', evidence: '', lists: '' })
  const busy = reactive({ page: false, preview: false, start: false, control: false, profile: false, evidence: false })
  const form = reactive({ name: '', mode: 'targeted' })
  const evidence = ref([]), evidenceTotal = ref(0), evidencePage = ref(1), evidenceOpen = ref(false)
  const evidenceIds = ref([])
  let generation = 0, profileSequence = 0, jobSequence = 0, pollSequence = 0, evidenceSequence = 0, previewSequence = 0, listsSequence = 0
  let disposed = false, timer = null
  const scope = () => ({ id: bookId.value, generation })
  const valid = (request) => !disposed && request.id === bookId.value && request.generation === generation
  const activeJob = computed(() => jobs.value.find((job) => ACTIVE_ROLE_JOBS.includes(job.status)) ||
    (ACTIVE_ROLE_JOBS.includes(currentJob.value?.status) ? currentJob.value : null))
  const canPreview = computed(() => !!book.value && !!form.name.trim() && !busy.preview && !busy.start)
  const canStart = computed(() => canPreview.value && !!preview.value && previewKey.value === roleFormKey(form) &&
    !activeJob.value)
  const canResume = computed(() => currentJob.value && RESUMABLE_ROLE_JOBS.includes(currentJob.value.status) &&
    !RESTART_ROLE_ERRORS.includes(currentJob.value.error) && !busy.control)

  watch(() => roleFormKey(form), () => { preview.value = null; previewKey.value = ''; errors.form = ''; previewSequence++ }, { flush: 'sync' })

  function stopPolling() { clearTimeout(timer); timer = null }
  function schedulePoll() {
    stopPolling()
    if (autoPoll && !disposed && ACTIVE_ROLE_JOBS.includes(currentJob.value?.status)) timer = setTimeout(pollJob, pollMs)
  }

  async function refreshLists() {
    const request = scope(), sequence = ++listsSequence
    const [roleResult, jobResult] = await Promise.allSettled([
      api.roles(request.id, rolePage.value), api.jobs(request.id, jobPage.value)
    ])
    if (!valid(request) || sequence !== listsSequence) return
    errors.lists = ''
    if (roleResult.status === 'fulfilled') { roles.value = roleResult.value.items; roleTotal.value = roleResult.value.total }
    else errors.lists = roleRequestError(roleResult.reason)
    if (jobResult.status === 'fulfilled') { jobs.value = jobResult.value.items; jobTotal.value = jobResult.value.total }
    else errors.lists = roleRequestError(jobResult.reason)
  }

  async function initialize() {
    generation++; profileSequence++; jobSequence++; evidenceSequence++; previewSequence++
    stopPolling()
    const request = scope()
    book.value = null; roles.value = []; jobs.value = []
    profile.value = null; selectedRoleId.value = null; currentJob.value = null; voiceSamples.value = []
    preview.value = null; previewKey.value = ''; evidenceOpen.value = false; evidence.value = []
    roleTotal.value = 0; jobTotal.value = 0; rolePage.value = 1; jobPage.value = 1
    form.name = ''; form.mode = 'targeted'
    for (const key of Object.keys(errors)) errors[key] = ''
    for (const key of Object.keys(busy)) busy[key] = false
    busy.page = true
    const [bookResult] = await Promise.allSettled([api.book(request.id)])
    if (!valid(request)) return
    if (bookResult.status === 'fulfilled') book.value = bookResult.value
    else errors.page = roleRequestError(bookResult.reason)
    if (book.value) {
      await refreshLists()
      if (!valid(request)) return
      const latest = jobs.value.find((job) => ACTIVE_ROLE_JOBS.includes(job.status)) || jobs.value[0]
      if (latest) await selectJob(latest)
      else if (roles.value[0]) await selectRole(roles.value[0])
    }
    if (valid(request)) busy.page = false
  }

  async function suggestions(query, callback) {
    const request = scope()
    try {
      const data = await api.characters(request.id, query)
      callback(valid(request) ? data.items.map((item) => ({ value: item.name, mention_count: item.mention_count })) : [])
    } catch { callback([]) }
  }

  async function previewForm() {
    if (!canPreview.value) return
    const request = scope(), key = roleFormKey(form), sequence = ++previewSequence
    busy.preview = true; errors.form = ''; preview.value = null
    try {
      const result = await api.preview(request.id, JSON.parse(key))
      if (valid(request) && sequence === previewSequence && key === roleFormKey(form)) {
        preview.value = result; previewKey.value = key
      }
    } catch (error) {
      if (valid(request) && sequence === previewSequence) errors.form = roleRequestError(error)
    } finally {
      if (valid(request)) busy.preview = false
    }
  }

  async function loadProfile(roleId) {
    const request = scope(), sequence = ++profileSequence
    evidenceSequence++; evidenceOpen.value = false
    evidence.value = []; errors.evidence = ''; busy.evidence = false
    profile.value = null; voiceSamples.value = []; errors.profile = ''; busy.profile = true
    try {
      const result = await api.profile(request.id, roleId)
      if (!valid(request) || sequence !== profileSequence) return
      profile.value = result
      if (result.available && result.profile.voice_evidence_ids?.length) {
        const responses = await Promise.all(result.profile.voice_evidence_ids.map((id) =>
          api.evidence(request.id, result.profile_id, { observation_id: id })))
        if (valid(request) && sequence === profileSequence) voiceSamples.value = responses.flatMap((page) => page.items)
      }
    } catch (error) {
      if (valid(request) && sequence === profileSequence) errors.profile = roleRequestError(error)
    } finally { if (valid(request) && sequence === profileSequence) busy.profile = false }
  }

  async function selectRole(role) {
    jobSequence++; stopPolling(); currentJob.value = null
    selectedRoleId.value = role.role_id; form.name = role.name
    await loadProfile(role.role_id)
  }

  function updateJob(job) {
    currentJob.value = job
    const index = jobs.value.findIndex((item) => item.id === job.id)
    if (index >= 0) jobs.value[index] = job
  }

  async function selectJob(job) {
    jobSequence++; stopPolling()
    updateJob(job)
    selectedRoleId.value = job.role_id; form.name = job.character || ''
    errors.task = ''
    await loadProfile(job.role_id)
    schedulePoll()
  }

  async function pollJob() {
    stopPolling()
    const request = scope(), id = currentJob.value?.id, sequence = jobSequence, poll = ++pollSequence
    if (!id) return
    try {
      const result = await api.job(request.id, id)
      if (!valid(request) || sequence !== jobSequence || poll !== pollSequence || id !== currentJob.value?.id) return
      const previousStatus = currentJob.value.status
      updateJob(result); errors.task = ''
      if (result.status !== previousStatus && !ACTIVE_ROLE_JOBS.includes(result.status)) { await loadProfile(result.role_id); await refreshLists() }
    } catch (error) {
      if (valid(request) && sequence === jobSequence && poll === pollSequence) errors.task = roleRequestError(error)
    } finally { if (valid(request) && sequence === jobSequence && poll === pollSequence) schedulePoll() }
  }

  async function start() {
    if (!canStart.value) return
    const request = scope(), payload = JSON.parse(previewKey.value)
    busy.start = true; errors.form = ''
    try {
      const result = await api.start(request.id, payload)
      if (!valid(request)) return
      preview.value = null; previewKey.value = ''
      await refreshLists()
      if (valid(request)) await selectJob(result)
    } catch (error) { if (valid(request)) errors.form = roleRequestError(error) }
    finally { if (valid(request)) busy.start = false }
  }

  async function control(action) {
    const job = currentJob.value
    if (!job || busy.control || (action === 'resume' && !canResume.value)) return
    const request = scope(), sequence = ++jobSequence
    busy.control = true; errors.task = ''
    try {
      const result = await api[action](request.id, job.id)
      if (!valid(request) || sequence !== jobSequence) return
      updateJob(result); schedulePoll(); await refreshLists()
    } catch (error) { if (valid(request) && sequence === jobSequence) errors.task = roleRequestError(error) }
    finally { if (valid(request)) busy.control = false }
  }

  async function showEvidence(ids = [], page = 1) {
    if (!profile.value?.available) return
    const request = scope(), version = profile.value.profile_id, sequence = ++evidenceSequence
    evidenceIds.value = [...new Set(ids)].slice(0, 8); evidencePage.value = page
    evidenceOpen.value = true; evidence.value = []; errors.evidence = ''; busy.evidence = true
    try {
      const pages = evidenceIds.value.length ? await Promise.all(evidenceIds.value.map((id) =>
        api.evidence(request.id, version, { observation_id: id }))) :
        [await api.evidence(request.id, version, { offset: (page - 1) * 20, limit: 20 })]
      if (!valid(request) || sequence !== evidenceSequence || profile.value?.profile_id !== version) return
      evidence.value = [...new Map(pages.flatMap((p) => p.items).map((item) => [item.observation_id, item])).values()]
      evidenceTotal.value = evidenceIds.value.length ? evidence.value.length : pages[0].total
    } catch (error) { if (valid(request) && sequence === evidenceSequence) errors.evidence = roleRequestError(error) }
    finally { if (valid(request) && sequence === evidenceSequence) busy.evidence = false }
  }

  async function refresh() {
    await refreshLists()
    if (currentJob.value) await pollJob()
    if (selectedRoleId.value) await loadProfile(selectedRoleId.value)
  }

  watch(bookId, initialize)
  onScopeDispose(() => { disposed = true; generation++; stopPolling() })
  return { book, roles, jobs, rolePage, jobPage, roleTotal, jobTotal, profile, selectedRoleId,
    currentJob, voiceSamples, preview, errors, busy, form, evidence, evidenceTotal,
    evidencePage, evidenceOpen, evidenceIds, activeJob, canPreview, canStart, canResume,
    initialize, refreshLists, suggestions, previewForm, loadProfile, selectRole, selectJob, pollJob,
    start, control, showEvidence, refresh }
}
