<template>
  <div class="graph-scroll" tabindex="0" role="region" aria-label="按章节排列的剧情图，可滚动查看">
    <div class="graph-canvas" :style="{ width: `${layout.width}px`, height: `${layout.height}px` }">
      <div v-for="row in layout.rows" :key="row.chapter" class="chapter-guide" :style="{ top: `${row.y}px` }">
        <span>第 <strong>{{ row.chapter }}</strong> 章</span>
      </div>
      <svg class="connections" :width="layout.width" :height="layout.height" aria-hidden="true">
        <defs><marker id="story-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 z" fill="context-stroke" /></marker></defs>
        <g v-for="edge in layout.edges" :key="edge.id" :class="['connection', edge.relation, { highlighted: touchesSelected(edge) }]">
          <path :d="edge.path" marker-end="url(#story-arrow)" />
          <text v-if="edge.relation !== 'follows'" :x="edge.labelX" :y="edge.labelY">{{ relationLabels[edge.relation] }}</text>
        </g>
      </svg>
      <button v-for="node in layout.nodes" :key="node.node_id" type="button"
        :aria-pressed="selected === node.node_id" :aria-label="`第${node.chapter_number}章，${node.needs_revision ? '待核实节点' : node.conclusion}`"
        :class="['graph-node', { selected: selected === node.node_id, stale: node.needs_revision }]"
        :style="{ left: `${node.x}px`, top: `${node.y}px`, width: `${node.width}px`, height: `${node.height}px` }"
        @click="$emit('select', node.node_id)">
        <span class="node-meta"><span>{{ node.branch || '章节事件' }}</span><span v-if="node.needs_revision" class="needs-review">待核实</span><span v-else>查看依据 ↗</span></span>
        <span class="node-conclusion">{{ node.conclusion }}</span>
        <span class="node-foot">{{ node.evidence.length }} 条证据记录<span v-if="degree(node.node_id) > 2"> · 分支交汇</span></span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { layoutStoryline, relationLabels } from '@/utils/storylineGraph'
const props = defineProps({ nodes: { type: Array, default: () => [] }, edges: { type: Array, default: () => [] }, selected: String })
defineEmits(['select'])
const layout = computed(() => layoutStoryline(props.nodes, props.edges))
const touchesSelected = edge => edge.source === props.selected || edge.target === props.selected
const degree = id => props.edges.filter(e => e.source === id || e.target === id).length
</script>

<style scoped>
.graph-scroll { overflow: auto; max-height: 680px; background: var(--bg); border-radius: 12px; scrollbar-gutter: stable; }
.graph-scroll:focus-visible { outline: 2px solid var(--gold); outline-offset: 3px; }
.graph-canvas { position: relative; }
.chapter-guide { position: absolute; left: 0; right: 0; border-top: 1px solid var(--line-soft); pointer-events: none; }
.chapter-guide > span { display: block; width: 68px; padding: 14px 8px 0; color: var(--muted); font-size: 11px; text-align: center; }
.chapter-guide strong { display: block; color: var(--gold); font-family: var(--read-serif); font-size: 25px; font-weight: 400; line-height: 1.5; }
.connections { position: absolute; inset: 0; pointer-events: none; overflow: visible; }
.connection path { fill: none; stroke: var(--muted); stroke-width: 1.4; opacity: .65; }
.connection text { fill: var(--gold); font-size: 10px; text-anchor: middle; paint-order: stroke; stroke: var(--bg); stroke-width: 5px; }
.connection.clue_for path, .connection.reveals path { stroke: var(--gold); stroke-dasharray: 5 4; }
.connection.causes path { stroke: var(--teal); }
.connection.contradicts path { stroke: #c98b81; stroke-dasharray: 3 4; }
.connection.highlighted path { opacity: 1; stroke-width: 2.5; }
.graph-node { position: absolute; display: flex; flex-direction: column; gap: 10px; padding: 16px; border: 1px solid var(--line); border-radius: 10px; background: var(--card); color: var(--text); text-align: left; cursor: pointer; transition: border-color .15s, background .15s; }
.graph-node:hover { border-color: var(--gold); }
.graph-node:focus-visible { outline: 2px solid var(--gold); outline-offset: 3px; }
.graph-node.selected { border-color: var(--gold); background: var(--card-soft); box-shadow: inset 3px 0 0 var(--gold); }
.graph-node.stale { border-style: dashed; }
.node-meta, .node-foot { color: var(--muted); font-size: 10px; line-height: 1.5; }
.node-meta { display: flex; justify-content: space-between; gap: 8px; }
.node-meta > span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 115px; }
.needs-review { color: var(--gold); }
.node-conclusion { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; font-size: 13px; line-height: 1.65; }
.node-foot { margin-top: auto; }
@media (prefers-reduced-motion: reduce) { .graph-node { transition: none; } }
</style>
