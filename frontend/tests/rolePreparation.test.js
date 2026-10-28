import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, ref } from 'vue'
import { useRolePreparation } from '../src/composables/useRolePreparation.js'
import { roleErrorMessage, roleFormKey } from '../src/utils/rolePreparation.js'

const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const settle = async () => { await new Promise(setImmediate); await nextTick() }
const job = (overrides = {}) => ({ id: 1, role_id: 10, character: '岑砚', status: 'running', stage: 'extract',
  calls: 1, max_calls: 64, completed_batches: 1, batch_count: 3, progress: 31, ...overrides })
const profile = (id = 10) => ({ available: true, profile_id: id * 10, version: 1, stale: false,
  profile: { character: id === 10 ? '岑砚' : '祝遥', voice_evidence_ids: [] }, evidence_count: 1 })

function harness(overrides = {}) {
  const bookId = ref(1)
  const calls = []
  const api = {
    book: async (id) => ({ id, title: `合成书 ${id}` }),
    settings: async () => ({ providers: [{ id: 1, name: '合成模型', model: 'fake' }], active_id: 1 }),
    roles: async () => ({ items: [], total: 0 }), jobs: async () => ({ items: [], total: 0 }),
    characters: async () => ({ items: [{ name: '岑砚' }] }),
    preview: async (id, payload) => { calls.push(['preview', id, payload]); return { target: { name: payload.name }, minimum_calls: 2, selected_chars: 200 } },
    start: async (id, payload) => { calls.push(['start', id, payload]); return job({ status: 'queued' }) },
    profile: async () => ({ available: false }), job: async () => job(),
    pause: async () => job({ status: 'pausing' }), resume: async () => job(),
    evidence: async () => ({ items: [], total: 0 }), ...overrides
  }
  const scope = effectScope()
  const state = scope.run(() => useRolePreparation(bookId, api, { autoPoll: false }))
  return { state, bookId, calls, api, dispose: () => scope.stop() }
}

test('preview does not start a task; start sends exactly the reviewed parameters', async () => {
  const h = harness()
  try {
    await h.state.initialize()
    h.state.form.name = '  岑砚  '
    assert.equal(h.state.canStart.value, false)
    await h.state.previewForm()
    assert.equal(h.calls.length, 1)
    assert.equal(h.state.canStart.value, true)
    await h.state.start()
    assert.deepEqual(h.calls[1][2], h.calls[0][2])
    assert.equal(h.calls[1][2].name, '岑砚')
    assert.equal(h.state.currentJob.value.status, 'queued')
  } finally { h.dispose() }
})

test('form changes invalidate previews and an in-flight preview cannot restore stale authorization', async () => {
  const pending = deferred()
  const h = harness({ preview: () => pending.promise })
  try {
    await h.state.initialize(); h.state.form.name = '岑砚'
    const request = h.state.previewForm()
    h.state.form.name = '祝遥'
    pending.resolve({ target: { name: '岑砚' }, minimum_calls: 2 })
    await request
    assert.equal(h.state.preview.value, null)
    assert.equal(h.state.canStart.value, false)
    assert.equal(h.state.busy.preview, false)
  } finally { h.dispose() }
})

test('an active task prevents preparing and creation carries no call budget', async () => {
  const h = harness({ jobs: async () => ({ items: [job()], total: 1 }) })
  try {
    await h.state.initialize(); h.state.form.name = '岑砚'
    await h.state.previewForm()
    assert.equal(h.state.canStart.value, false)
    await h.state.selectRole({ role_id: 20, name: '祝遥' })
    assert.equal(h.state.activeJob.value.id, 1)
    h.state.jobs.value = []
    await h.state.previewForm()
    assert.equal(h.state.canStart.value, true)
    assert.equal('max_calls' in h.calls.at(-1)[2], false)
  } finally { h.dispose() }
})

test('changing roles discards a late profile response and does not reset newer loading state', async () => {
  const a = deferred(), b = deferred()
  const h = harness({ profile: (book, id) => id === 10 ? a.promise : b.promise })
  try {
    await h.state.initialize()
    const first = h.state.selectRole({ role_id: 10, name: '岑砚' })
    const second = h.state.selectRole({ role_id: 20, name: '祝遥' })
    a.resolve(profile(10)); await first
    assert.equal(h.state.profile.value, null)
    assert.equal(h.state.busy.profile, true)
    b.resolve(profile(20)); await second
    assert.equal(h.state.profile.value.profile.character, '祝遥')
  } finally { h.dispose() }
})

test('a book switch isolates pending profile and preview responses', async () => {
  const pending = deferred()
  const h = harness({ profile: () => pending.promise })
  try {
    await h.state.initialize()
    const first = h.state.selectRole({ role_id: 10, name: '岑砚' })
    h.bookId.value = 2; await settle()
    assert.equal(h.state.book.value.title, '合成书 2')
    pending.resolve(profile(10)); await first
    assert.equal(h.state.profile.value, null)
    assert.equal(h.state.selectedRoleId.value, null)
  } finally { h.dispose() }
})

test('out-of-order polling cannot move a completed task back to running', async () => {
  const old = deferred(), fresh = deferred()
  let count = 0
  const h = harness({ job: () => ++count === 1 ? old.promise : fresh.promise,
    profile: async () => profile() })
  try {
    await h.state.initialize(); await h.state.selectJob(job())
    const first = h.state.pollJob(), second = h.state.pollJob()
    fresh.resolve(job({ status: 'done', progress: 100 })); await second
    old.resolve(job({ status: 'running' })); await first
    assert.equal(h.state.currentJob.value.status, 'done')
  } finally { h.dispose() }
})

test('pausing invalidates polling started before the action', async () => {
  const pending = deferred()
  const h = harness({ job: () => pending.promise })
  try {
    await h.state.initialize(); await h.state.selectJob(job())
    const poll = h.state.pollJob()
    await h.state.control('pause')
    pending.resolve(job()); await poll
    assert.equal(h.state.currentJob.value.status, 'pausing')
  } finally { h.dispose() }
})

test('old call limits do not prevent continuing; changed sources still require a new task', async () => {
  const h = harness()
  try {
    await h.state.initialize()
    await h.state.selectJob(job({ status: 'failed', calls: 64, error: 'model_budget_exhausted' }))
    assert.equal(h.state.canResume.value, true)
    h.state.currentJob.value = job({ status: 'failed', error: 'source_changed' })
    assert.equal(h.state.canResume.value, false)
    assert.match(roleErrorMessage('source_changed'), /重新预览/)
  } finally { h.dispose() }
})

test('specific evidence is requested by observation id and a role switch discards late evidence', async () => {
  const pending = deferred(), requests = []
  const h = harness({ profile: async (book, id) => profile(id), evidence: (book, id, params) => {
    requests.push({ book, id, params }); return pending.promise
  } })
  try {
    await h.state.initialize(); await h.state.selectRole({ role_id: 10, name: '岑砚' })
    const first = h.state.showEvidence(['obs_last_page', 'obs_last_page'])
    assert.equal(requests.length, 1)
    assert.deepEqual(requests[0].params, { observation_id: 'obs_last_page' })
    await h.state.selectRole({ role_id: 20, name: '祝遥' })
    pending.resolve({ items: [{ observation_id: 'obs_last_page' }], total: 1 }); await first
    assert.deepEqual(h.state.evidence.value, [])
    assert.equal(h.state.evidenceOpen.value, false)
    assert.equal(h.state.busy.evidence, false)
  } finally { h.dispose() }
})

test('disposal prevents pending responses from updating a closed workspace', async () => {
  const pending = deferred()
  const h = harness({ job: () => pending.promise })
  await h.state.initialize(); await h.state.selectJob(job())
  const first = h.state.pollJob()
  h.dispose()
  pending.resolve(job({ status: 'done' })); await first
  assert.equal(h.state.currentJob.value.status, 'running')
})

test('changing meaningful form settings changes its reviewed identity', () => {
  const form = { name: '岑砚', mode: 'targeted', provider_id: 1 }
  const key = roleFormKey(form)
  assert.equal(roleFormKey({ ...form, name: ' 岑砚 ' }), key)
  for (const change of [{ mode: 'full' }, { name: '祝遥' }]) {
    assert.notEqual(roleFormKey({ ...form, ...change }), key)
  }
})

test('actual rate-limit failures show feedback and resume needs no budget or pacing input', async () => {
  const requests = []
  const h = harness({ resume: async (...args) => { requests.push(args); return job() } })
  try {
    await h.state.initialize()
    assert.equal('max_calls' in h.state.form, false)
    assert.equal('request_interval_seconds' in h.state.form, false)
    await h.state.selectJob(job({ status: 'paused', error: 'model_rate_limited', request_interval_seconds: 60 }))
    await h.state.control('resume')
    assert.deepEqual(requests[0], [1, 1])
    assert.match(roleErrorMessage('model_rate_limited'), /429/)
  } finally { h.dispose() }
})

test('preparation never sends a provider override, including after selecting an old record', async () => {
  const h = harness({ settings: async () => { throw new Error('settings are resolved by backend') } })
  try {
    await h.state.initialize()
    assert.equal('provider_id' in h.state.form, false)
    await h.state.selectJob(job({ provider_id: 1 }))
    h.state.jobs.value = []; h.state.currentJob.value = null
    await h.state.previewForm(); await h.state.start()
    assert.equal('provider_id' in h.calls[0][2], false)
    assert.equal('provider_id' in h.calls[1][2], false)
    assert.equal(h.state.errors.form, '')
  } finally { h.dispose() }
})

test('character suggestions retain server richness order and chapter counts', async () => {
  const h = harness({ characters: async () => ({ items: [
    { name: '祝遥', mention_count: 20 }, { name: '岑砚', mention_count: 2 }
  ] }) })
  try {
    await h.state.initialize()
    let items
    await h.state.suggestions('', (value) => { items = value })
    assert.deepEqual(items, [{ value: '祝遥', mention_count: 20 }, { value: '岑砚', mention_count: 2 }])
  } finally { h.dispose() }
})
