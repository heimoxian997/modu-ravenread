import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref, reactive, nextTick } from 'vue'
import { useRoleBatches } from '../src/composables/useRoleBatches.js'

const job = (change = {}) => ({ id: 1, role_id: 10, character: '岑砚', workflow: 'chapter_batches_v1',
  status: 'ready', calls: 0, max_calls: 64, completed_batches: 0, batch_count: 5, ...change })
const deferred = () => { let resolve; return { promise: new Promise((r) => { resolve = r }), resolve: (v) => resolve(v) } }
const settle = async () => { await nextTick(); await new Promise(setImmediate) }
function harness(overrides = {}) {
  const calls = [], book = ref(1)
  const root = {
    currentJob: ref(job()), selectedRoleId: ref(10), busy: reactive({ control: false }),
    selectJob: async (value) => { root.currentJob.value = value }, refreshLists: async () => {}, pollJob: async () => {}, loadProfile: async () => {},
    selectRole: async () => { root.currentJob.value = null }, initialize: async () => { root.currentJob.value = null }
  }
  const detail = { number: 1, chapter_number: 1, revision: 2, character: '岑砚', scenes: [], result: { character: '岑砚', observations: [], gaps: [] } }
  const api = {
    batches: async () => ({ items: [{ number: 1, status: 'pending' }], total: 5 }), batch: async () => structuredClone(detail),
    extract: async (...args) => { calls.push(['extract', ...args]); return job({ status: 'queued' }) },
    synthesize: async (...args) => { calls.push(['synthesize', ...args]); return job({ status: 'queued' }) },
    editBatch: async (...args) => { calls.push(['edit', ...args]); return { ...structuredClone(detail), revision: 3 } },
    deleteJob: async (...args) => { calls.push(['deleteJob', ...args]) }, deleteRole: async (...args) => { calls.push(['deleteRole', ...args]) }, ...overrides
  }
  const scope = effectScope(), state = scope.run(() => useRoleBatches(book, api, root))
  return { book, root, state, calls, dispose: () => scope.stop() }
}

test('two-batch trial sends only selected count, with no call budget or implicit synthesis', async () => {
  const h = harness()
  try {
    await settle(); await h.state.execute('extract')
    assert.deepEqual(h.calls, [['extract', 1, 1, { limit: 2 }]])
  } finally { h.dispose() }
})

test('old lifetime limits never disable a new bounded batch run', async () => {
  const h = harness()
  try {
    await settle(); h.root.currentJob.value = job({ calls: 1000, max_calls: 64 }); await settle()
    assert.equal(h.state.canRun.value, true)
    await h.state.execute('extract')
    assert.equal(h.calls[0][3].limit, 2)
  } finally { h.dispose() }
})

test('count, stop-at and explicit re-extraction remain distinct', async () => {
  const h = harness()
  try {
    await settle(); h.state.runMode.value = 'until'; h.state.amount.value = 4
    await h.state.execute('extract')
    assert.equal(h.calls[0][3].stop_at, 4); assert.equal(h.calls[0][3].limit, undefined)
    h.root.currentJob.value = job(); await settle()
    await h.state.execute('extract', 3, true)
    assert.equal(h.calls[1][3].batch_number, 3); assert.equal(h.calls[1][3].force, true)
    assert.equal(h.calls[1][3].stop_at, undefined)
  } finally { h.dispose() }
})

test('synthesis is explicit and requires every batch complete', async () => {
  const h = harness()
  try {
    await settle(); await h.state.execute('synthesize'); assert.equal(h.calls.length, 0)
    h.root.currentJob.value = job({ completed_batches: 5 }); await settle()
    await h.state.execute('synthesize'); assert.equal(h.calls[0][0], 'synthesize')
  } finally { h.dispose() }
})

test('editing carries revision and a late detail response cannot cross jobs', async () => {
  const pending = deferred(), h = harness({ batch: () => pending.promise })
  try {
    await settle(); const old = h.state.view(1)
    h.root.currentJob.value = job({ id: 2 }); await settle()
    pending.resolve({ number: 1, revision: 0, character: '岑砚', result: { character: '岑砚', observations: [], gaps: [] } })
    await old; assert.equal(h.state.detail.value, null); assert.equal(h.state.open.value, false)
  } finally { h.dispose() }
  const other = harness()
  try {
    await settle(); await other.state.view(1); await other.state.save()
    assert.equal(other.calls[0][4].revision, 2)
    assert.equal(other.state.detail.value.revision, 3)
    other.root.currentJob.value = job({ status: 'running' }); await settle()
    await other.state.save(); assert.equal(other.calls.length, 1)
  } finally { other.dispose() }
})

test('delete record preserves selection of role and clears batch dialog', async () => {
  const h = harness()
  try {
    await settle(); await h.state.view(1); await h.state.remove('job', job()); await settle()
    assert.deepEqual(h.calls[0], ['deleteJob', 1, 1])
    assert.equal(h.root.selectedRoleId.value, 10); assert.equal(h.root.currentJob.value, null)
    assert.equal(h.state.open.value, false)
  } finally { h.dispose() }
})

test('polling the same record preserves open editor and selected batch count', async () => {
  const h = harness()
  try {
    await settle(); await h.state.view(1); h.state.amount.value = 4
    h.root.pollJob = async () => { h.root.currentJob.value = job({ completed_batches: 2 }) }
    await h.state.save(); await settle()
    assert.equal(h.state.open.value, true)
    assert.equal(h.state.detail.value.revision, 3)
    assert.equal(h.state.amount.value, 4)
  } finally { h.dispose() }
})

test('retry all failed uses the server total across pages and never sends generation limits', async () => {
  const h = harness({ batches: async () => ({items:[{number:21,status:'pending'}],total:50,failed_total:7}) })
  try {
    await settle(); h.state.page.value = 2; await h.state.load()
    assert.equal(h.state.failedTotal.value, 7)
    assert.equal(h.state.canRetryFailed.value, true)
    h.state.amount.value = 2; h.state.runMode.value = 'until'
    await h.state.execute('retry_failed')
    assert.deepEqual(h.calls, [['extract',1,1,{retry_failed:true}]])
  } finally { h.dispose() }
})

test('retry failed is disabled while running or when the selected record has none', async () => {
  const h = harness()
  try {
    await settle(); await h.state.execute('retry_failed')
    assert.equal(h.calls.length, 0)
    h.state.failedTotal.value = 3; h.root.currentJob.value = job({status:'running'}); await settle()
    assert.equal(h.state.canRetryFailed.value, false)
    await h.state.execute('retry_failed'); assert.equal(h.calls.length, 0)
    h.root.currentJob.value = job({id:2}); await settle()
    assert.equal(h.state.failedTotal.value, 0)
  } finally { h.dispose() }
})
