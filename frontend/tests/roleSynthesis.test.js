import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref, nextTick } from 'vue'
import { useRoleSynthesis } from '../src/composables/useRoleSynthesis.js'

function harness(overrides = {}) {
  const book = ref(1), job = ref({ id:1,workflow:'chapter_batches_v1',status:'ready',batch_count:129,completed_batches:129,results_revision:1 })
  const calls = [], scope = effectScope()
  const api = {
    synthesisPreview:async()=>({preview_key:'a'.repeat(64),group_count:12}),
    synthesize:async (...args)=>{calls.push(args);return {...job.value,status:'queued'}},
    synthesisResponse:async()=>({available:true,cache_key:'b'.repeat(64),revision:1,raw_content:'{}'}),
    editSynthesisResponse:async (...args)=>{calls.push(['edit',...args]);return {available:true}},
    job:async()=>({...job.value,status:'ready'}), ...overrides
  }
  const state = scope.run(()=>useRoleSynthesis(book,job,api,(result)=>{job.value=result}))
  return {book,job,calls,state,dispose:()=>scope.stop()}
}

test('synthesis requires a free preview and defaults to one group per explicit run', async()=>{
  const h=harness()
  try {
    await h.state.start(); assert.equal(h.calls.length,0)
    await h.state.plan(); assert.equal(h.calls.length,0)
    await h.state.start()
    assert.deepEqual(h.calls[0],[1,1,{limit:1,preview_key:'a'.repeat(64),retry_failed:false}])
    assert.equal(h.state.ready.value,false)
  } finally {h.dispose()}
})
test('model resend is explicit and local JSON repair never starts synthesis',async()=>{
  const h=harness()
  try {
    await h.state.inspect(); await h.state.save()
    assert.equal(h.calls.length,1); assert.equal(h.calls[0][0],'edit')
    assert.equal(h.state.preview.value,null)
    await h.state.plan(); await h.state.start(true)
    assert.equal(h.calls[1][2].retry_failed,true)
  } finally {h.dispose()}
})
test('a late plan cannot carry authorization to another role record',async()=>{
  let resolve; const pending=new Promise(r=>{resolve=r})
  const h=harness({synthesisPreview:()=>pending})
  try {
    const request=h.state.plan(); h.job.value={...h.job.value,id:2}; await nextTick()
    resolve({preview_key:'a'.repeat(64)}); await request
    assert.equal(h.state.preview.value,null)
    assert.equal(h.state.busy.value,false)
  } finally {h.dispose()}
})
