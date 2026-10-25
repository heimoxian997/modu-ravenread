import test from 'node:test'
import assert from 'node:assert/strict'
import { layoutStoryline } from '../src/utils/storylineGraph.js'

const node = (id, chapter, branch) => ({ node_id: id, chapter_number: chapter, branch, conclusion: '合成事件', evidence: [] })

test('chapters sort vertically; parallel events and named branches never overlap', () => {
  const graph = layoutStoryline([node('last', 8), node('left', 3, '甲路'), node('right', 3, '乙路'), node('start', 1)])
  const positions = graph.nodes.map(n => `${n.x},${n.y}`)
  assert.equal(new Set(positions).size, 4)
  assert.deepEqual(graph.rows.map(r => r.chapter), [1, 3, 8])
  const [left, right] = ['left', 'right'].map(id => graph.nodes.find(n => n.node_id === id))
  assert.equal(left.y, right.y)
  assert.ok(Math.abs(left.x-right.x) >= left.width)
  assert.ok(graph.nodes.every(n => n.x+n.width < graph.width && n.y+n.height < graph.height))
})

test('branching and merging retain every valid explicit connection', () => {
  const graph = layoutStoryline([node('a', 1), node('b', 3, '甲路'), node('c', 3, '乙路'), node('d', 4)],
    [['a','b'],['a','c'],['b','d'],['c','d']].map(([source,target]) => ({source,target,relation:'follows'})))
  assert.equal(graph.edges.length, 4)
  assert.ok(graph.edges.every(e => e.path.startsWith('M ') && !e.path.includes('NaN')))
})

test('backward revelations and same-chapter edges have distinct drawable paths', () => {
  const graph = layoutStoryline([node('a', 1), node('b', 1), node('c', 4)], [
    {source:'c',target:'a',relation:'reveals'}, {source:'a',target:'b',relation:'clue_for'},
    {source:'missing',target:'a',relation:'follows'}])
  assert.equal(graph.edges.length, 2)
  assert.notEqual(graph.edges[0].path, graph.edges[1].path)
  assert.ok(graph.width > Math.max(...graph.nodes.map(n => n.x+n.width)))
})

test('empty graphs remain scrollable without invalid dimensions', () => {
  const graph = layoutStoryline()
  assert.ok(graph.width > 0 && graph.height > 0)
  assert.deepEqual(graph.nodes, [])
})
