const CARD_WIDTH = 236
const CARD_HEIGHT = 148
const COLUMN_GAP = 40
const ROW_GAP = 60
const LEFT = 76
const TOP = 28

export const relationLabels = {
  follows: '先后', causes: '因果', clue_for: '线索', reveals: '揭示', contradicts: '反驳'
}

export function layoutStoryline(nodes = [], edges = []) {
  const sorted = [...nodes].sort((a, b) => a.chapter_number - b.chapter_number)
  const branches = [...new Set(sorted.map(n => n.branch).filter(Boolean))]
  const rows = []
  const positioned = []
  let rowIndex = -1
  let previousChapter = null
  let occupied = new Set()
  let maxLane = 0
  for (const node of sorted) {
    if (node.chapter_number !== previousChapter) {
      rowIndex++
      occupied = new Set()
      previousChapter = node.chapter_number
      rows.push({ chapter: node.chapter_number, y: TOP + rowIndex * (CARD_HEIGHT + ROW_GAP) })
    }
    let lane = node.branch ? branches.indexOf(node.branch) : 0
    while (occupied.has(lane)) lane++
    occupied.add(lane)
    maxLane = Math.max(maxLane, lane)
    positioned.push({ ...node, x: LEFT + lane * (CARD_WIDTH + COLUMN_GAP),
      y: TOP + rowIndex * (CARD_HEIGHT + ROW_GAP), width: CARD_WIDTH, height: CARD_HEIGHT })
  }
  const byId = new Map(positioned.map(node => [node.node_id, node]))
  const connections = edges.flatMap((edge, index) => {
    const source = byId.get(edge.source)
    const target = byId.get(edge.target)
    if (!source || !target) return []
    let path
    let labelX
    let labelY
    if (source.y === target.y) {
      const forward = source.x < target.x
      const x1 = source.x + (forward ? CARD_WIDTH : 0)
      const x2 = target.x + (forward ? 0 : CARD_WIDTH)
      const y = source.y + CARD_HEIGHT / 2
      path = `M ${x1} ${y} C ${(x1+x2)/2} ${y-40}, ${(x1+x2)/2} ${y-40}, ${x2} ${y}`
      labelX = (x1+x2)/2
      labelY = y-34
    } else if (source.y < target.y) {
      const x1 = source.x + CARD_WIDTH / 2
      const x2 = target.x + CARD_WIDTH / 2
      const y1 = source.y + CARD_HEIGHT
      const y2 = target.y
      const skipsCard = positioned.some(node => node.y>source.y && node.y<target.y &&
        x1>=node.x && x1<=node.x+CARD_WIDTH)
      if (skipsCard) {
        const rail = LEFT + (maxLane+1)*(CARD_WIDTH+COLUMN_GAP)-22
        path = `M ${x1} ${y1} C ${x1} ${y1+20}, ${rail} ${y1+20}, ${rail} ${y1+40} L ${rail} ${y2-28} Q ${rail} ${y2-12}, ${x2} ${y2-12} L ${x2} ${y2}`
        labelX = rail-18
      } else {
        path = `M ${x1} ${y1} C ${x1} ${(y1+y2)/2}, ${x2} ${(y1+y2)/2}, ${x2} ${y2}`
        labelX = (x1+x2)/2 + 22
      }
      labelY = (y1+y2)/2
    } else {
      // Revelation links can point back; route them outside the chapter cards.
      const x1 = source.x + CARD_WIDTH
      const x2 = target.x + CARD_WIDTH
      const y1 = source.y + CARD_HEIGHT/2
      const y2 = target.y + CARD_HEIGHT/2
      const rail = LEFT + (maxLane+1)*(CARD_WIDTH+COLUMN_GAP) + 20 + index%3*12
      path = `M ${x1} ${y1} C ${rail} ${y1}, ${rail} ${y2}, ${x2} ${y2}`
      labelX = rail-8
      labelY = (y1+y2)/2
    }
    return [{ ...edge, id: `${edge.source}-${edge.target}-${edge.relation}`, path, labelX, labelY }]
  })
  const backward = connections.some(e => byId.get(e.source).y > byId.get(e.target).y)
  return { nodes: positioned, edges: connections, rows,
    width: Math.max(420, LEFT+(maxLane+1)*(CARD_WIDTH+COLUMN_GAP)+(backward ? 100 : 8)),
    height: Math.max(240, TOP+(rowIndex+1)*(CARD_HEIGHT+ROW_GAP)) }
}
