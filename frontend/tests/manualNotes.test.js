import test from 'node:test'
import assert from 'node:assert/strict'
import { eventPayload, storylinePayload, addedEventNode, storylineEditPayload } from '../src/utils/manualNotes.js'

const event = () => ({ title: '传递纸条', claim: '甲某把纸条交给乙某。', participants: '甲某、乙某、甲某', chapter_number: 3, quote: '甲某把纸条交给乙某。', basis: 'explicit' })
const node = (chapter, id) => ({ chapter_number: chapter, conclusion: '核对交接。', event: { chapter_number: chapter, note_id: id, version: 2, entities: ['甲某', '乙某'] } })
const line = () => ({ title: '纸条去向', category: 'plot', overview: '梳理纸条的交接。', stage: '核对中', entities: '', nodes: [node(5, 'event_b'), node(3, 'event_a')], connect: true })

test('manual event deduplicates names and anchors evidence to its chapter', () => {
  const result = eventPayload(event())
  assert.deepEqual(result.payload.participants, ['甲某', '乙某'])
  assert.equal(result.chapter_number, result.evidence[0].chapter_number)
  assert.equal(result.payload.action, '传递纸条')
})
test('manual event rejects incomplete evidence, oversized claims and missing perspective', () => {
  assert.throws(() => eventPayload({ ...event(), quote: '短句' }), /4–120/)
  assert.throws(() => eventPayload({ ...event(), claim: '甲'.repeat(181) }), /1–180/)
  assert.throws(() => eventPayload({ ...event(), basis: 'interpretation' }), /视角/)
})
test('line preserves evidence versions and connects in chapter order', () => {
  const result = storylinePayload(line())
  assert.deepEqual(result.nodes.map(n => n.chapter_number), [3, 5])
  assert.deepEqual(result.nodes[0].evidence, [{ note_id: 'event_a', version: 2 }])
  assert.deepEqual(result.entities, ['甲某', '乙某'])
  assert.equal(result.edges[0].source, result.nodes[0].node_id)
  assert.equal(result.edges[0].target, result.nodes[1].node_id)
  assert.deepEqual(storylinePayload({ ...line(), connect: false }).edges, [])
})
test('line requires selected same-chapter events and a conclusion', () => {
  assert.throws(() => storylinePayload({ ...line(), nodes: [] }), /1–40/)
  assert.throws(() => storylinePayload({ ...line(), nodes: [{ ...node(1, 'x'), event: null }] }), /选择事件/)
  assert.throws(() => storylinePayload({ ...line(), nodes: [{ ...node(1, 'x'), chapter_number: 3 }] }), /属于节点/)
})

test('added node keeps event version and allows only forward chapter connections', () => {
  const existing = [{ node_id: 'early', chapter_number: 1, evidence: [{ note_id: 'old', version: 1 }] }]
  const event = { note_id: 'new_event', version: 4, chapter_number: 3 }
  const result = addedEventNode(event, 3, '合成新节点', existing, 'new_node', 'early')
  assert.deepEqual(result.node.evidence, [{ note_id: 'new_event', version: 4 }])
  assert.equal(result.edges[0].source, 'early')
  assert.equal(result.edges[0].target, 'new_node')
  assert.throws(() => addedEventNode(event, 1, '合成节点', existing, 'n'), /本章/)
  assert.throws(() => addedEventNode(event, 3, '合成节点', [{ ...existing[0], chapter_number: 4 }], 'n', 'early'), /不能晚于/)
  assert.throws(() => addedEventNode(event, 3, '合成节点', [result.node], 'another'), /已经引用/)
})

test('editing sends new nodes and edges without rewriting untouched branches', () => {
  const old = { note_id: 'line_test', version: 2, title: '合成线', overview: '合成概述', stage: '进行中',
    nodes: [{ node_id: 'a', chapter_number: 1, conclusion: '合成旧节点', evidence: [{ note_id: 'event_a', version: 1 }] },
      { node_id: 'b', chapter_number: 3, conclusion: '另一合成节点', evidence: [{ note_id: 'event_b', version: 1 }] }],
    edges: [{ source: 'a', target: 'b', relation: 'follows' }] }
  const added = { node_id: 'c', chapter_number: 3, conclusion: '合成新增节点', evidence: [{ note_id: 'event_c', version: 3 }] }
  const draft = structuredClone(old)
  draft.nodes.push(added)
  draft.edges.push({ source: 'b', target: 'c', relation: 'follows' })
  const patch = storylineEditPayload(old, draft)
  assert.deepEqual(patch.nodes, [added])
  assert.equal(patch.expected_version, 2)
  assert.deepEqual(patch.edges, [{ source: 'b', target: 'c', relation: 'follows' }])
  draft.nodes = draft.nodes.filter(n => n.node_id !== 'b')
  const removed = storylineEditPayload(old, draft)
  assert.deepEqual(removed.remove_node_ids, ['b'])
  assert.deepEqual(removed.edges, [])
})
