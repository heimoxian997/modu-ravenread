<template>
  <div class="page settings-page" v-loading="loading">
    <div class="page-header settings-header">
      <div>
        <div class="eyebrow">设置 · 模型服务</div>
        <h1 class="page-title">模型服务</h1>
        <p class="page-subtitle">
          为后续摘要与伴读选择 OpenAI 兼容服务。预设只是模板，地址、模型和 Key 都可以自由修改。
        </p>
      </div>
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>
        添加服务
      </el-button>
    </div>

    <el-alert
      class="privacy-alert"
      type="info"
      :closable="false"
      show-icon
      title="Key 只加密保存在本机"
      description="接口不返回明文；测试连接只发送一句“只回复 OK”，不会发送任何书稿。"
    />

    <section class="panel current-panel">
      <div class="panel-head">
        <div>
          <h2>当前模型</h2>
          <p>摘要与伴读后续会使用这里选中的服务。</p>
        </div>
        <el-tag v-if="activeProvider" type="success" effect="plain" round>
          当前使用
        </el-tag>
      </div>

      <div v-if="activeProvider" class="current-body">
        <div class="current-main">
          <div class="provider-title">
            <strong>{{ activeProvider.name }}</strong>
            <el-tag class="model-tag" size="small" effect="plain">{{ presetLabel(activeProvider.preset_key) }}</el-tag>
            <el-tag
              size="small"
              :type="activeProvider.is_local ? 'success' : 'warning'"
              effect="plain"
            >
              {{ activeProvider.is_local ? '本地' : '云端' }}
            </el-tag>
          </div>
          <div class="current-meta">
            <span><b>模型</b>{{ activeProvider.model }}</span>
            <span><b>地址</b>{{ activeProvider.base_url }}</span>
            <span>
              <b>Key</b>
              {{ activeProvider.has_key ? activeProvider.masked_key || '****' : '未填写' }}
            </span>
          </div>
          <div v-if="activeProvider.has_key" class="key-ok">Key 已加密保存</div>
          <div v-else class="key-warning">
            {{ activeProvider.is_local ? '本地服务可以没有 Key。' : '尚未填写 Key，云端请求会失败。' }}
          </div>
        </div>
        <div class="current-actions">
          <el-button
            :loading="testingId === activeProvider.id"
            @click="testConnection(activeProvider)"
          >
            测试连接
          </el-button>
          <el-button @click="openEdit(activeProvider)">编辑</el-button>
        </div>
      </div>

      <div v-else class="current-empty">
        <el-empty description="先添加一个模型服务" :image-size="60">
          <el-button type="primary" @click="openCreate">
            添加第一个模型服务
          </el-button>
        </el-empty>
      </div>

      <div
        v-if="activeProvider && testResults[activeProvider.id]"
        class="test-result"
        :class="testResults[activeProvider.id].ok ? 'is-ok' : 'is-fail'"
      >
        <div class="test-result-head">
          <strong>{{ testResults[activeProvider.id].ok ? '连接成功' : '连接失败' }}</strong>
          <span v-if="testResults[activeProvider.id].latency_ms">
            {{ testResults[activeProvider.id].latency_ms }} ms
          </span>
        </div>
        <p>
          {{
            testResults[activeProvider.id].ok
              ? `回复：${testResults[activeProvider.id].reply || '—'}`
              : testResults[activeProvider.id].error || '未知错误'
          }}
        </p>
      </div>
    </section>

    <section v-if="providers.length" class="providers-section">
      <div class="section-head">
        <div>
          <h2>服务列表</h2>
          <p>同一时间只启用一个当前模型；删除当前服务会回到未配置状态。</p>
        </div>
        <span class="count">{{ providers.length }} 个服务</span>
      </div>

      <div class="provider-grid">
        <article
          v-for="provider in providers"
          :key="provider.id"
          class="provider-card"
          :class="{ 'is-active': provider.id === activeId }"
        >
          <div class="provider-card-head">
            <div class="provider-title">
              <strong>{{ provider.name }}</strong>
              <el-tag v-if="provider.id === activeId" type="success" size="small" effect="plain">
                当前
              </el-tag>
            </div>
            <el-tag class="model-tag" size="small" effect="plain">{{ presetLabel(provider.preset_key) }}</el-tag>
          </div>

          <div class="provider-tags">
            <el-tag
              size="small"
              :type="provider.is_local ? 'success' : 'warning'"
              effect="plain"
            >
              {{ provider.is_local ? '本地服务' : '云端服务' }}
            </el-tag>
            <el-tag v-if="!provider.has_key" size="small" type="warning" effect="plain">
              未填 Key
            </el-tag>
            <span v-else class="masked-key">Key {{ provider.masked_key || '****' }}</span>
            <el-tag v-if="provider.disable_thinking" size="small" type="info" effect="plain">
              关思考
            </el-tag>
          </div>

          <dl class="provider-fields">
            <div>
              <dt>模型</dt>
              <dd>{{ provider.model }}</dd>
            </div>
            <div>
              <dt>Base URL</dt>
              <dd class="mono">{{ provider.base_url }}</dd>
            </div>
          </dl>

          <div class="provider-actions">
            <el-button size="small" @click="openEdit(provider)">编辑</el-button>
            <el-button
              size="small"
              :loading="testingId === provider.id"
              @click="testConnection(provider)"
            >
              测试
            </el-button>
            <el-button
              v-if="provider.id !== activeId"
              size="small"
              type="primary"
              plain
              :loading="activatingId === provider.id"
              @click="activate(provider)"
            >
              设为当前
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="deletingId === provider.id"
              @click="removeProvider(provider)"
            >
              删除
            </el-button>
          </div>

          <div
            v-if="testResults[provider.id]"
            class="test-result"
            :class="testResults[provider.id].ok ? 'is-ok' : 'is-fail'"
          >
            <div class="test-result-head">
              <strong>{{ testResults[provider.id].ok ? '连接成功' : '连接失败' }}</strong>
              <span v-if="testResults[provider.id].latency_ms">
                {{ testResults[provider.id].latency_ms }} ms
              </span>
            </div>
            <p>
              {{
                testResults[provider.id].ok
                  ? `回复：${testResults[provider.id].reply || '—'}`
                  : testResults[provider.id].error || '未知错误'
              }}
            </p>
          </div>
        </article>
      </div>
    </section>

    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="min(640px, 92vw)"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form label-position="top" class="provider-form">
        <el-form-item label="预设模板">
          <el-select v-model="form.preset_key" style="width: 100%" @change="applyPreset">
            <el-option
              v-for="preset in presets"
              :key="preset.preset_key"
              :label="preset.name"
              :value="preset.preset_key"
            />
          </el-select>
          <div class="field-hint">预设只填充默认值，下面的字段都可以修改。</div>
        </el-form-item>

        <div class="form-grid">
          <el-form-item label="名称">
            <el-input v-model="form.name" placeholder="留空使用预设名称" maxlength="100" />
          </el-form-item>
          <el-form-item label="模型名">
            <el-input v-model="form.model" :placeholder="modelPlaceholder" maxlength="200" />
          </el-form-item>
        </div>

        <el-form-item label="Base URL">
          <el-input
            v-model="form.base_url"
            placeholder="https://api.example.com/v1"
            maxlength="500"
          />
          <div class="field-hint">
            通常以 /v1 结尾，也可粘贴完整 /chat/completions 地址。
          </div>
        </el-form-item>

        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            autocomplete="new-password"
            :disabled="clearKey"
            :placeholder="keyPlaceholder"
            maxlength="4096"
          />
          <div class="field-hint">
            Key 只加密保存在本机，接口不会返回明文。Ollama / 内网服务可以留空。
          </div>
        </el-form-item>

        <el-checkbox v-if="editingProvider?.has_key" v-model="clearKey">
          清除已保存的 Key
        </el-checkbox>

        <el-form-item label="思考模式">
          <el-switch
            v-model="form.disable_thinking"
            active-text="关闭思考"
            inactive-text="跟随模型默认"
          />
          <div class="field-hint">
            Qwen3 这类思考模型打开后会附加 enable_thinking=false：单章耗时和 token
            都降一个量级，摘要会略长一点。非思考模型忽略此项。
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveProvider">
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createProviderApi,
  deleteProviderApi,
  getLlmSettingsApi,
  setActiveProviderApi,
  testProviderApi,
  updateProviderApi
} from '@/api/llmSettings'
import { refreshSettingsStatus } from '@/stores/settings'

const loading = ref(false)
const saving = ref(false)
const testingId = ref(null)
const activatingId = ref(null)
const deletingId = ref(null)
const providers = ref([])
const presets = ref([])
const activeId = ref(null)
const dialogVisible = ref(false)
const editingId = ref(null)
const clearKey = ref(false)
const testResults = ref({})

const form = reactive({
  name: '',
  preset_key: 'deepseek',
  base_url: '',
  model: '',
  api_key: '',
  disable_thinking: false
})

const activeProvider = computed(() =>
  providers.value.find((provider) => provider.id === activeId.value)
)
const editingProvider = computed(() =>
  providers.value.find((provider) => provider.id === editingId.value)
)
const dialogTitle = computed(() =>
  editingId.value ? '编辑模型服务' : '添加模型服务'
)
const currentPreset = computed(
  () => presets.value.find((preset) => preset.preset_key === form.preset_key) || {}
)
const modelPlaceholder = computed(
  () => `如 ${currentPreset.value.model || 'deepseek-chat、qwen-plus'}`
)
const keyPlaceholder = computed(() => {
  if (clearKey.value) return '保存后会清除已保存的 Key'
  if (editingId.value && editingProvider.value?.has_key) {
    return `已保存（${editingProvider.value.masked_key || '****'}），留空不修改`
  }
  return '可留空；Ollama / 本地服务通常不需要'
})

function presetLabel(key) {
  return presets.value.find((preset) => preset.preset_key === key)?.name || key || '自定义'
}

function fillPreset(key) {
  const preset = presets.value.find((item) => item.preset_key === key)
  if (!preset) return
  form.preset_key = preset.preset_key
  form.name = preset.name
  form.base_url = preset.base_url || ''
  form.model = preset.model || ''
}

function applyPreset(key) {
  clearKey.value = false
  fillPreset(key)
}

async function load() {
  loading.value = true
  try {
    const response = await getLlmSettingsApi()
    providers.value = response.data.providers || []
    presets.value = response.data.presets || []
    activeId.value = response.data.active_id
    await refreshSettingsStatus()
  } catch {
    providers.value = []
    presets.value = []
    activeId.value = null
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  clearKey.value = false
  Object.assign(form, {
    name: '',
    preset_key: 'deepseek',
    base_url: '',
    model: '',
    api_key: '',
    disable_thinking: false
  })
  fillPreset('deepseek')
  dialogVisible.value = true
}

function openEdit(provider) {
  editingId.value = provider.id
  clearKey.value = false
  Object.assign(form, {
    name: provider.name,
    preset_key: provider.preset_key,
    base_url: provider.base_url,
    model: provider.model,
    api_key: '',
    disable_thinking: !!provider.disable_thinking
  })
  dialogVisible.value = true
}

async function saveProvider() {
  const name = form.name.trim()
  const baseUrl = form.base_url.trim()
  const model = form.model.trim()
  if (!baseUrl || !model) {
    ElMessage.warning('请填写 Base URL 和模型名')
    return
  }
  if (editingId.value && !name) {
    ElMessage.warning('请填写服务名称')
    return
  }

  const payload = {
    name: name || null,
    preset_key: form.preset_key,
    base_url: baseUrl,
    model,
    disable_thinking: form.disable_thinking
  }
  if (form.api_key.trim() && !clearKey.value) {
    payload.api_key = form.api_key.trim()
  }
  if (editingId.value && clearKey.value) {
    payload.clear_key = true
  }

  saving.value = true
  try {
    if (editingId.value) {
      await updateProviderApi(editingId.value, payload)
    } else {
      await createProviderApi(payload)
    }
    dialogVisible.value = false
    await load()
    ElMessage.success(editingId.value ? '模型服务已更新' : '模型服务已添加')
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

async function testConnection(provider) {
  if (testingId.value) return
  testingId.value = provider.id
  try {
    const response = await testProviderApi(provider.id)
    testResults.value = {
      ...testResults.value,
      [provider.id]: response.data
    }
  } catch (error) {
    testResults.value = {
      ...testResults.value,
      [provider.id]: {
        ok: false,
        latency_ms: 0,
        reply: null,
        model: provider.model,
        error: error.message || '测试连接失败'
      }
    }
  } finally {
    testingId.value = null
  }
}

async function activate(provider) {
  if (activatingId.value) return
  activatingId.value = provider.id
  try {
    await setActiveProviderApi(provider.id)
    await load()
    ElMessage.success(`已切换到 ${provider.name}`)
  } catch {
    // 请求层已提示
  } finally {
    activatingId.value = null
  }
}

async function removeProvider(provider) {
  try {
    await ElMessageBox.confirm(
      `删除“${provider.name}”不会删除书稿或索引，但会移除它的地址和 Key。`,
      '删除模型服务',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }
  deletingId.value = provider.id
  try {
    await deleteProviderApi(provider.id)
    const next = { ...testResults.value }
    delete next[provider.id]
    testResults.value = next
    await load()
    ElMessage.success('模型服务已删除')
  } catch {
    // 请求层已提示
  } finally {
    deletingId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.settings-page {
  max-width: 1180px;
}

.settings-header {
  align-items: center;
  margin-bottom: 16px;
}

.eyebrow {
  margin-bottom: 7px;
  color: var(--amber);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
}

.privacy-alert {
  margin-bottom: 16px;
}

.current-panel {
  padding: 20px 22px;
  margin-bottom: 22px;
}

.panel-head,
.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.panel-head {
  margin-bottom: 14px;
}

.panel-head h2,
.section-head h2 {
  font-size: 17px;
}

.panel-head p,
.section-head p {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.7;
}

.current-body {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 15px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--card-soft);
}

.current-main {
  min-width: 0;
}

.provider-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.provider-title strong {
  font-size: 16px;
}

.current-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 22px;
  margin-top: 10px;
  color: var(--muted);
  font-size: 11px;
}

.current-meta b {
  margin-right: 6px;
  color: var(--muted);
  font-weight: 400;
}

.current-meta span {
  overflow-wrap: anywhere;
}

.key-ok,
.key-warning {
  margin-top: 8px;
  font-size: 11px;
}

.key-ok {
  color: var(--green);
}

.key-warning {
  color: var(--gold);
}

.current-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  flex: none;
}

.current-empty {
  padding: 4px 0 2px;
}

.providers-section {
  margin-top: 4px;
}

.section-head {
  align-items: center;
  margin-bottom: 12px;
}

.count {
  color: var(--muted);
  font-size: 11px;
}

.provider-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.provider-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 17px 17px 15px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--card);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.32);
}

.provider-card.is-active {
  border-color: var(--gold);
  box-shadow: 0 0 0 2px var(--gold-soft-bg);
}

.provider-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.provider-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  margin-top: 10px;
}

.masked-key {
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
}

.provider-fields {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 13px 0 0;
}

.provider-fields > div {
  min-width: 0;
}

.provider-fields dt {
  margin-bottom: 3px;
  color: var(--muted);
  font-size: 10px;
}

.provider-fields dd {
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.provider-fields dd.mono {
  color: var(--muted);
  font-size: 10px;
}

.provider-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: auto;
  padding-top: 14px;
}

.test-result {
  margin-top: 10px;
  padding: 9px 11px;
  border-radius: 8px;
  font-size: 11px;
  line-height: 1.7;
}

.test-result.is-ok {
  border: 1px solid rgba(95, 141, 119, 0.45);
  background: var(--green-soft-bg);
  color: #74a68b;
}

.test-result.is-fail {
  border: 1px solid rgba(176, 85, 96, 0.45);
  background: rgba(122, 37, 48, 0.14);
  color: var(--danger-hi);
}

.test-result-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.test-result p {
  margin-top: 4px;
  overflow-wrap: anywhere;
}

.empty-panel {
  padding: 28px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.field-hint {
  margin-top: 5px;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.6;
}

.provider-form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.provider-form :deep(.el-form-item__label) {
  padding-bottom: 4px;
  color: var(--muted);
  font-size: 12px;
}

@media (max-width: 900px) {
  .current-body {
    flex-direction: column;
  }

  .provider-grid {
    grid-template-columns: 1fr;
  }

  .settings-header {
    align-items: flex-start;
  }

  .form-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}

@media (max-width: 600px) {
  .current-panel {
    padding: 16px;
  }

  .current-actions {
    width: 100%;
  }
}
</style>
