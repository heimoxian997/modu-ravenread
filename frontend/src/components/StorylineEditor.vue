<template>
  <section class="editor">
    <el-button @click="open" :loading="loading">编辑剧情线</el-button>
    <el-dialog v-model="visible" title="编辑剧情线" width="min(720px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" :before-close="close">
      <p v-if="error" role="alert">{{ error }}</p>
      <el-form v-if="draft" label-position="top" :disabled="saving">
        <el-form-item label="标题"><el-input v-model="draft.title" maxlength="80" /></el-form-item>
        <el-form-item label="概述"><el-input v-model="draft.overview" type="textarea" maxlength="300" /></el-form-item>
        <el-form-item label="当前阶段"><el-input v-model="draft.stage" maxlength="100" /></el-form-item>
        <p>节点保留原章号与证据；移除节点会同时移除相关连接，原始事件不会删除。</p>
        <div v-for="node in draft.nodes" :key="node.node_id" class="node">
          <label>第 {{ node.chapter_number }} 章</label>
          <el-input v-model="node.conclusion" type="textarea" maxlength="500" />
          <el-button text type="danger" :disabled="draft.nodes.length <= 1" @click="remove(node.node_id)">移除节点</el-button>
        </div>
        <StorylineNodeAdd :key="generation" :book-id="bookId" :chapters="chapters" :nodes="draft.nodes" :disabled="saving" @add="addNode" />
      </el-form>
      <template #footer><el-button :disabled="saving" @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="!draft" @click="save">保存修改</el-button></template>
    </el-dialog>
  </section>
</template>
<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import request from '@/utils/request'
import StorylineNodeAdd from '@/components/StorylineNodeAdd.vue'
import { storylineEditPayload } from '@/utils/manualNotes'
const props = defineProps({ bookId: Number, noteId: String, chapters: { type: Array, default: () => [] } })
const emit = defineEmits(['saved'])
const draft = ref(null), visible = ref(false), error = ref(''), loading = ref(false), saving = ref(false)
let original = null
const generation = ref(0)
watch(() => [props.bookId, props.noteId], () => { generation.value++; visible.value = false; draft.value = null; saving.value = false; loading.value = false })
async function open() {
  const version = ++generation.value
  loading.value = true; error.value = ''; draft.value = null; visible.value = true
  try {
    const r = await request({ url: `/books/${props.bookId}/storylines/${props.noteId}/edit`, silent: true })
    if (version !== generation.value) return
    if (!r.data.ok) throw new Error('当前阅读范围无法编辑此版本，请推进阅读后再试。')
    original = { ...r.data.data, edges: r.data.data.edges || [] }; draft.value = structuredClone(original)
  } catch (e) { if (version === generation.value) error.value = e.message || '加载失败' }
  finally { if (version === generation.value) loading.value = false }
}
function close(done) { if (!saving.value) done() }
function remove(id) { draft.value.nodes = draft.value.nodes.filter(n => n.node_id !== id); draft.value.edges = draft.value.edges.filter(e => e.source !== id && e.target !== id) }
function addNode({ node, edges }) { draft.value.nodes.push(node); draft.value.edges.push(...edges) }
async function save() {
  if (saving.value || !draft.value) return
  saving.value = true; error.value = ''
  const version = generation.value
  const payload = storylineEditPayload(original, draft.value)
  try {
    const r = await request({ url: `/books/${props.bookId}/reading-notes/upsert`, method: 'post', data: payload, silent: true })
    if (version !== generation.value) return
    if (!r.data.ok) throw new Error(r.data.error === 'version_conflict' ? '剧情线已被更新，请关闭后重新打开编辑，当前修改尚未保存。' : `保存失败：${r.data.error}；请核对节点证据。`)
    visible.value = false; emit('saved')
  } catch (e) { if (version === generation.value) error.value = e.message || '保存失败' }
  finally { if (version === generation.value) saving.value = false }
}
onBeforeUnmount(() => { generation.value++ })
</script>
<style scoped>.editor { margin:12px 0; }.node { margin:16px 0; } p { line-height:1.6; }</style>
