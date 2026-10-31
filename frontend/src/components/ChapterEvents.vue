<template>
  <section class="chapter-events-panel" aria-label="章节事件">
    <header><div><small v-if="chapter">第 {{ chapter }} 章 · 事件记录</small><h2>{{ chapterTitle || '选择一章，查看事件' }}</h2></div><el-button :loading="loading" @click="reload">刷新事件</el-button></header>
    <p class="hint">查看每章保存的事件，包括尚未归入剧情线的独立事件。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="!loading && !error && !items.length">{{ chapter ? '本章暂无已保存事件。有事件不代表已完整跟读，没有事件也不代表没有重要情节。' : '请先在阅读器打开正文章节。' }}</p>
    <p v-if="total">共 {{ total }} 条事件</p>
    <el-skeleton v-if="loading && !items.length" :rows="5" animated />
    <div class="event-grid">
    <article v-for="item in items" :key="item.note_id">
      <h3>{{ item.title }} <small>{{ item.storylines.length ? '已关联剧情线' : '独立事件' }}</small></h3>
      <p>{{ item.claim }}</p>
      <small v-if="item.basis === 'interpretation'">解释性判断：{{ item.perspective }}</small>
      <div><el-button v-for="line in item.storylines" :key="line.note_id" text @click="$emit('select-line', line.note_id)">{{ line.title }} →</el-button></div>
      <details><summary>查看原文依据 · {{ item.evidence.length }} 条</summary>
        <blockquote v-for="(quote, i) in item.evidence" :key="i">第 {{ quote.chapter_number }} 章：{{ quote.quote }}</blockquote>
      </details>
    </article>
    </div>
    <el-button v-if="nextOffset !== null" :loading="loading" @click="load(true)">加载更多事件</el-button>
  </section>
</template>
<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import request from '@/utils/request'
const props = defineProps({ bookId: Number, safeChapter: Number, chapterNumber: Number, chapterTitle: String })
defineEmits(['select-line'])
const chapter = computed(() => props.chapterNumber)
const items = ref([]), total = ref(0), nextOffset = ref(null), loading = ref(false), error = ref('')
let generation = 0
async function load(more = false) {
  const version = ++generation
  if (!more) { items.value = []; total.value = 0; nextOffset.value = null }
  error.value = ''
  if (!chapter.value || !props.safeChapter || chapter.value > props.safeChapter) { loading.value = false; return }
  loading.value = true
  try {
    const r = await request({ url: `/books/${props.bookId}/chapter-events/${chapter.value}`, params: { offset: more ? nextOffset.value : 0 }, silent: true })
    if (version !== generation) return
    if (!r.data.ok) throw new Error('事件暂不可见，请检查阅读进度后刷新。')
    const data = r.data.data
    items.value = more ? [...items.value, ...data.items] : data.items
    total.value = data.total; nextOffset.value = data.next_offset
  } catch (e) { if (version === generation) error.value = e.message || '事件加载失败' }
  finally { if (version === generation) loading.value = false }
}
function reload() { void load() }
watch(() => [props.bookId, props.safeChapter, props.chapterNumber], reload, { immediate: true })
onBeforeUnmount(() => { generation++ })
</script>
<style scoped>
.chapter-events-panel { min-width:0; }
.event-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%, 290px),1fr)); gap:16px; margin:20px 0; align-items:start; }
header { display:flex; align-items:center; flex-wrap:wrap; gap:14px; } header .el-select { width:260px; } h2 { font-family:var(--read-serif); font-size:24px; margin:6px 0; font-weight:500; }
article { padding:20px; border:1px solid var(--line-soft); border-radius:10px; overflow-wrap:anywhere; background:var(--bg); } h3 { font-size:15px; } small,.hint { opacity:.7; font-weight:normal; }
p,blockquote { line-height:1.8; } summary { cursor:pointer; } blockquote { margin:12px 0; padding-left:14px; border-left:2px solid var(--gold); }
</style>
