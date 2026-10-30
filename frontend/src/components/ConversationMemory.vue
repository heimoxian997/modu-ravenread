<template>
  <details class="memory" @toggle="opened">
    <summary>讨论记忆</summary>
    <p>保存交流偏好、猜测与待讨论问题；不作为原著事实。长对话才自动压缩。</p>
    <button type="button" @click="load">刷新</button>
    <p v-if="error" role="alert">{{ error }}</p>
    <form @submit.prevent="save">
      <select v-model="kind" aria-label="记忆类型"><option v-for="(label, key) in kinds" :key="key" :value="key">{{ label }}</option></select>
      <textarea v-model="content" maxlength="1200" placeholder="希望记住或更正的内容" aria-label="记忆内容" />
      <button :disabled="busy || !content.trim()" type="submit">{{ editing ? '保存更正' : '记住' }}</button>
      <button v-if="editing || sources.length" type="button" @click="reset">取消编辑</button>
    </form>
    <article v-for="item in items" :key="item.key">
      <small>{{ kinds[item.kind] }} · {{ item.status === 'forgotten' ? '已遗忘' : item.origin }}</small>
      <p>{{ item.status === 'forgotten' ? '这条记忆不再自动召回' : item.content }}</p>
      <template v-if="item.status !== 'forgotten'">
        <button type="button" :disabled="busy" @click="choose(item)">更正</button>
        <button type="button" :disabled="busy" @click="forget(item)">忘记</button>
      </template>
      <details v-if="item.sources.length"><summary>查看来源消息</summary>
        <p v-for="id in item.sources" :key="id">#{{ id }} {{ sourceMap[id]?.role === 'user' ? '你' : '伴读' }}：{{ sourceMap[id]?.content || '来源当前不可见' }}</p>
      </details>
    </article>
    <p v-if="!items.length">还没有长期记忆；近期讨论仍保存在聊天记录中。</p>
  </details>
</template>
<script setup>
import { ref, computed, watch } from 'vue'
import request from '@/utils/request'
const props = defineProps({ bookId: Number, spoilers: Boolean, revision: Number })
const data = ref({ items: [], digests: [], sources: [] }), content = ref(''), kind = ref('preference')
const editing = ref(null), sources = ref([]), busy = ref(false), error = ref('')
let version = 0, visible = false
const kinds = { preference: '交流偏好', agreement: '约定', hypothesis: '暂时猜测', question: '待讨论问题', correction: '更正' }
const sourceMap = computed(() => Object.fromEntries(data.value.sources.map(s => [s.id, s])))
const items = computed(() => [
  ...data.value.items.map(i => ({ ...i, key: `m${i.id}`, origin: '手动记忆' })),
  ...data.value.digests.flatMap(d => d.items.map((i, n) => ({ ...i, key: `d${d.id}:${n}`, origin: '对话压缩', status: 'active' })))
])
function reset() { content.value = ''; editing.value = null; sources.value = [] }
function choose(item) { content.value = item.content; kind.value = item.kind; editing.value = item.id || null; sources.value = item.sources }
async function load() {
  const turn = ++version
  try {
    const result = await request({ url: `/books/${props.bookId}/agent/memory`, params: { spoilers: props.spoilers }, silent: true })
    if (turn === version) { data.value = result.data; error.value = '' }
  } catch (e) { if (turn === version) error.value = e.message || '无法读取记忆' }
}
async function submit(payload) {
  busy.value = true
  try {
    await request({ url: `/books/${props.bookId}/agent/memory`, method: 'post', data: { ...payload, spoilers: props.spoilers }, silent: true })
    reset(); await load()
  } catch (e) { error.value = e.message || '保存失败' }
  finally { busy.value = false }
}
function save() { return submit({ kind: kind.value, content: content.value, sources: sources.value, supersedes: editing.value }) }
function forget(item) { return submit({ kind: item.kind, content: '用户要求不再召回此记忆', sources: item.sources, supersedes: item.id || null, forget: true }) }
function opened(event) { visible = event.target.open; if (visible) void load() }
watch(() => [props.bookId, props.spoilers, props.revision], () => { version++; reset(); data.value = { items: [], digests: [], sources: [] }; if (visible) void load() })
</script>
<style scoped>
.memory { padding:10px 16px; border-bottom:1px solid #8884; max-height:40vh; overflow:auto; font-size:12px }
.memory p { white-space:pre-wrap; overflow-wrap:anywhere; margin:8px 0 }
.memory form { display:grid; gap:6px; margin:10px 0 }
.memory textarea { min-height:60px; color:inherit; background:transparent }
.memory button,.memory select { color:inherit; background:transparent; border:1px solid #8886; border-radius:4px; padding:4px 7px; margin-right:5px }
.memory article { border-top:1px solid #8884; padding:8px 0 }
</style>
