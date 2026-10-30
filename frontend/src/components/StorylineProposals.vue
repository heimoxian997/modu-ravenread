<template>
  <section class="proposals" aria-label="待确认剧情线建议">
    <h2>待确认的整理建议 <small>{{ items.length }} 条</small></h2>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重新加载</button></p>
    <p v-else-if="!items.length">暂无待确认建议。确认前，建议不会改变正式剧情线。</p>
    <details v-for="item in items" :key="item.id">
      <summary>{{ item.before ? '修改' : '新增' }}：{{ item.after.title }} · 第 {{ item.chapter }} 章</summary>
      <p>{{ item.reason }}</p>
      <div class="comparison">
        <article v-for="(value, index) in [item.before, item.after]" :key="index">
          <h3>{{ index ? '建议改为' : '当前内容' }}</h3>
          <template v-if="value">
            <strong>{{ value.title }}</strong><p>{{ value.overview }}</p><p>{{ value.stage }}</p>
            <ul><li v-for="node in value.nodes" :key="node.node_id">
              第 {{ node.chapter_number }} 章：{{ node.conclusion }}
              <small>依据：{{ node.evidence.map(e => `${e.note_id} v${e.version}`).join('、') }}</small>
            </li></ul>
            <p v-for="(edge, i) in value.edges" :key="i">{{ edge.source }} → {{ edge.target }} · {{ edge.relation }} {{ edge.reason }}</p>
          </template><p v-else>尚未建立</p>
        </article>
      </div>
      <details><summary>核对原文依据</summary><article v-for="(source, i) in item.evidence" :key="i">
        <strong>{{ source.title }}</strong><p>{{ source.claim }}</p>
        <blockquote v-for="(quote, j) in source.quotes" :key="j">第 {{ quote.chapter_number }} 章：{{ quote.quote }}</blockquote>
      </article></details>
      <p>请核对节点及连接；确认后生成新版本，删除节点不会删除底层事件。</p>
      <button :disabled="busy" @click="decide(item.id, 'accept')">确认应用</button>
      <button :disabled="busy" @click="decide(item.id, 'reject')">拒绝建议</button>
    </details>
  </section>
</template>
<script setup>
import { ref, watch } from 'vue'
import request from '@/utils/request'
const props = defineProps({ bookId: Number })
const emit = defineEmits(['changed'])
const items = ref([]), error = ref(''), busy = ref(false)
let generation = 0
async function load() {
  const version = ++generation
  items.value = []; error.value = ''
  try {
    const response = await request({ url: `/books/${props.bookId}/storyline-proposals`, silent: true })
    if (version !== generation) return
    if (!response.data.ok) throw new Error('建议暂时不可见，请检查阅读进度后刷新')
    items.value = response.data.data.items
  } catch (e) { if (version === generation) error.value = e.message || '建议加载失败' }
}
async function decide(id, action) {
  busy.value = true; error.value = ''
  const book = props.bookId
  try {
    const response = await request({ url: `/books/${book}/storyline-proposals/${id}/${action}`, method: 'post', silent: true })
    if (book !== props.bookId) return
    if (!response.data.ok) throw new Error('建议的来源或版本已变化，未应用；请让伴读重新核对，或拒绝此建议。')
    await load(); emit('changed')
  } catch (e) { if (book === props.bookId) error.value = e.message || '处理失败，建议已保留' }
  finally { busy.value = false }
}
watch(() => props.bookId, load, { immediate: true })
</script>
<style scoped>
.proposals { padding:20px; margin:20px 0; border:1px solid var(--line-soft); border-radius:12px; }
h2 { font-size:18px; } small { font-size:12px; opacity:.7; } summary, button { cursor:pointer; }
details { padding:14px 0; border-top:1px solid var(--line-soft); } p, li { line-height:1.7; }
.comparison { display:grid; grid-template-columns:1fr 1fr; gap:20px; } article { min-width:0; overflow-wrap:anywhere; }
li small { display:block; } button { margin:8px 12px 0 0; padding:8px 14px; }
@media(max-width:700px) { .comparison { grid-template-columns:1fr; } }
</style>
