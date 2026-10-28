<template>
  <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" :title="detail ? `第 ${detail.number} 批 · 第 ${detail.chapter_number} 章` : '读取批次'" width="min(1080px, calc(100vw - 32px))" class="batch-editor-dialog" destroy-on-close>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-skeleton v-if="!detail" :rows="8" animated />
    <template v-else>
      <p class="editor-note">选择原文段落作为依据，不必手抄引文。可填写表达开头，由程序定位并补到句末。原文只读，人物理解和说话者仍需核对。当前版本 {{ detail.revision }}。</p>
      <el-alert v-if="detail.error" :title="roleErrorMessage(detail.error)" type="warning" :closable="false" />
      <p v-if="detail.status === 'done' && detail.error" class="editor-note">最近重抽未通过校验，下方保留的是上次有效结果。</p>
      <el-tabs v-model="tab">
        <el-tab-pane label="抽取结果" name="result">
          <el-form v-if="editor" :disabled="active || busy" label-position="top">
            <p v-if="!editor.observations.length" class="editor-note">还没有有效条目。可以查看原始返回，再手工整理；也可以稍后单独重试这一批。</p>
            <article v-for="(item, index) in editor.observations" :key="index" class="observation-editor" :class="{ 'has-issue': issuesFor(item).length }">
              <div class="observation-heading"><strong>条目 {{ index + 1 }}</strong><el-button text type="danger" @click="editor.observations.splice(index, 1)">删除条目</el-button></div>
              <p v-for="issue in issuesFor(item)" :key="issue.code" class="issue-note">{{ issueMessage(issue.code) }}</p>
              <div class="field-grid">
                <el-form-item label="类型"><el-select v-model="item.kind" @change="(kind) => changedKind(item, kind)"><el-option v-for="kind in kinds" :key="kind" :value="kind" :label="observationKindLabel(kind)" /></el-select></el-form-item>
                <el-form-item label="信息来源"><el-select v-model="item.attribution"><el-option v-for="(label, value) in attributions" :key="value" :value="value" :label="label" /></el-select></el-form-item>
                <el-form-item label="人物是否知情"><el-select v-model="item.knowledge"><el-option v-for="kind in knowledge" :key="kind" :value="kind" :label="knowledgeLabel(kind)" /></el-select></el-form-item>
              </div>
              <el-form-item label="结论"><el-input v-model="item.claim" type="textarea" :rows="2" maxlength="600" show-word-limit /></el-form-item>
              <el-form-item v-if="item.kind === 'speech'" label="说话者"><el-input :model-value="item.speaker || ''" @update:model-value="item.speaker = $event || null" /></el-form-item>
              <div v-for="(quote, i) in item.evidence" :key="i" class="quote-editor">
                <div class="quote-heading"><span>原文依据 {{ i + 1 }}</span><el-button text :disabled="item.evidence.length <= 1" @click="item.evidence.splice(i, 1)">移除</el-button></div>
                <template v-if="'paragraph_id' in quote">
                  <el-select v-model="quote.paragraph_id" aria-label="依据所在原文段落" filterable placeholder="选择本批原文段落">
                    <el-option v-for="paragraph in detail.paragraphs || []" :key="paragraph.paragraph_id" :value="paragraph.paragraph_id" :label="paragraphLabel(paragraph)" />
                  </el-select>
                  <el-input :model-value="quote.opening || ''" @update:model-value="quote.opening = $event || null" maxlength="80" placeholder="可选：表达开头，至少 4 字；留空保留完整段落" aria-label="表达开头（可选）" />
                  <div v-if="resolvedFor(item, i)" class="saved-location">
                    <p class="editor-note">程序定位结果 · {{ resolvedFor(item, i).match_method === 'opening_sentence' ? '已补到原文句末' : '完整原文段落' }}<span v-if="resolvedFor(item, i).location_warning"> · 开头未能可靠定位，请核对完整段落</span></p>
                    <pre class="paragraph-preview">{{ resolvedFor(item, i).quote }}</pre>
                  </div>
                  <div v-else-if="paragraphFor(quote)"><p class="editor-note">所选原文段落，保存后检查定位结果。</p><pre class="paragraph-preview">{{ paragraphFor(quote).text }}</pre></div>
                </template>
                <template v-else>
                  <el-select v-model="quote.scene_id" aria-label="引文所属片段"><el-option v-for="(scene, n) in detail.scenes" :key="scene.scene_id" :value="scene.scene_id" :label="`片段 ${n + 1} · 第 ${scene.chapter_number} 章`" /></el-select>
                  <el-input v-model="quote.quote" type="textarea" :rows="3" maxlength="400" show-word-limit placeholder="旧版引文，可继续编辑，或改为选择原文段落" />
                  <el-button text @click="item.evidence.splice(i, 1, emptyQuote())">改为选择原文段落</el-button>
                </template>
              </div>
              <el-button text :disabled="item.evidence.length >= 4" @click="item.evidence.push(emptyQuote())">＋ 添加依据</el-button>
            </article>
            <el-button :disabled="editor.observations.length >= 30" @click="addObservation">＋ 添加条目</el-button>
            <el-form-item label="资料不足或不能确定的部分（每行一项，最多 12 项）" class="gaps-field"><el-input :model-value="editor.gaps.join('\n')" @update:model-value="editor.gaps = $event.split('\n').filter((line) => line.trim())" type="textarea" :rows="3" /></el-form-item>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="本批原文" name="source"><article v-for="paragraph in detail.paragraphs || []" :key="paragraph.paragraph_id" class="source-scene"><h3>{{ paragraphLabel(paragraph, false) }}</h3><pre>{{ paragraph.text }}</pre></article></el-tab-pane>
        <el-tab-pane label="原始返回" name="raw"><p class="editor-note">模型的原始文本只作为草稿，未通过核验的内容不会进入最终人物资料。需要修复结构时，可在这里编辑 JSON 后保存。</p><el-input v-model="rawDraft" type="textarea" :rows="16" :disabled="active || busy" /><el-alert v-if="jsonError" :title="jsonError" type="error" :closable="false" /><el-button class="raw-save" :disabled="active || busy || !rawDraft" @click="saveJson">校验并保存 JSON 修订</el-button></el-tab-pane>
      </el-tabs>
    </template>
    <template #footer><span v-if="active" class="editor-note">当前任务运行中，暂停后才能编辑。</span><el-button @click="$emit('update:modelValue', false)">关闭</el-button><el-button v-if="detail && tab === 'result'" type="primary" :disabled="active || !editor" :loading="busy" @click="$emit('save')">校验并保存结果</el-button></template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { knowledgeLabel, observationKindLabel, roleErrorMessage } from '@/utils/rolePreparation'
const props = defineProps({ modelValue: Boolean, detail: Object, editor: Object, active: Boolean, busy: Boolean, error: String })
const emit = defineEmits(['update:modelValue', 'save', 'save-json'])
const tab = ref('result'), rawDraft = ref(''), jsonError = ref('')
let issueMap = new WeakMap(), resolvedMap = new WeakMap()
const issuesFor = (item) => issueMap.get(item) || []
const resolvedFor = (item, index) => {
  const saved = resolvedMap.get(item)
  return saved && saved.references[index] === JSON.stringify(item.evidence[index]) ? saved.evidence[index] : null
}
watch([() => props.detail, () => props.editor], () => {
  issueMap = new WeakMap(); resolvedMap = new WeakMap()
  for (const issue of props.detail?.issues || []) {
    const item = props.editor?.observations[issue.observation_index]
    if (item) issueMap.set(item, [...(issueMap.get(item) || []), issue])
  }
  for (const resolved of props.detail?.resolved_evidence || []) {
    const item = props.editor?.observations[resolved.observation_index]
    if (item) resolvedMap.set(item, { references: item.evidence.map((ref) => JSON.stringify(ref)), evidence: resolved.evidence })
  }
}, { immediate: true })
const kinds = ['identity', 'state', 'speech', 'action', 'belief', 'relationship', 'value', 'boundary', 'counterexample']
const knowledge = ['experienced', 'learned', 'believed', 'reader_only', 'unknown', 'not_applicable']
const attributions = { self_statement: '本人表达', action: '行为表现', narrator: '旁白叙述', other_statement: '他人评价', inference: '分析推断' }
const issueMessage = (code) => ({ quote_not_found: '本条引文未能匹配所选片段，请对照原文核对内容和片段归属。', quote_omitted: '本条引文含有省略内容，请改用短连续片段，或拆成多处原文依据。', quote_ambiguous: '本条引文在片段中出现多次，请扩充上下文以明确位置。', unknown_scene_reference: '本条依据不属于当前批次，请选择本批片段。', invalid_speech_attribution: '本人台词的说话者或信息来源标记不符合要求，请核对。', wrong_character: '返回的人物名称与准备目标不同，请修订原始 JSON 或重新抽取。' })[code] || '本条未通过校验，请对照原文修订。'
const paragraphFor = (quote) => props.detail?.paragraphs?.find((p) => p.paragraph_id === quote.paragraph_id)
const paragraphLabel = (paragraph, preview = true) => {
  const scene = props.detail.scenes.findIndex((s) => s.scene_id === paragraph.scene_id) + 1
  return `片段 ${scene} · 段落 ${paragraph.number}` + (preview ? ` · ${paragraph.text.slice(0, 45)}` : '')
}
const emptyQuote = () => ({ paragraph_id: '' })
function addObservation() { props.editor.observations.push({ kind: 'action', claim: '', attribution: 'narrator', knowledge: 'unknown', speaker: null, evidence: [emptyQuote()] }) }
function changedKind(item, kind) { if (kind === 'speech') { item.speaker = props.detail.character; item.attribution = 'self_statement'; item.knowledge = 'not_applicable' } }
function saveJson() {
  jsonError.value = ''
  try {
    const text = rawDraft.value.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '')
    emit('save-json', JSON.parse(text))
  } catch { jsonError.value = 'JSON 格式不正确，请修正后再保存，或使用抽取结果表单编辑。' }
}
watch(() => props.detail, (value) => { tab.value = 'result'; rawDraft.value = value?.raw_content || ''; jsonError.value = '' })
</script>

<style scoped>
.editor-note { color: var(--muted); font-size: 12px; line-height: 1.8; margin-bottom: 14px; }
.field-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.observation-editor { padding: 18px; margin-bottom: 16px; border: 1px solid var(--line-soft); background: var(--card-soft); border-radius: 10px; }
.observation-heading, .quote-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.observation-heading { margin-bottom: 12px; color: var(--gold); }.quote-heading { color: var(--muted); font-size: 12px; }
.has-issue { border-color: var(--danger-hi); }.issue-note { color: var(--danger-hi); font-size: 12px; line-height: 1.8; margin-bottom: 12px; }
.quote-editor { border-top: 1px solid var(--line-soft); padding-top: 8px; margin-top: 10px; }.quote-editor .el-select { width: 100%; margin-bottom: 9px; }
.source-scene { border-bottom: 1px solid var(--line-soft); margin-bottom: 18px; padding-bottom: 18px; }.source-scene h3 { color: var(--gold); font-size: 14px; margin-bottom: 12px; }.source-scene pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 14px/1.9 'Noto Serif SC', 'SimSun', serif; color: var(--text); }
.gaps-field { margin-top: 22px; }.raw-save { margin-top: 14px; }
.paragraph-preview { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 220px; overflow-y: auto; color: var(--text); background: var(--card-soft); padding: 12px; font: 14px/1.9 'Noto Serif SC', 'SimSun', serif; }
.saved-location { margin-top: 10px; }.saved-location .editor-note { margin-bottom: 0; }
@media (max-width: 850px) { .field-grid { grid-template-columns: 1fr; gap: 0; } }
</style>
