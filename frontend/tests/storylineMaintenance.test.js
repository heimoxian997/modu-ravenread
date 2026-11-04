import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from 'vue/compiler-sfc'
import * as Vue from 'vue'
import * as manualNotes from '../src/utils/manualNotes.js'

// Mount actual setup code to exercise asynchronous form state without a browser.
function mount(name, props, dependencies = {}) {
  const source = readFileSync(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')
  let code = compileScript(parse(source).descriptor, { id: `test-${name}` }).content
  code = code.replace(/import \{([^}]+)\} from ['"]vue['"]/g, (_, names) => `const { ${names.replace(/\bas\b/g, ':')} } = Vue`)
  code = code.replace(/^import .+ from .+$/gm, '').replace('export default', 'return')
  const injected = { ElMessage: { success() {} }, StorylineNodeAdd: {}, ...manualNotes, ...dependencies }
  const component = new Function('Vue', ...Object.keys(injected), code)(Vue, ...Object.values(injected))
  component.render = () => null
  const renderer = Vue.createRenderer({ createComment: () => ({}), insert() {}, remove() {}, parentNode() {}, nextSibling() {} })
  const emitted = {}
  const handlers = Object.fromEntries(['add', 'moved', 'restored', 'saved'].map(name => [`on${name[0].toUpperCase()}${name.slice(1)}`, (...args) => { (emitted[name] ||= []).push(args) }]))
  const app = renderer.createApp(component, { ...props, ...handlers })
  const vm = app.mount({})
  return { state: vm.$.setupState, emitted, unmount: () => app.unmount() }
}
const settle = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const event = (id, chapter) => ({ note_id: id, version: 3, chapter_number: chapter, title: '合成事件', claim: '合成事件结论', evidence: [] })

test('adding a node ignores old chapter responses and retains fixed evidence version', async () => {
  const requests = []
  const h = mount('StorylineNodeAdd', { bookId: 1, chapters: [{ idx: 0, title: '合成章' }, { idx: 2, title: '合成章' }], nodes: [] }, {
    request(options) { const result = deferred(); requests.push({ options, ...result }); return result.promise },
  })
  h.state.open()
  h.state.chapter = 1; h.state.changeChapter()
  requests[1].resolve({ data: { ok: true, data: { items: [event('current', 1)], next_offset: null } } })
  await settle()
  requests[0].resolve({ data: { ok: true, data: { items: [event('old', 3)], next_offset: null } } })
  await settle()
  assert.equal(h.state.events[0].note_id, 'current')
  h.state.selectedId = 'current'; h.state.selectEvent(); h.state.add()
  const result = h.emitted.add[0][0]
  assert.deepEqual(result.node.evidence, [{ note_id: 'current', version: 3 }])
  assert.equal(result.node.chapter_number, 1)
  assert.equal(h.state.visible, false)
  h.unmount()
})

test('moving uses both displayed versions and ignores an earlier target selection', async () => {
  const first = deferred(), second = deferred(), submissions = []
  const h = mount('StorylineNodeMove', { bookId: 1, noteId: 'source', version: 7, node: { node_id: 'node_a', chapter_number: 3 }, nodeCount: 2 }, {
    listStorylines: async () => ({ items: [{ note_id: 'source' }, { note_id: 'target' }], has_more: false, cursor: null }),
    getStorylineEdit: (book, id) => id === 'first' ? first.promise : second.promise,
    moveStorylineNode: async (...args) => { submissions.push(args); return { target_note_id: 'target', node_id: 'moved_node' } },
  })
  h.state.open(); await settle()
  assert.deepEqual(h.state.lines.map(l => l.note_id), ['target'])
  h.state.targetId = 'first'; h.state.loadTarget()
  h.state.targetId = 'target'; h.state.loadTarget()
  second.resolve({ note_id: 'target', version: 9, overview: '合成概述', nodes: [] }); await settle()
  first.resolve({ note_id: 'first', version: 1, nodes: [] }); await settle()
  assert.equal(h.state.target.note_id, 'target')
  await h.state.move()
  assert.deepEqual(submissions[0], [1, 'source', 'node_a', { expected_version: 7, target_note_id: 'target', target_expected_version: 9 }])
  assert.equal(h.emitted.moved.length, 1)
  h.unmount()
})

test('restoring submits deletion version and refreshes the deleted list', async () => {
  let restored = false
  const h = mount('StorylineDeleted', { bookId: 1 }, {
    listDeletedStorylines: async () => ({ items: restored ? [] : [{ note_id: 'deleted_line', version: 4, title: '合成线', node_count: 2 }], next_offset: null }),
    restoreStoryline: async (book, id, version) => { assert.equal(book, 1); assert.equal(id, 'deleted_line'); assert.equal(version, 4); restored = true; return { note_id: id, version: 5 } },
  })
  h.state.open(); await settle()
  await h.state.restore(h.state.items[0])
  assert.deepEqual(h.state.items, [])
  assert.equal(h.emitted.restored[0][0].version, 5)
  assert.equal(h.state.busyId, '')
  h.unmount()
})

test('editor persists appended nodes and connections with its original version', async () => {
  const requests = []
  const original = { note_id: 'line_test', version: 5, title: '合成线', overview: '合成概述', stage: '进行中', nodes: [{ node_id: 'old', chapter_number: 1, conclusion: '合成结论', evidence: [{ note_id: 'event_a', version: 1 }] }] }
  const h = mount('StorylineEditor', { bookId: 1, noteId: 'line_test', chapters: [] }, {
    request: async options => { requests.push(options); return { data: { ok: true, data: original } } },
  })
  await h.state.open()
  h.state.addNode({ node: { node_id: 'new', chapter_number: 3, conclusion: '合成新增结论', evidence: [{ note_id: 'event_b', version: 2 }] }, edges: [{ source: 'old', target: 'new', relation: 'follows' }] })
  await h.state.save()
  assert.equal(requests[1].data.expected_version, 5)
  assert.equal(requests[1].data.nodes[0].node_id, 'new')
  assert.equal(requests[1].data.edges[0].target, 'new')
  assert.equal(h.emitted.saved.length, 1)
  h.unmount()
})
