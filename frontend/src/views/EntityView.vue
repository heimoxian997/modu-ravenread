<template>
  <div class="page entity-page" v-loading="loading">
    <template v-if="book">
      <div class="entity-top">
        <el-button text class="back-button" @click="backToBook">
          <el-icon><ArrowLeft /></el-icon>
          返回书籍详情
        </el-button>
        <div class="page-header entity-header">
          <div>
            <div class="entity-title-line">
              <h1 class="page-title">人物与世界</h1>
              <el-tag :type="statusTag.type" size="small" effect="plain">
                {{ statusTag.label }}
              </el-tag>
            </div>
            <p class="page-subtitle">
              {{ book.title }} · 人物卡与世界观事实的检索和人工修正
            </p>
          </div>
        </div>
      </div>

      <section class="panel status-panel">
        <div class="status-head">
          <div>
            <h2>索引状态</h2>
            <p class="muted">修正后需重建对应索引，检索才会使用新内容。</p>
          </div>
          <el-tag
            :type="status.available ? (status.complete ? 'success' : 'warning') : 'danger'"
            effect="plain"
          >
            {{ status.available ? (status.complete ? '覆盖全书' : '部分索引') : '不可用' }}
          </el-tag>
        </div>

        <div class="status-metrics">
          <div>
            <span>抽取覆盖</span>
            <strong>{{ status.chapters_processed }} / {{ status.total_main }}</strong>
          </div>
          <div>
            <span>人物变化</span>
            <strong>{{ status.character_snapshots }}</strong>
          </div>
          <div>
            <span>世界观</span>
            <strong>{{ status.lore_facts }}</strong>
          </div>
          <div>
            <span>已索引</span>
            <strong>{{ status.character_indexed }} / {{ status.lore_indexed }}</strong>
          </div>
        </div>

        <div class="status-actions">
          <el-button
            plain
            :loading="rebuildingLayer"
            :disabled="busy"
            @click="rebuildEntityLayer"
          >
            <el-icon><RefreshLeft /></el-icon>
            重新加工实体层
          </el-button>
          <el-select v-model="buildLayer" :disabled="busy" style="width: 124px">
            <el-option label="全部" value="all" />
            <el-option label="人物" value="character" />
            <el-option label="世界观" value="lore" />
          </el-select>
          <el-checkbox
            v-if="!status.complete"
            v-model="allowPartial"
            :disabled="busy"
          >
            仅试跑：允许部分索引
          </el-checkbox>
          <el-button
            type="primary"
            :loading="rebuilding"
            :disabled="!canBuild"
            @click="rebuildIndex"
          >
            <el-icon><Refresh /></el-icon>
            重建索引
          </el-button>
        </div>

        <div v-if="!status.available" class="warning-strip danger-strip">
          Qdrant 不可用：{{ status.error || '请确认本地服务与 data/qdrant 可用' }}
        </div>
        <div v-else-if="statusNeedsRebuild" class="warning-strip">
          索引可能过期（人工修正或实体层已变化），请重建对应索引。
        </div>
        <div v-if="!status.complete" class="warning-strip">
          部分索引：只覆盖已抽取章节（{{ status.chapters_processed }}/{{ status.total_main }}）。
          未勾选“允许部分索引”时无法重建。
        </div>
      </section>

      <section class="panel search-panel">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="人物" name="character">
            <div class="search-row">
              <el-input
                v-model="characterQuery"
                placeholder="搜索人物，例如：林晚"
                clearable
                :disabled="busy || !status.available"
                @keyup.enter="searchCurrent"
              />
              <el-select v-model="topK" style="width: 92px" :disabled="busy">
                <el-option :value="5" label="Top 5" />
                <el-option :value="10" label="Top 10" />
                <el-option :value="20" label="Top 20" />
              </el-select>
              <el-button
                type="primary"
                :loading="searching"
                :disabled="busy || !status.available"
                @click="searchCurrent"
              >
                <el-icon><Search /></el-icon>
                检索
              </el-button>
              <div class="chapter-pick">
                <span class="chapter-pick-label">查看章节</span>
                <el-input-number
                  v-model="cardChapter"
                  :min="0"
                  :max="maxChapterIdx"
                  :step="1"
                  step-strictly
                  size="default"
                  controls-position="right"
                  :disabled="busy || !status.available || !status.chapters_processed"
                  @change="onCardChapterChange"
                />
                <span class="chapter-pick-value">第 {{ cardChapter + 1 }} 章</span>
              </div>
            </div>

            <div class="alias-block">
              <div class="alias-row">
                <el-input
                  v-model="aliasQuery"
                  placeholder="称谓，例如：姐姐"
                  clearable
                  :disabled="busy || !status.available"
                  @keyup.enter="loadAliasCandidates"
                />
                <el-button
                  :loading="aliasLoading"
                  :disabled="busy || !status.available || !aliasQuery.trim()"
                  @click="loadAliasCandidates"
                >
                  查候选
                </el-button>
                <span class="alias-hint">
                  只统计到第 {{ cardChapter + 1 }} 章，不泄露后文
                </span>
              </div>
              <div v-if="aliasResult" class="alias-result">
                <div class="alias-head">
                  <el-tag
                    :type="aliasResult.ambiguous ? 'warning' : 'success'"
                    size="small"
                    effect="plain"
                  >
                    {{ aliasResult.ambiguous ? '有歧义' : '单一候选' }}
                  </el-tag>
                  <span class="alias-reason">{{ aliasResult.reason }}</span>
                  <span class="alias-count">
                    {{ aliasResult.total_candidates }} 个候选
                    <template v-if="aliasResult.truncated"> · 未全部展示</template>
                  </span>
                </div>
                <div v-if="aliasResult.candidates.length" class="alias-grid">
                  <div class="alias-grid-head">
                    <span>称呼</span>
                    <span>本章</span>
                    <span>累计</span>
                    <span>分数</span>
                    <span>出现章</span>
                  </div>
                  <div
                    v-for="(item, index) in aliasResult.candidates"
                    :key="item.character_id"
                    class="alias-grid-row"
                    :class="{ dim: !item.eligible }"
                  >
                    <span class="alias-name">
                      {{ item.display_name }}
                      <em v-if="index === 0">首选</em>
                    </span>
                    <span>{{ item.chapter_hits }}</span>
                    <span>{{ item.occurrence_count }}</span>
                    <span>{{ item.score }}</span>
                    <span class="alias-chapters">
                      {{ item.chapters.slice(0, 8).join('、')
                      }}{{ item.chapters.length > 8 ? '…' : '' }}
                    </span>
                  </div>
                </div>
                <el-empty
                  v-else
                  description="截至该章没有这个称谓的观测"
                  :image-size="50"
                />
              </div>
            </div>

            <div v-if="characterResults.length" class="result-list">
              <article
                v-for="item in characterResults"
                :key="item.snapshot_id"
                class="result-card"
                :class="{ clickable: !busy }"
                @click="openCharacter(item)"
              >
                <div class="result-head">
                  <div class="result-title">
                    <strong>{{ item.character }}</strong>
                    <el-tag v-if="item.edited" type="warning" size="small" effect="plain">已修正</el-tag>
                  </div>
                  <span class="result-meta">
                    截至第 {{ item.as_of_chapter + 1 }} 章 · rrf {{ item.rrf_score }}
                  </span>
                </div>
                <p class="result-line"><span>身份</span>{{ item.identity || '—' }}</p>
                <p class="result-line"><span>性格</span>{{ joinText(item.traits) }}</p>
                <p class="result-line"><span>关系</span>{{ joinText(item.relations) }}</p>
                <p class="result-line clamp"><span>已知</span>{{ joinText(item.known_facts) }}</p>
                <div class="result-scores">
                  dense {{ item.dense_score }} · sparse {{ item.sparse_score }}
                </div>
              </article>
            </div>
            <el-empty
              v-else-if="characterSearched && !searching"
              description="没有匹配的人物"
              :image-size="70"
            />
            <p v-else-if="!searching" class="search-hint">
              输入关键词后检索人物变化；同一角色只保留最高分事件，完整卡按需计算。
            </p>
          </el-tab-pane>

          <el-tab-pane label="世界观" name="lore">
            <div class="search-row">
              <el-input
                v-model="loreQuery"
                placeholder="搜索世界观，例如：LAX"
                clearable
                :disabled="busy || !status.available"
                @keyup.enter="searchCurrent"
              />
              <el-select v-model="topK" style="width: 92px" :disabled="busy">
                <el-option :value="5" label="Top 5" />
                <el-option :value="10" label="Top 10" />
                <el-option :value="20" label="Top 20" />
              </el-select>
              <el-button
                type="primary"
                :loading="searching"
                :disabled="busy || !status.available"
                @click="searchCurrent"
              >
                <el-icon><Search /></el-icon>
                检索
              </el-button>
              <div class="chapter-pick">
                <span class="chapter-pick-label">查看章节</span>
                <el-input-number
                  v-model="loreChapter"
                  :min="0"
                  :max="maxChapterIdx"
                  :step="1"
                  step-strictly
                  size="default"
                  controls-position="right"
                  :disabled="busy || !status.available || !status.chapters_processed"
                  @change="onLoreChapterChange"
                />
                <span class="chapter-pick-value">第 {{ loreChapter + 1 }} 章</span>
              </div>
            </div>

            <div v-if="loreResults.length" class="result-list">
              <article
                v-for="item in loreResults"
                :key="item.lore_id"
                class="result-card"
                :class="{ clickable: !busy }"
                @click="openLore(item)"
              >
                <div class="result-head">
                  <div class="result-title">
                    <strong>{{ item.canonical_name }}</strong>
                    <el-tag v-if="item.edited" type="warning" size="small" effect="plain">已修正</el-tag>
                  </div>
                  <span class="result-meta">
                    {{ item.category || '未分类' }} · 截至第
                    {{ (item.query_chapter ?? loreChapter) + 1 }} 章 · 出自第
                    {{ item.chapter_idx + 1 }} 章 · rrf {{ item.rrf_score }}
                  </span>
                </div>
                <p class="result-line"><span>概述</span>{{ item.summary || '—' }}</p>
                <p class="result-line clamp"><span>细节</span>{{ joinText(item.details) }}</p>
                <p class="result-line"><span>关联</span>{{ joinText(item.entities) }}</p>
                <div class="result-scores">
                  dense {{ item.dense_score }} · sparse {{ item.sparse_score }}
                </div>
              </article>
            </div>
            <el-empty
              v-else-if="loreSearched && !searching"
              description="没有匹配的世界观"
              :image-size="70"
            />
            <p v-else-if="!searching" class="search-hint">
              输入关键词后检索世界观事实。
            </p>
          </el-tab-pane>
        </el-tabs>
      </section>
    </template>

    <el-empty v-else-if="!loading" description="没有找到这本书" :image-size="110">
      <el-button type="primary" @click="router.push('/')">返回书库</el-button>
    </el-empty>

    <el-drawer
      v-model="drawerVisible"
      :title="drawerTitle"
      size="520px"
      destroy-on-close
    >
      <div v-loading="detailLoading" class="drawer-body">
        <template v-if="detail && detailType === 'character'">
          <div class="drawer-meta">
            <span>第 {{ (detail.query_chapter ?? cardChapter) + 1 }} 章时的人物卡</span>
            <el-tag v-if="detail.edited" type="warning" size="small" effect="plain">已修正</el-tag>
          </div>
          <div v-if="detail.character.aliases?.length" class="alias-line">
            别称：{{ detail.character.aliases.join('、') }}
          </div>

          <template v-if="!editing">
            <div class="detail-field"><span>身份</span><p>{{ detail.effective.identity || '—' }}</p></div>
            <div class="detail-field"><span>性格</span><p>{{ joinText(detail.effective.traits) }}</p></div>
            <div class="detail-field"><span>关系</span><p>{{ joinText(detail.effective.relations) }}</p></div>
            <div class="detail-field"><span>已知</span><p>{{ joinText(detail.effective.known_facts) }}</p></div>
            <div class="detail-field"><span>代表台词</span><p>{{ detail.effective.voice_sample || '—' }}</p></div>
            <div class="detail-field"><span>人物弧光</span><p>{{ detail.effective.arc || '—' }}</p></div>
            <div class="drawer-actions">
              <el-button
                type="primary"
                :disabled="busy"
                @click="startEdit"
              >
                <el-icon><Edit /></el-icon>
                编辑
              </el-button>
              <el-button
                plain
                :disabled="busy || !detail.edited"
                @click="resetCurrent"
              >
                <el-icon><RefreshLeft /></el-icon>
                恢复原始
              </el-button>
            </div>
          </template>

          <template v-else>
            <div class="detail-field">
              <span>从第几章起生效</span>
              <div class="from-chapter-row">
                <el-input-number
                  v-model="characterForm.from_chapter"
                  :min="0"
                  :max="maxChapterIdx"
                  :step="1"
                  step-strictly
                  :disabled="saving"
                  controls-position="right"
                />
                <span class="from-chapter-hint">
                  第 {{ characterForm.from_chapter + 1 }} 章及以后使用这份修正
                </span>
              </div>
            </div>
            <div class="detail-field">
              <span>身份</span>
              <el-input v-model="characterForm.identity" type="textarea" :rows="2" />
            </div>
            <div class="detail-field">
              <span>性格（一行一条）</span>
              <el-input v-model="characterForm.traits" type="textarea" :rows="3" />
            </div>
            <div class="detail-field">
              <span>关系（一行一条）</span>
              <el-input v-model="characterForm.relations" type="textarea" :rows="3" />
            </div>
            <div class="detail-field">
              <span>已知（一行一条）</span>
              <el-input v-model="characterForm.known_facts" type="textarea" :rows="5" />
            </div>
            <div class="detail-field">
              <span>代表台词</span>
              <el-input v-model="characterForm.voice_sample" type="textarea" :rows="2" />
            </div>
            <div class="detail-field">
              <span>人物弧光</span>
              <el-input v-model="characterForm.arc" type="textarea" :rows="2" />
            </div>
            <div class="drawer-actions">
              <el-button type="primary" :loading="saving" @click="saveCharacter">保存修正</el-button>
              <el-button :disabled="saving" @click="editing = false">取消</el-button>
            </div>
          </template>
        </template>

        <template v-if="detail && detailType === 'lore'">
          <div class="drawer-meta">
            <span v-if="detail.available">
              {{ detail.lore.canonical_name }} · 第
              {{ (detail.query_chapter ?? loreChapter) + 1 }} 章时 · 出自第
              {{ detail.lore.chapter_idx + 1 }} 章
            </span>
            <span v-else>
              {{ detail.lore.canonical_name }} · 第
              {{ (detail.query_chapter ?? loreChapter) + 1 }} 章尚未出现
            </span>
            <el-tag v-if="detail.edited" type="warning" size="small" effect="plain">已修正</el-tag>
          </div>
          <template v-if="!editing">
            <el-alert
              v-if="!detail.available"
              type="info"
              :closable="false"
              show-icon
              title="该章还没有这条世界观，把查看章节往后调即可看到。"
              style="margin-bottom: 12px"
            />
            <div class="detail-field"><span>类型</span><p>{{ detail.effective.category || '未分类' }}</p></div>
            <div class="detail-field"><span>别名</span><p>{{ joinText(detail.effective.aliases) }}</p></div>
            <div class="detail-field"><span>概述</span><p>{{ detail.effective.summary || '—' }}</p></div>
            <div class="detail-field"><span>细节</span><p>{{ joinText(detail.effective.details) }}</p></div>
            <div class="detail-field"><span>关联</span><p>{{ joinText(detail.effective.entities) }}</p></div>
            <div class="drawer-actions">
              <el-button
                type="primary"
                :disabled="busy"
                @click="startEdit"
              >
                <el-icon><Edit /></el-icon>
                编辑
              </el-button>
              <el-button
                plain
                :disabled="busy || !detail.edited"
                @click="resetCurrent"
              >
                <el-icon><RefreshLeft /></el-icon>
                恢复原始
              </el-button>
            </div>
          </template>
          <template v-else>
            <div class="detail-field">
              <span>类型</span>
              <el-input v-model="loreForm.category" />
            </div>
            <div class="detail-field">
              <span>别名（一行一条）</span>
              <el-input v-model="loreForm.aliases" type="textarea" :rows="2" />
            </div>
            <div class="detail-field">
              <span>概述</span>
              <el-input v-model="loreForm.summary" type="textarea" :rows="3" />
            </div>
            <div class="detail-field">
              <span>细节（一行一条）</span>
              <el-input v-model="loreForm.details" type="textarea" :rows="4" />
            </div>
            <div class="detail-field">
              <span>关联（一行一条）</span>
              <el-input v-model="loreForm.entities" type="textarea" :rows="3" />
            </div>
            <div class="drawer-actions">
              <el-button type="primary" :loading="saving" @click="saveLore">保存修正</el-button>
              <el-button :disabled="saving" @click="editing = false">取消</el-button>
            </div>
          </template>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Edit, Refresh, RefreshLeft, Search } from '@element-plus/icons-vue'
import { getBookApi } from '@/api/book'
import {
  buildEntityIndexApi,
  getAliasCandidatesApi,
  getCharacterDetailApi,
  getEntityIndexStatusApi,
  getLoreDetailApi,
  rebuildEntityLayerApi,
  resetCharacterCardApi,
  resetLoreApi,
  searchCharactersApi,
  searchLoreApi,
  updateCharacterCardApi,
  updateLoreApi
} from '@/api/entity'

const route = useRoute()
const router = useRouter()
const bookId = computed(() => Number(route.params.id))

const loading = ref(false)
const book = ref(null)
const status = ref(defaultStatus())
const rebuilding = ref(false)
const rebuildingLayer = ref(false)
const searching = ref(false)
const buildLayer = ref('all')
const allowPartial = ref(false)
const activeTab = ref('character')
const topK = ref(5)
const characterQuery = ref('')
const loreQuery = ref('')
const cardChapter = ref(0)
const loreChapter = ref(0)
const aliasQuery = ref('')
const aliasLoading = ref(false)
const aliasResult = ref(null)
const openedCharacterId = ref(null)
const openedLoreId = ref(null)
const characterResults = ref([])
const loreResults = ref([])
const characterSearched = ref(false)
const loreSearched = ref(false)
const drawerVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const detailType = ref('character')
const editing = ref(false)
const saving = ref(false)
const characterForm = ref(emptyCharacterForm())
const loreForm = ref(emptyLoreForm())

function defaultStatus() {
  return {
    available: true,
    total_main: 0,
    chapters_processed: 0,
    complete: false,
    character_snapshots: 0,
    character_indexed: 0,
    character_dirty: false,
    character_needs_rebuild: false,
    lore_facts: 0,
    lore_indexed: 0,
    lore_dirty: false,
    lore_needs_rebuild: false,
    collections: {},
    error: null
  }
}

function emptyCharacterForm() {
  return {
    from_chapter: 0,
    identity: '',
    traits: '',
    relations: '',
    known_facts: '',
    voice_sample: '',
    arc: ''
  }
}

function emptyLoreForm() {
  return {
    category: '',
    aliases: '',
    summary: '',
    details: '',
    entities: ''
  }
}

const statusNeedsRebuild = computed(
  () => status.value.character_needs_rebuild || status.value.lore_needs_rebuild
)

const statusTag = computed(() => {
  if (!status.value.available) return { label: '不可用', type: 'danger' }
  if (statusNeedsRebuild.value) return { label: '需重建', type: 'warning' }
  if (!status.value.character_indexed && !status.value.lore_indexed) {
    return { label: '未建立', type: 'info' }
  }
  if (!status.value.complete) return { label: '部分索引', type: 'warning' }
  return { label: '已建立', type: 'success' }
})

const busy = computed(
  () => rebuilding.value || rebuildingLayer.value || searching.value
)

const maxChapterIdx = computed(() =>
  Math.max(0, Number(status.value.chapters_processed || 0) - 1)
)

const canBuild = computed(
  () =>
    status.value.available &&
    !busy.value &&
    (status.value.complete || allowPartial.value)
)

const drawerTitle = computed(() => {
  if (!detail.value) return '详情'
  if (detailType.value === 'character') return detail.value.character.canonical_name
  return detail.value.lore.canonical_name
})

function joinText(value) {
  if (!Array.isArray(value) || !value.length) return '—'
  return value
    .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
    .join('、')
}

function toLines(value) {
  if (!Array.isArray(value)) return ''
  return value
    .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
    .join('\n')
}

function fromLines(value) {
  return String(value || '')
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function load() {
  loading.value = true
  try {
    const [bookResponse, statusResponse] = await Promise.all([
      getBookApi(bookId.value),
      getEntityIndexStatusApi(bookId.value).catch(() => ({ data: defaultStatus() }))
    ])
    book.value = bookResponse.data
    status.value = { ...defaultStatus(), ...(statusResponse.data || {}) }
    allowPartial.value = false
    cardChapter.value = maxChapterIdx.value
    loreChapter.value = maxChapterIdx.value
    if (!status.value.complete) {
      allowPartial.value = false
    }
  } catch {
    book.value = null
  } finally {
    loading.value = false
  }
}

async function refreshStatus() {
  try {
    const response = await getEntityIndexStatusApi(bookId.value)
    status.value = { ...defaultStatus(), ...(response.data || {}) }
  } catch {
    status.value = { ...status.value, available: false }
  }
}

async function rebuildEntityLayer() {
  try {
    await ElMessageBox.confirm(
      '将从章节抽取 JSON 重新生成人物、别名观测、快照和世界观。现有快照会被替换，人工修正会保留；完成后还需重建索引。是否继续？',
      '重新加工实体层',
      {
        type: 'warning',
        confirmButtonText: '重新加工',
        cancelButtonText: '取消'
      }
    )
  } catch {
    return
  }
  rebuildingLayer.value = true
  try {
    const response = await rebuildEntityLayerApi(bookId.value)
    const data = response.data
    characterResults.value = []
    loreResults.value = []
    characterSearched.value = false
    loreSearched.value = false
    await refreshStatus()
    ElMessage.success(
      `实体层已重新加工：${data.chapters_processed} 章，${data.characters} 人物；请重建索引`
    )
  } catch {
    // 请求层已提示
  } finally {
    rebuildingLayer.value = false
  }
}

async function rebuildIndex() {
  if (!status.value.available || rebuildingLayer.value) return
  if (!status.value.complete && !allowPartial.value) {
    ElMessage.warning('抽取尚未覆盖全书，请先勾选“允许部分索引”')
    return
  }
  rebuilding.value = true
  try {
    const response = await buildEntityIndexApi(bookId.value, {
      layer: buildLayer.value,
      allow_partial: allowPartial.value
    })
    const data = response.data
    ElMessage.success(
      `索引已重建：人物 ${data.character_count} / 世界观 ${data.lore_count}`
    )
    await refreshStatus()
    if (activeTab.value === 'character' && characterQuery.value.trim()) {
      await runSearch('character')
    } else if (activeTab.value === 'lore' && loreQuery.value.trim()) {
      await runSearch('lore')
    }
  } catch {
    // 请求层已提示
  } finally {
    rebuilding.value = false
  }
}

async function searchCurrent() {
  if (activeTab.value === 'character') {
    await runSearch('character')
  } else {
    await runSearch('lore')
  }
}

async function runSearch(layer) {
  if (!status.value.available) return
  const query = (layer === 'character' ? characterQuery.value : loreQuery.value).trim()
  if (!query) {
    ElMessage.warning('请输入检索关键词')
    return
  }
  searching.value = true
  try {
    if (layer === 'character') {
      const response = await searchCharactersApi(bookId.value, {
        query,
        top_k: topK.value,
        chapter_idx: cardChapter.value
      })
      characterResults.value = response.data || []
      characterSearched.value = true
    } else {
      const response = await searchLoreApi(bookId.value, {
        query,
        top_k: topK.value,
        chapter_idx: loreChapter.value
      })
      loreResults.value = response.data || []
      loreSearched.value = true
    }
  } catch {
    // 请求层已提示
  } finally {
    searching.value = false
  }
}

async function loadCharacterDetail(characterId) {
  detailLoading.value = true
  detail.value = null
  try {
    const response = await getCharacterDetailApi(
      bookId.value,
      characterId,
      cardChapter.value
    )
    detail.value = response.data
    return true
  } catch {
    return false
  } finally {
    detailLoading.value = false
  }
}

async function openCharacter(item) {
  if (busy.value) return
  detailType.value = 'character'
  drawerVisible.value = true
  editing.value = false
  openedCharacterId.value = item.character_id
  const ok = await loadCharacterDetail(item.character_id)
  if (!ok) drawerVisible.value = false
}

async function loadAliasCandidates() {
  const alias = aliasQuery.value.trim()
  if (!alias) return
  aliasLoading.value = true
  try {
    const response = await getAliasCandidatesApi(
      bookId.value,
      alias,
      cardChapter.value
    )
    aliasResult.value = response.data
  } catch (error) {
    if (error?.isBusiness) {
      aliasResult.value = null
      ElMessage.error(error.message || '查询失败')
    }
  } finally {
    aliasLoading.value = false
  }
}

async function onCardChapterChange() {
  editing.value = false
  if (characterQuery.value.trim() && characterSearched.value) {
    await runSearch('character')
  }
  if (aliasResult.value && aliasQuery.value.trim()) {
    await loadAliasCandidates()
  }
  if (
    detailType.value === 'character' &&
    openedCharacterId.value !== null &&
    drawerVisible.value
  ) {
    await loadCharacterDetail(openedCharacterId.value)
  }
}

async function loadLoreDetail(loreId) {
  detailLoading.value = true
  detail.value = null
  try {
    const response = await getLoreDetailApi(
      bookId.value,
      loreId,
      loreChapter.value
    )
    detail.value = response.data
    return true
  } catch {
    return false
  } finally {
    detailLoading.value = false
  }
}

async function openLore(item) {
  if (busy.value) return
  detailType.value = 'lore'
  drawerVisible.value = true
  editing.value = false
  openedLoreId.value = item.lore_id
  const ok = await loadLoreDetail(item.lore_id)
  if (!ok) drawerVisible.value = false
}

async function onLoreChapterChange() {
  editing.value = false
  if (loreQuery.value.trim() && loreSearched.value) {
    await runSearch('lore')
  }
  if (
    detailType.value === 'lore' &&
    openedLoreId.value !== null &&
    drawerVisible.value
  ) {
    await loadLoreDetail(openedLoreId.value)
  }
}

function startEdit() {
  if (!detail.value || busy.value) return
  if (detailType.value === 'character') {
    const effective = detail.value.effective
    characterForm.value = {
      from_chapter:
        detail.value.override_from_chapter ?? detail.value.query_chapter ?? cardChapter.value,
      identity: effective.identity || '',
      traits: toLines(effective.traits),
      relations: toLines(effective.relations),
      known_facts: toLines(effective.known_facts),
      voice_sample: effective.voice_sample || '',
      arc: effective.arc || ''
    }
  } else {
    const effective = detail.value.effective
    loreForm.value = {
      category: effective.category || '',
      aliases: toLines(effective.aliases),
      summary: effective.summary || '',
      details: toLines(effective.details),
      entities: toLines(effective.entities)
    }
  }
  editing.value = true
}

async function saveCharacter() {
  saving.value = true
  try {
    const response = await updateCharacterCardApi(
      bookId.value,
      detail.value.character.id,
      {
        from_chapter: characterForm.value.from_chapter,
        chapter_idx: cardChapter.value,
        identity: characterForm.value.identity || null,
        traits: fromLines(characterForm.value.traits),
        relations: fromLines(characterForm.value.relations),
        known_facts: fromLines(characterForm.value.known_facts),
        voice_sample: characterForm.value.voice_sample || null,
        arc: characterForm.value.arc || null
      }
    )
    detail.value = response.data
    editing.value = false
    await refreshStatus()
    ElMessage.success(
      `修正从第 ${characterForm.value.from_chapter + 1} 章起生效；重建人物索引后检索更新`
    )
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

async function saveLore() {
  saving.value = true
  try {
    const response = await updateLoreApi(bookId.value, detail.value.lore.id, {
      chapter_idx: loreChapter.value,
      category: loreForm.value.category || null,
      aliases: fromLines(loreForm.value.aliases),
      summary: loreForm.value.summary || null,
      details: fromLines(loreForm.value.details),
      entities: fromLines(loreForm.value.entities)
    })
    detail.value = response.data
    editing.value = false
    await refreshStatus()
    ElMessage.success(
      `修正作用于第 ${(detail.value.lore.chapter_idx ?? loreChapter.value) + 1} 章的记录；重建世界观索引后检索更新`
    )
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

async function resetCurrent() {
  if (!detail.value) return
  const isCharacter = detailType.value === 'character'
  try {
    await ElMessageBox.confirm(
      `确认恢复${isCharacter ? '人物卡' : '世界观条目'}的原始内容？当前人工修正会删除。`,
      '恢复原始',
      {
        type: 'warning',
        confirmButtonText: '恢复原始',
        cancelButtonText: '取消'
      }
    )
  } catch {
    return
  }
  saving.value = true
  try {
    const response = isCharacter
      ? await resetCharacterCardApi(
          bookId.value,
          detail.value.character.id,
          cardChapter.value
        )
      : await resetLoreApi(
          bookId.value,
          detail.value.lore.id,
          loreChapter.value
        )
    detail.value = response.data
    editing.value = false
    await refreshStatus()
    ElMessage.success('已恢复原始内容')
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

function backToBook() {
  router.push(`/books/${bookId.value}`)
}

onMounted(load)
</script>

<style scoped>
.entity-page {
  max-width: 1440px;
}

.entity-top {
  margin-bottom: 16px;
}

.back-button {
  margin-bottom: 10px;
  color: var(--muted);
}

.entity-title-line {
  display: flex;
  align-items: center;
  gap: 12px;
}

.entity-header {
  margin-bottom: 0;
}

.status-panel,
.search-panel {
  padding: 20px 22px;
}

.status-panel {
  margin-bottom: 16px;
}

.status-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.status-head h2 {
  font-size: 17px;
}

.status-head p {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
}

.status-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.status-metrics > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--card-soft);
}

.status-metrics span {
  color: var(--muted);
  font-size: 10px;
}

.status-metrics strong {
  font-size: 18px;
}

.status-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.warning-strip {
  margin-top: 12px;
  padding: 9px 12px;
  border-left: 2px solid var(--gold);
  border-radius: 4px;
  background: var(--gold-soft-bg);
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.danger-strip {
  border-left-color: var(--danger-hi);
  background: var(--surface-hover);
}

.search-panel {
  min-height: 380px;
}

.search-row {
  display: flex;
  gap: 9px;
  margin-bottom: 16px;
}

.search-row :deep(.el-input) {
  flex: 1;
}

.alias-block {
  margin: -4px 0 18px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--card-soft);
}

.alias-row {
  display: flex;
  align-items: center;
  gap: 9px;
}

.alias-row :deep(.el-input) {
  flex: 0 0 220px;
}

.alias-hint {
  color: var(--muted);
  font-size: 12px;
}

.alias-result {
  margin-top: 12px;
}

.alias-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  color: var(--muted);
  font-size: 12px;
}

.alias-reason {
  flex: 1;
}

.alias-count {
  white-space: nowrap;
}

.alias-grid-head,
.alias-grid-row {
  display: grid;
  grid-template-columns: 1.4fr 52px 52px 60px 2fr;
  gap: 8px;
  align-items: center;
  padding: 6px 0;
  font-size: 12px;
}

.alias-grid-head {
  color: var(--muted);
  border-bottom: 1px solid var(--line-soft);
}

.alias-grid-row {
  border-bottom: 1px solid var(--line-soft);
}

.alias-grid-row:last-child {
  border-bottom: 0;
}

.alias-grid-row.dim {
  opacity: 0.45;
}

.alias-name {
  color: var(--text);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.alias-name em {
  margin-left: 6px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--green-soft-bg);
  color: var(--green-soft);
  font-size: 11px;
  font-style: normal;
}

.alias-chapters {
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chapter-pick {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.chapter-pick-label,
.chapter-pick-value {
  font-size: 12px;
  color: var(--muted);
}

.chapter-pick :deep(.el-input-number) {
  width: 108px;
}

.from-chapter-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.from-chapter-hint {
  font-size: 12px;
  color: var(--muted);
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.result-card {
  padding: 13px 15px;
  border: 1px solid var(--line-soft);
  border-radius: 11px;
  background: var(--card-soft);
  transition: border-color 0.2s ease, background 0.2s ease;
}

.result-card.clickable {
  cursor: pointer;
}

.result-card.clickable:hover {
  border-color: var(--gold-dim);
  background: var(--surface-hover);
}

.result-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 7px;
}

.result-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.result-title strong {
  font-size: 15px;
}

.result-meta {
  color: var(--muted);
  font-size: 10px;
  white-space: nowrap;
}

.result-line {
  display: flex;
  gap: 9px;
  margin: 3px 0;
  color: var(--text);
  font-size: 12px;
  line-height: 1.7;
}

.result-line span {
  flex: none;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.04em;
}

.result-line.clamp {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.result-scores {
  margin-top: 6px;
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 9px;
}

.search-hint {
  margin: 28px 0;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}

.drawer-body {
  min-height: 160px;
}

.drawer-meta {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 9px;
  color: var(--muted);
  font-size: 11px;
}

.alias-line {
  margin-bottom: 16px;
  color: var(--muted);
  font-size: 12px;
}

.detail-field {
  margin-bottom: 15px;
}

.detail-field > span {
  display: block;
  margin-bottom: 5px;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.06em;
}

.detail-field p {
  color: var(--text);
  font-size: 13px;
  line-height: 1.9;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.drawer-actions {
  display: flex;
  gap: 9px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}

@media (max-width: 760px) {
  .status-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .search-row {
    flex-wrap: wrap;
  }

  .search-row :deep(.el-input) {
    flex-basis: 100%;
  }

  .result-head {
    flex-direction: column;
    gap: 4px;
  }

  .result-meta {
    white-space: normal;
  }
}
</style>
