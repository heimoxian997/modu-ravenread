import test from 'node:test'
import assert from 'node:assert/strict'
import { createCompanionScheduler } from '../src/utils/companionScheduler.js'

function harness() {
  const timers = new Map(), calls = [], states = []
  let id = 0
  const scheduler = createCompanionScheduler({
    setTimer(fn) { timers.set(++id, fn); return id },
    clearTimer(key) { timers.delete(key) },
    publish(state) { states.push(state) },
    run(request) { return new Promise((resolve, reject) => calls.push({ request, resolve, reject })) },
  })
  return { scheduler, calls, states, tick() {
    const callbacks = [...timers.values()]; timers.clear()
    callbacks.forEach(fn => fn())
  } }
}
const settle = () => new Promise(resolve => setImmediate(resolve))

test('rapid sequential navigation processes only the final chapter', () => {
  const h = harness()
  h.scheduler.start(1, 0)
  h.scheduler.navigate(1, 1)
  h.scheduler.navigate(1, 2)
  h.tick()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].request.chapterIdx, 2)
})

test('jump requires a choice and never backfills skipped chapters', () => {
  const h = harness()
  h.scheduler.start(1, 0)
  h.scheduler.navigate(1, 8)
  h.tick()
  assert.equal(h.calls.length, 0)
  h.scheduler.acceptJump()
  h.tick()
  assert.equal(h.calls[0].request.chapterIdx, 8)
})

test('late completion cannot publish after navigation; requests remain serial', async () => {
  const h = harness()
  h.scheduler.start(1, 0); h.tick()
  h.scheduler.navigate(1, 1); h.tick()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].request.signal.aborted, true)
  h.calls[0].resolve('old result'); await settle()
  assert.equal(h.calls.length, 2)
  assert.equal(h.states.some(s => s.status === 'completed'), false)
  h.calls[1].resolve('current result'); await settle()
  assert.equal(h.states.at(-1).chapterIdx, 1)
})

test('switching books pauses and ignores late failures', async () => {
  const h = harness()
  h.scheduler.start(1, 0); h.tick()
  h.scheduler.navigate(2, 0)
  h.calls[0].reject(new Error('synthetic failure')); await settle()
  assert.equal(h.states.at(-1).status, 'paused')
  h.tick(); assert.equal(h.calls.length, 1)
})

test('failed chapter can retry; disposal suppresses results', async () => {
  const h = harness()
  h.scheduler.start(1, 0); h.tick()
  h.calls[0].reject(new Error('synthetic failure')); await settle()
  assert.equal(h.states.at(-1).status, 'failed')
  h.scheduler.retry(); h.tick()
  assert.equal(h.calls.length, 2)
  h.scheduler.dispose()
  h.calls[1].resolve('late result'); await settle()
  assert.equal(h.states.at(-1).status, 'paused')
})
