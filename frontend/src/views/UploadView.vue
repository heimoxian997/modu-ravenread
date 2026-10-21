<template>
  <div class="page upload-page">
    <div class="page-header">
      <div>
        <div class="eyebrow">导入向导</div>
        <h1 class="page-title">导入一本新书</h1>
        <p class="page-subtitle">
          上传 txt，告诉系统章节长什么样，确认章节边界后保存到本机书库。
        </p>
      </div>
      <el-tag effect="plain" type="success" round>
        <el-icon><Lock /></el-icon>
        全程离线
      </el-tag>
    </div>

    <div class="panel wizard">
      <div class="stepper-wrap">
        <el-steps :active="state.step" finish-status="success" align-center>
          <el-step title="选择文件" description="txt · 编码探测" />
          <el-step title="设置格式" description="章节 / 番外样例" />
          <el-step title="预览调整" description="改名 · 切类型 · 合并 · 拆分" />
          <el-step title="确认入库" description="元数据与保存" />
        </el-steps>
      </div>

      <div class="wizard-body">
        <!-- 步骤 1：上传 -->
        <section v-if="state.step === 0" class="step-section">
          <div class="step-heading">
            <span class="step-number">01</span>
            <div>
              <h2>选择小说 txt</h2>
              <p>文件会先进入临时草稿区，确认入库后才会成为正式书籍。</p>
            </div>
          </div>

          <FileDropzone
            :loading="uploading"
            :file="state.file"
            @file-selected="handleFileSelected"
          />

          <div class="upload-notes">
            <div>
              <el-icon><Files /></el-icon>
              <span>自动探测 UTF-8 / GB18030 等常见编码</span>
            </div>
            <div>
              <el-icon><Cpu /></el-icon>
              <span>解析只扫描标题行，不改动源文件</span>
            </div>
            <div>
              <el-icon><Delete /></el-icon>
              <span>超过 24 小时的未完成草稿会自动清理</span>
            </div>
          </div>

          <div v-if="state.draft" class="resume-row">
            <span>已有上传文件：{{ state.draft.filename }}</span>
            <el-button text type="primary" @click="state.step = 1">
              继续设置格式
            </el-button>
          </div>
        </section>

        <!-- 步骤 2：样例 -->
        <section v-else-if="state.step === 1" class="step-section">
          <div class="step-heading">
            <span class="step-number">02</span>
            <div>
              <h2>告诉墨读章节的格式</h2>
              <p>请照书中标题原样填写；样例只用于推导格式，标题文字不必每章相同。</p>
            </div>
          </div>

          <div v-if="state.draft" class="file-overview">
            <div class="file-meta">
              <div class="file-icon"><Document /></div>
              <div>
                <strong>{{ state.draft.filename }}</strong>
                <div class="meta-tags">
                  <el-tag size="small" effect="plain">{{ state.draft.encoding }}</el-tag>
                  <span>{{ formatCharCount(state.draft.char_count) }}</span>
                  <span>开头预览 500 字</span>
                </div>
              </div>
            </div>
            <pre class="head-preview">{{ state.draft.head }}</pre>
          </div>

          <el-alert
            type="info"
            :closable="false"
            show-icon
            title="最多支持两种章节格式同时生效"
            description="每个样例独立推导规则，相同格式会自动合并。比如同一本书同时有“chapter 3”和“第104章 chapter104”，各填一个样例即可；系统会同时识别两类标题。"
          />

          <div class="config-grid">
            <div class="config-card">
              <div class="config-title">
                <h3>章节标题样例 <span class="required-mark">必填 1–2 个</span></h3>
                <p>
                  每种章节格式填一个代表性标题；同格式会自动合并。行首的
                  ☆、●、※、- 等装饰符会自动忽略。
                </p>
              </div>
              <SampleInput
                v-model="state.chapterSamples"
                :min="1"
                :max="2"
                add-label="再添加一种章节格式"
                placeholder="例如：第一章 初见 / chapter 3"
                :examples="[
                  '第一章 初见',
                  '第1023章 大结局',
                  '1. 初见',
                  'chapter 3',
                  '第104章 chapter104'
                ]"
                :disabled="parsing"
              />
            </div>

            <div class="config-card">
              <div class="config-title">
                <h3>番外名称样例 <span class="optional-mark">可选 0–2 个</span></h3>
                <p>填写后，匹配到的章节会标记为番外；不填则默认没有番外。</p>
              </div>
              <SampleInput
                v-model="state.extraSamples"
                :min="0"
                :max="2"
                add-label="添加番外样例"
                placeholder="例如：番外一 春日"
                :examples="['番外一 春日', '番外：婚后', '番外篇·标题']"
                :disabled="parsing"
              />
            </div>
          </div>

          <div class="book-title-row">
            <label>书名 <span class="required-mark">必填</span></label>
            <el-input
              v-model="state.book.title"
              maxlength="200"
              show-word-limit
              placeholder="默认使用文件名，可修改"
            />
            <span class="field-hint">书名会显示在书库列表和详情页。</span>
          </div>

          <div v-if="parseError" class="parse-error">
            <el-alert
              type="error"
              :closable="false"
              show-icon
              :title="parseError"
            />
          </div>

          <div class="step-actions">
            <div>
              <el-button @click="state.step = 0">重新选择文件</el-button>
            </div>
            <el-button type="primary" :loading="parsing" @click="startParse()">
              <el-icon><MagicStick /></el-icon>
              开始解析章节
            </el-button>
          </div>
        </section>

        <!-- 步骤 3：预览与调整 -->
        <section v-else-if="state.step === 2" class="step-section preview-step">
          <div class="step-heading with-tools">
            <span class="step-number">03</span>
            <div>
              <h2>预览并调整章节</h2>
              <p>左侧可改名、切换正文/番外、合并；右侧选中文字可从该处拆分。</p>
            </div>
            <div class="heading-tools">
              <el-tag v-if="state.dirty" type="warning" effect="plain">有未保存调整</el-tag>
              <el-button :loading="parsing" @click="reparse">
                <el-icon><Refresh /></el-icon>
                重新解析
              </el-button>
            </div>
          </div>

          <div class="rule-bar">
            <span class="rule-label">识别规则</span>
            <el-tag
              v-for="(rule, index) in chapterRules"
              :key="`${rule.prefix}-${rule.unit}-${index}`"
              effect="plain"
              :type="multipleChapterRules ? 'warning' : 'primary'"
            >
              {{ multipleChapterRules ? `规则${index + 1}：` : '' }}
              {{ chapterRuleText(rule) }}
            </el-tag>
            <el-tag v-if="multipleChapterRules" type="warning" effect="dark">
              混合格式
            </el-tag>
            <el-tag v-if="extraPrefixText" effect="plain" type="warning">
              {{ extraPrefixText }}
            </el-tag>
            <el-tag v-if="state.parseResult?.rules?.fallback" type="danger" effect="dark">
              低置信度 · 自动切分
            </el-tag>
            <span v-if="chapterRules.length" class="rule-pattern">
              {{
                multipleChapterRules
                  ? `已同时启用 ${chapterRules.length} 条章节正则`
                  : chapterRules[0]?.pattern
              }}
            </span>
          </div>

          <div class="stats-grid">
            <div v-for="item in statItems" :key="item.label" class="stat-card">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
              <small v-if="item.sub">{{ item.sub }}</small>
            </div>
          </div>

          <WarningList :warnings="warnings" />

          <div v-if="!state.chapters.length" class="fallback-panel">
            <el-empty description="没有识别到章节" :image-size="92">
              <p class="fallback-copy">
                可能是章节格式与书中不一致。建议返回上一步核对样例；也可以显式选择降级方案，
                系统会按约 4000 字自动切分，结果会标记为低置信度。
              </p>
            </el-empty>
            <div class="fallback-actions">
              <el-checkbox v-model="fallbackChecked">
                我已知晓风险，按约 4000 字自动切分
              </el-checkbox>
              <el-button
                type="warning"
                :disabled="!fallbackChecked"
                :loading="parsing"
                @click="startParse({ fallback: true })"
              >
                使用降级切分
              </el-button>
            </div>
          </div>

          <div v-else class="editor-grid">
            <div class="editor-column">
              <ChapterTable
                :chapters="state.chapters"
                :selected-index="state.selectedIndex"
                @select="handleSelect"
                @rename="handleRename"
                @toggle-kind="handleToggleKind"
                @merge="handleMerge"
              />
            </div>
            <div class="editor-column">
              <ChapterPreview
                :chapter="selectedChapter"
                :content="previewContent"
                :loading="previewLoading"
                @split="handleSplit"
              />
            </div>
          </div>

          <div class="ack-row">
            <el-checkbox
              v-if="warnings.length"
              v-model="state.acknowledged"
              class="ack-checkbox"
            >
              我已阅读并知晓上述 {{ warnings.length }} 条警告
            </el-checkbox>
            <span v-else class="muted">没有发现需要特别确认的警告。</span>
          </div>

          <div class="step-actions">
            <el-button @click="state.step = 1">返回修改样例</el-button>
            <el-button
              type="primary"
              :disabled="!canProceedToConfirm"
              @click="goConfirm"
            >
              下一步：确认书籍信息
            </el-button>
          </div>
        </section>

        <!-- 步骤 4：元数据与确认 -->
        <section v-else class="step-section">
          <div class="step-heading">
            <span class="step-number">04</span>
            <div>
              <h2>确认书籍信息</h2>
              <p>这些字段都可以留空；简介和主角会显示在书籍详情页。</p>
            </div>
          </div>

          <div class="confirm-grid">
            <div class="confirm-form panel-soft">
              <BookForm v-model="state.book" :disabled="submitting" />
            </div>

            <aside class="confirm-summary">
              <div class="summary-book">
                <div class="summary-cover">
                  <span>{{ state.book.title?.slice(0, 1) || '书' }}</span>
                </div>
                <div>
                  <h3>{{ state.book.title || '未填写书名' }}</h3>
                  <p>{{ state.book.author || '作者未填写' }}</p>
                </div>
              </div>

              <dl class="summary-list">
                <div>
                  <dt>章节</dt>
                  <dd>
                    {{ stats.chapter_count || 0 }} 章
                    <span class="muted">
                      正文 {{ stats.main_chapter_count || 0 }} / 番外
                      {{ stats.extra_chapter_count || 0 }}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>字数</dt>
                  <dd>{{ formatCharCount(stats.total_chars || 0) }}</dd>
                </div>
                <div>
                  <dt>编码</dt>
                  <dd>{{ state.draft?.encoding || '—' }}</dd>
                </div>
                <div>
                  <dt>源文件</dt>
                  <dd class="text-ellipsis">{{ state.draft?.filename || '—' }}</dd>
                </div>
              </dl>

              <el-alert
                type="success"
                :closable="false"
                show-icon
                title="保存位置：本机 data/ 目录"
                description="确认后会写入 SQLite 与本地源文件，不会上传到任何服务器。"
              />

              <el-checkbox
                v-if="warnings.length"
                v-model="state.acknowledged"
                class="ack-checkbox"
              >
                我已阅读并知晓解析警告
              </el-checkbox>
            </aside>
          </div>

          <div class="step-actions">
            <el-button @click="state.step = 2">返回调整章节</el-button>
            <el-button
              type="primary"
              size="large"
              :loading="submitting"
              :disabled="!canConfirm"
              @click="confirmSave"
            >
              <el-icon><CircleCheck /></el-icon>
              确认并保存到书库
            </el-button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import FileDropzone from '@/components/FileDropzone.vue'
import SampleInput from '@/components/SampleInput.vue'
import ChapterTable from '@/components/ChapterTable.vue'
import ChapterPreview from '@/components/ChapterPreview.vue'
import WarningList from '@/components/WarningList.vue'
import BookForm from '@/components/BookForm.vue'
import { confirmDraftApi, getDraftContentApi, parseDraftApi, uploadDraftApi } from '@/api/draft'
import {
  applyParseResult,
  mergeWithPrevious,
  renameChapter,
  resetUploadState,
  splitChapter,
  toggleChapterKind,
  uploadState as state
} from '@/stores/upload'
import {
  formatCharCount,
  formatNumber,
  parseProtagonists
} from '@/utils/text'

const router = useRouter()
const uploading = ref(false)
const parsing = ref(false)
const submitting = ref(false)
const parseError = ref('')
const previewContent = ref('')
const previewLoading = ref(false)
const fallbackChecked = ref(false)
let previewSeq = 0

const warnings = computed(() => state.parseResult?.warnings || [])
const stats = computed(() => state.parseResult?.stats || {})
const selectedChapter = computed(() =>
  state.selectedIndex === null ? null : state.chapters[state.selectedIndex] || null
)
const canProceedToConfirm = computed(
  () =>
    state.chapters.length > 0 &&
    (!warnings.value.length || state.acknowledged)
)
const canConfirm = computed(
  () =>
    canProceedToConfirm.value &&
    String(state.book.title || '').trim().length > 0 &&
    !submitting.value
)

const chapterRules = computed(() => {
  const rules = state.parseResult?.rules
  if (!rules) return []
  if (Array.isArray(rules.chapters) && rules.chapters.length) {
    return rules.chapters
  }
  return rules.chapter ? [rules.chapter] : []
})

const multipleChapterRules = computed(() => chapterRules.value.length > 1)

function chapterRuleText(rule) {
  const prefix = rule.prefix ? `“${rule.prefix}”` : '无前缀'
  const unit = rule.unit ? `“${rule.unit}”` : '无单位'
  return `${prefix} + 章节数字 + ${unit}`
}

const extraPrefixText = computed(() => {
  const prefixes = state.parseResult?.rules?.extra?.prefixes
  if (!prefixes?.length) return ''
  return `番外前缀：${prefixes.join(' / ')}`
})

const statItems = computed(() => {
  const data = stats.value
  if (!data.chapter_count) return []
  return [
    { label: '总字数', value: formatCharCount(data.total_chars), sub: '源文本字符数' },
    {
      label: '章节总数',
      value: `${formatNumber(data.chapter_count)} 章`,
      sub: '正文 + 番外'
    },
    {
      label: '正文',
      value: `${formatNumber(data.main_chapter_count)} 章`,
      sub: '正文章节'
    },
    {
      label: '番外',
      value: `${formatNumber(data.extra_chapter_count)} 章`,
      sub: '番外章节'
    },
    {
      label: '章均字数',
      value: formatNumber(data.avg_word_count),
      sub: `最短 ${formatNumber(data.min_word_count)} 字`
    },
    {
      label: '最长章节',
      value: formatNumber(data.max_word_count),
      sub: data.longest_chapter?.title || '—'
    }
  ]
})

function scrollTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function filtered(list) {
  return (list || []).map((item) => String(item || '').trim()).filter(Boolean)
}

async function handleFileSelected(file) {
  if (uploading.value) return
  uploading.value = true
  parseError.value = ''
  try {
    const previousFile = file
    resetUploadState()
    state.file = previousFile
    const response = await uploadDraftApi(file)
    const draft = response.data
    state.draft = draft
    state.book.title = draft.filename.replace(/\.txt$/i, '').trim()
    state.step = 1
    ElMessage.success('上传成功，已探测编码并完成文本规范化')
    scrollTop()
  } catch {
    state.file = file
  } finally {
    uploading.value = false
  }
}

function validateConfig() {
  if (!state.draft) {
    ElMessage.error('请先上传 txt 文件')
    state.step = 0
    return false
  }
  const title = String(state.book.title || '').trim()
  if (!title) {
    ElMessage.error('请填写书名')
    return false
  }
  const chapterSamples = filtered(state.chapterSamples)
  if (chapterSamples.length < 1 || chapterSamples.length > 2) {
    ElMessage.error('请填写 1–2 个章节标题样例')
    return false
  }
  const extraSamples = filtered(state.extraSamples)
  if (extraSamples.length > 2) {
    ElMessage.error('番外样例最多 2 个')
    return false
  }
  state.chapterSamples = chapterSamples
  state.extraSamples = extraSamples
  return true
}

async function startParse({ fallback = false } = {}) {
  if (parsing.value || !validateConfig()) return
  if (state.dirty) {
    try {
      await ElMessageBox.confirm(
        '重新解析会丢弃当前所有手工调整（改名、切类型、合并、拆分），是否继续？',
        '重新解析',
        {
          type: 'warning',
          confirmButtonText: '丢弃调整并解析',
          cancelButtonText: '取消'
        }
      )
    } catch {
      return
    }
  }

  parsing.value = true
  parseError.value = ''
  try {
    const response = await parseDraftApi(state.draft.draft_token, {
      chapter_samples: filtered(state.chapterSamples),
      extra_samples: filtered(state.extraSamples),
      fallback
    })
    applyParseResult(response.data)
    fallbackChecked.value = false
    await nextTick()
    await loadPreview()
    ElMessage.success(
      response.data.chapters.length
        ? `解析完成：共 ${response.data.chapters.length} 章`
        : '解析完成，但没有识别到章节'
    )
    scrollTop()
  } catch (error) {
    const details = error?.data?.details
    parseError.value = details ? `${error.message}（${details}）` : error.message
  } finally {
    parsing.value = false
  }
}

async function reparse() {
  await startParse()
}

async function loadPreview() {
  const chapter = selectedChapter.value
  const token = state.draft?.draft_token
  if (!chapter || !token) {
    previewContent.value = ''
    previewLoading.value = false
    return
  }
  const sequence = ++previewSeq
  previewLoading.value = true
  try {
    const response = await getDraftContentApi(token, chapter.start, chapter.end)
    if (sequence === previewSeq) {
      previewContent.value = response.data.text || ''
    }
  } catch {
    if (sequence === previewSeq) previewContent.value = ''
  } finally {
    if (sequence === previewSeq) previewLoading.value = false
  }
}

watch(
  () => state.selectedIndex,
  () => {
    loadPreview()
  }
)

function handleSelect(index) {
  state.selectedIndex = index
}

function handleRename({ index, title }) {
  renameChapter(index, title)
}

function handleToggleKind(index) {
  toggleChapterKind(index)
}

function handleMerge(index) {
  if (index <= 0) return
  mergeWithPrevious(index)
  ElMessage.success('已与上一章合并')
  loadPreview()
}

function handleSplit(offset) {
  const chapter = selectedChapter.value
  if (!chapter) return
  const absolute = chapter.start + offset
  if (absolute <= chapter.start || absolute >= chapter.end) {
    ElMessage.warning('拆分位置无效，请重新选择')
    return
  }
  splitChapter(state.selectedIndex, absolute)
  ElMessage.success('已插入章节边界，可继续修改标题或类型')
  loadPreview()
}

function goConfirm() {
  if (!canProceedToConfirm.value) {
    ElMessage.warning('请先处理解析警告并勾选“已知晓”')
    return
  }
  state.step = 3
  scrollTop()
}

async function confirmSave() {
  if (submitting.value || !canConfirm.value) {
    if (warnings.value.length && !state.acknowledged) {
      ElMessage.warning('请先勾选“我已阅读并知晓解析警告”')
    } else if (!String(state.book.title || '').trim()) {
      ElMessage.error('书名不能为空')
    }
    return
  }
  if (!state.draft) {
    ElMessage.error('上传草稿已失效，请重新上传')
    return
  }
  submitting.value = true
  try {
    const response = await confirmDraftApi(state.draft.draft_token, {
      chapters: state.chapters.map((chapter) => ({
        start: chapter.start,
        end: chapter.end,
        title: String(chapter.title || '').trim(),
        kind: chapter.kind
      })),
      book: {
        title: String(state.book.title || '').trim(),
        author: String(state.book.author || '').trim() || null,
        synopsis: String(state.book.synopsis || '').trim() || null,
        protagonists: parseProtagonists(state.book.protagonistsText)
      }
    })
    const bookId = response.data.book_id
    resetUploadState()
    ElMessage.success('书籍已保存到本机书库')
    router.replace(`/books/${bookId}`)
  } catch {
    // 请求层已提示；保留页面状态让用户修正后重试
  } finally {
    submitting.value = false
  }
}

onBeforeRouteLeave(async () => {
  if (!state.dirty) return true
  try {
    await ElMessageBox.confirm(
      '当前有未保存的章节调整，离开后会丢失。确定离开吗？',
      '离开导入向导',
      {
        type: 'warning',
        confirmButtonText: '丢弃调整并离开',
        cancelButtonText: '继续编辑'
      }
    )
    resetUploadState()
    return true
  } catch {
    return false
  }
})

onMounted(() => {
  if (state.step === 0 && !state.draft && !state.file) {
    resetUploadState()
  }
})
</script>

<style scoped>
.upload-page {
  max-width: 1400px;
}

.eyebrow {
  margin-bottom: 7px;
  color: var(--amber);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.2em;
}

.wizard {
  overflow: hidden;
}

.stepper-wrap {
  padding: 26px 30px 22px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(180deg, var(--card-soft), var(--card));
}

.stepper-wrap :deep(.el-step__title) {
  font-size: 13px;
  font-weight: 600;
}

.stepper-wrap :deep(.el-step__description) {
  font-size: 11px;
}

.stepper-wrap :deep(.el-step__head.is-success),
.stepper-wrap :deep(.el-step__title.is-success) {
  color: var(--green);
  border-color: var(--green);
}

.wizard-body {
  min-height: 560px;
  padding: 30px;
}

.step-section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.step-heading {
  display: flex;
  align-items: flex-start;
  gap: 15px;
}

.step-heading.with-tools {
  align-items: center;
}

.step-number {
  display: grid;
  place-items: center;
  flex: none;
  width: 46px;
  height: 46px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--card-soft);
  color: var(--amber-deep);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 14px;
  font-weight: 700;
}

.step-heading h2 {
  margin-bottom: 6px;
  font-size: 21px;
}

.step-heading p {
  color: var(--ink-soft);
  line-height: 1.7;
}

.heading-tools {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-left: auto;
}

.upload-notes {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.upload-notes > div {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 11px 13px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--card-soft);
  color: var(--muted);
  font-size: 12px;
}

.upload-notes .el-icon {
  color: var(--amber);
}

.resume-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 10px;
  background: var(--card-soft);
  color: var(--muted);
  font-size: 13px;
}

.file-overview {
  display: grid;
  grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr);
  gap: 18px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: var(--card-soft);
}

.file-meta {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}

.file-icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 42px;
  height: 42px;
  border-radius: 11px;
  background: var(--gold-soft-bg);
  color: var(--amber-deep);
}

.file-icon svg {
  width: 20px;
  height: 20px;
}

.file-meta strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 7px;
  color: var(--muted);
  font-size: 11px;
}

.head-preview {
  max-height: 120px;
  margin: 0;
  overflow: auto;
  padding: 10px 12px;
  border-radius: 9px;
  background: var(--card);
  color: var(--muted);
  font-family: 'Songti SC', 'SimSun', serif;
  font-size: 12px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.config-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.config-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: var(--card-soft);
}

.config-title h3 {
  margin-bottom: 6px;
  font-size: 15px;
}

.config-title p {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.required-mark {
  margin-left: 4px;
  color: var(--danger-hi);
  font-size: 11px;
  font-weight: 400;
}

.optional-mark {
  margin-left: 4px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 400;
}

.book-title-row {
  display: flex;
  flex-direction: column;
  gap: 7px;
  max-width: 620px;
}

.book-title-row > label {
  font-size: 13px;
  font-weight: 600;
}

.field-hint {
  color: var(--muted);
  font-size: 11px;
}

.parse-error {
  margin-top: 2px;
}

.step-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 4px;
  padding-top: 20px;
  border-top: 1px solid var(--line-soft);
}

.rule-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 11px 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--card-soft);
  font-size: 12px;
}

.rule-label {
  color: var(--muted);
  font-weight: 600;
}

.rule-pattern {
  max-width: 100%;
  overflow: hidden;
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 13px 14px;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: var(--card);
}

.stat-card > span {
  color: var(--muted);
  font-size: 11px;
}

.stat-card strong {
  overflow: hidden;
  font-size: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stat-card small {
  overflow: hidden;
  color: var(--muted);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.editor-grid {
  display: grid;
  grid-template-columns: minmax(400px, 0.92fr) minmax(0, 1.08fr);
  gap: 18px;
  align-items: stretch;
}

.editor-column {
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--card);
}

.editor-column:first-child {
  padding-bottom: 10px;
}

.fallback-panel {
  padding: 24px;
  border: 1px dashed var(--line);
  border-radius: 14px;
  background: var(--card-soft);
}

.fallback-copy {
  max-width: 600px;
  margin: -4px auto 18px;
  color: var(--muted);
  line-height: 1.8;
}

.fallback-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 18px;
  flex-wrap: wrap;
}

.ack-row {
  min-height: 38px;
}

.ack-checkbox {
  white-space: normal;
}

.confirm-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.8fr);
  gap: 20px;
  align-items: start;
}

.panel-soft {
  padding: 22px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--card-soft);
}

.confirm-summary {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 20px;
  border-radius: 14px;
  background: var(--card-soft);
  color: var(--text);
}

.summary-book {
  display: flex;
  align-items: center;
  gap: 13px;
}

.summary-cover {
  display: grid;
  place-items: center;
  flex: none;
  width: 52px;
  height: 70px;
  border-radius: 5px 9px 9px 5px;
  background: linear-gradient(145deg, var(--gold), #8f7138);
  color: #241c10;
  font-family: 'STKaiti', 'KaiTi', serif;
  font-size: 26px;
  font-weight: 700;
  box-shadow: 4px 4px 0 #000000;
}

.summary-book h3 {
  margin-bottom: 5px;
  font-size: 17px;
}

.summary-book p {
  color: var(--muted);
  font-size: 12px;
}

.summary-list {
  display: flex;
  flex-direction: column;
  gap: 11px;
  margin: 0;
}

.summary-list > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 14px;
  padding-bottom: 9px;
  border-bottom: 1px solid var(--line-soft);
}

.summary-list dt {
  color: var(--muted);
  font-size: 11px;
}

.summary-list dd {
  margin: 0;
  max-width: 72%;
  font-size: 12px;
  text-align: right;
}

.summary-list .muted {
  display: block;
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.confirm-summary :deep(.el-alert) {
  --el-alert-bg-color: var(--surface-hover);
  --el-alert-title-color: var(--text);
  --el-alert-description-color: var(--muted);
  border: 1px solid var(--line-soft);
}

.confirm-summary :deep(.el-alert__icon) {
  color: var(--green-soft);
}

.confirm-summary .ack-checkbox {
  --el-checkbox-text-color: #d4cebd;
  --el-checkbox-checked-text-color: var(--text);
}

@media (max-width: 1180px) {
  .stats-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .editor-grid {
    grid-template-columns: 1fr;
  }

  .editor-column:first-child {
    max-height: none;
  }
}

@media (max-width: 900px) {
  .wizard-body {
    padding: 20px;
  }

  .config-grid,
  .file-overview,
  .confirm-grid {
    grid-template-columns: 1fr;
  }

  .upload-notes {
    grid-template-columns: 1fr;
  }

  .stepper-wrap {
    padding: 18px 12px;
  }

  .stepper-wrap :deep(.el-step__description) {
    display: none;
  }
}

@media (max-width: 600px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .step-heading.with-tools {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .heading-tools {
    width: 100%;
    margin-left: 61px;
  }

  .step-actions {
    align-items: stretch;
    flex-direction: column-reverse;
  }

  .step-actions .el-button {
    margin-left: 0;
  }
}
</style>
