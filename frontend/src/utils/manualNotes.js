export const splitNames = value => [...new Set(String(value || '').split(/[,，、\n]+/).map(s => s.trim()).filter(Boolean))]

export function addedEventNode(event, chapter, conclusion, existingNodes, nodeId, connectAfter) {
  if (!event || event.chapter_number !== chapter) throw new Error('请选择本章的事件')
  if (!conclusion.trim() || conclusion.trim().length > 500) throw new Error('请填写 1–500 字的节点结论')
  if (existingNodes.some(n => n.chapter_number === chapter && n.evidence.some(e => e.note_id === event.note_id))) throw new Error('这条剧情线已经引用该事件，请选择其他事件')
  const node = { node_id: nodeId, chapter_number: chapter, conclusion: conclusion.trim(), evidence: [{ note_id: event.note_id, version: event.version }] }
  const edges = []
  if (connectAfter) {
    const anchor = existingNodes.find(n => n.node_id === connectAfter)
    if (!anchor || anchor.chapter_number > chapter) throw new Error('前置节点不能晚于新增节点')
    edges.push({ source: anchor.node_id, target: nodeId, relation: 'follows', basis: 'explicit' })
  }
  return { node, edges }
}

export function storylineEditPayload(original, draft) {
  const fields = ['node_id', 'chapter_number', 'conclusion', 'evidence', 'branch', 'basis', 'perspective']
  const nodes = draft.nodes.filter(n => {
    const previous = original.nodes.find(o => o.node_id === n.node_id)
    return !previous || fields.some(key => JSON.stringify(n[key]) !== JSON.stringify(previous[key]))
  }).map(n => Object.fromEntries(fields.filter(k => n[k] != null && (k !== 'perspective' || n[k].trim())).map(k => [k, n[k]])))
  const ids = new Set(draft.nodes.map(n => n.node_id))
  const edges = draft.edges.filter(e => ids.has(e.source) && ids.has(e.target) && !original.edges.some(o => o.source === e.source && o.target === e.target && o.relation === e.relation))
  return { kind: 'storyline_patch', note_id: original.note_id, expected_version: original.version,
    change_reason: '用户手动编辑', title: draft.title, overview: draft.overview, stage: draft.stage, nodes, edges,
    remove_node_ids: original.nodes.filter(n => !ids.has(n.node_id)).map(n => n.node_id) }
}

export function eventPayload(draft) {
  const title = draft.title.trim(), claim = draft.claim.trim(), quote = draft.quote.trim()
  const participants = splitNames(draft.participants)
  if (!title || title.length > 80) throw new Error('请填写 1–80 字的事件标题')
  if (!claim || claim.length > 180) throw new Error('请填写 1–180 字的事件内容')
  if (!participants.length || participants.length > 6 || participants.some(n => n.length > 80)) throw new Error('请填写 1–6 个原文中出现的参与人物或称呼，每个不超过 80 字')
  if (quote.length < 4 || quote.length > 120) throw new Error('请摘录本章 4–120 字的原文作为依据')
  if (draft.basis === 'interpretation' && !draft.perspective?.trim()) throw new Error('请注明这个判断的视角')
  return { kind: 'entry', chapter_number: draft.chapter_number, title, claim, basis: draft.basis,
    ...(draft.basis === 'interpretation' ? { perspective: draft.perspective.trim() } : {}),
    payload: { type: 'event', action: title, participants }, evidence: [{ chapter_number: draft.chapter_number, quote }] }
}

export function storylinePayload(draft) {
  const title = draft.title.trim(), overview = draft.overview.trim(), stage = draft.stage.trim()
  if (!title || title.length > 80 || !overview || overview.length > 300 || !stage || stage.length > 100) throw new Error('请填写标题、概述和当前阶段，并检查字数限制')
  if (!draft.nodes.length || draft.nodes.length > 40) throw new Error('请选择 1–40 个事件节点')
  const nodes = draft.nodes.map((n, i) => {
    if (!n.event || !n.conclusion.trim() || n.conclusion.trim().length > 500) throw new Error(`请为节点 ${i + 1} 选择事件并填写 1–500 字的结论`)
    if (n.event.chapter_number !== n.chapter_number) throw new Error('事件必须属于节点所选章节，请重新选择')
    return { node_id: `manual_${i + 1}`, chapter_number: n.chapter_number, conclusion: n.conclusion.trim(),
      evidence: [{ note_id: n.event.note_id, version: n.event.version }] }
  }).sort((a, b) => a.chapter_number - b.chapter_number)
  const entities = splitNames(draft.entities || draft.nodes.flatMap(n => n.event?.entities || []).join('、'))
  if (!entities.length || entities.length > 8 || entities.some(n => n.length > 80)) throw new Error('请填写 1–8 个关联人物或称呼，每个不超过 80 字')
  const edges = draft.connect ? nodes.slice(1).map((n, i) => ({ source: nodes[i].node_id, target: n.node_id, relation: 'follows', basis: 'explicit' })) : []
  return { kind: 'storyline', title, category: draft.category, entities, overview, stage, nodes, edges }
}
