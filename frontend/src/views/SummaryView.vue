<template>
  <div class="page summary-page" v-loading="loading">
    <div class="detail-top">
      <el-button text class="back-button" @click="goDetail">
        <el-icon><ArrowLeft /></el-icon>
        返回书籍详情
      </el-button>
      <div class="page-header summary-header">
        <div>
          <div class="eyebrow">章节摘要 · 批量与编辑</div>
          <h1 class="page-title">章节摘要 · 批量与编辑</h1>
          <p class="page-subtitle">
            摘要可批量生成、编辑，并建立本地索引用于检索；不调用 LLM 做问答。
          </p>
        </div>
        <div class="header-actions">
          <el-tag v-if="activeProvider" class="model-tag" type="success" effect="plain" round>
            {{ activeProvider.name }} · {{ activeProvider.model }}
          </el-tag>
          <el-button @click="goSettings">
            <el-icon><Setting /></el-icon>
            模型设置
          </el-button>
        </div>
      </div>
    </div>

    <el-alert
      v-if="!activeProvider"
      class="section-alert"
      type="warning"
      :closable="false"
      show-icon
      title="还没有启用模型服务"
      description="先到设置页添加并启用一个模型，才能生成章节摘要。"
    >
      <template #default>
        <el-button size="small" type="warning" plain @click="goSettings">
          去设置
        </el-button>
      </template>
    </el-alert>
    <el-alert
      v-else-if="!canExtract"
      class="section-alert"
      type="warning"
      :closable="false"
      show-icon
      title="当前模型缺少 API Key"
      description="云端模型必须有 Key；请到设置页填写后再试。"
    />

    <section class="panel batch-panel">
      <div class="panel-head batch-head">
        <div>
          <h2>批量生成</h2>
          <p>逐章串行；停止会等当前章完成，已提交章节不会丢失。关闭页面任务继续。</p>
        </div>
        <el-tag v-if="batchJob" :type="batchTag.type" effect="plain" round>
          {{ batchTag.label }}
        </el-tag>
      </div>

      <div v-if="!batchRunning" class="batch-form">
        <el-radio-group v-model="batchMode" class="batch-modes">
          <el-radio-button value="continue">继续生成</el-radio-button>
          <el-radio-button value="limit">处理 N 章</el-radio-button>
          <el-radio-button value="stop_at">停在第 N 章</el-radio-button>
        </el-radio-group>
        <el-input-number
          v-if="batchMode === 'limit'"
          v-model="batchLimit"
          :min="1"
          :max="1000"
          size="default"
          class="batch-number"
        />
        <el-input-number
          v-if="batchMode === 'stop_at'"
          v-model="batchStopAt"
          :min="1"
          :max="9999"
          size="default"
          class="batch-number"
        />
        <el-checkbox v-model="batchForce" :disabled="!canExtract">
          强制重跑
        </el-checkbox>
        <el-checkbox v-model="batchRetryFailed" :disabled="!canExtract">
          补失败章
        </el-checkbox>
        <el-select
          v-model="batchCheckProviderId"
          class="batch-check"
          size="default"
          :disabled="!canExtract"
          title="默认只跑规则层；选择模型才会调用 LLM 复核（只标注，不删除）"
        >
          <el-option :value="null" label="称谓复核：关（仅规则层）" />
          <el-option
            v-for="provider in providers"
            :key="provider.id"
            :value="provider.id"
            :label="`称谓复核：${provider.name}${provider.disable_thinking ? ' · 关思考' : ''}`"
          />
        </el-select>
        <el-button
          type="primary"
          :loading="batchStarting"
          :disabled="!canStartBatch"
          @click="startBatch"
        >
          开始生成
        </el-button>
      </div>

      <div v-else class="batch-progress">
        <div class="batch-progress-head">
          <span>{{ batchJob.message || '正在生成…' }}</span>
          <strong>{{ batchJob.progress }}%</strong>
        </div>
        <el-progress
          :percentage="batchJob.progress"
          :show-text="false"
          :stroke-width="9"
        />
        <div class="batch-meta">
          <span>已生成 {{ batchJob.generated }} / {{ batchJob.planned }}</span>
          <span>失败 {{ batchJob.failed }}</span>
          <span v-if="batchJob.next_chapter_number">
            下一章：第 {{ batchJob.next_chapter_number }} 章
          </span>
          <span v-if="batchJob.current_chapter_idx !== null">
            当前：第 {{ batchJob.current_chapter_idx + 1 }} 章
          </span>
        </div>
        <div class="batch-stop-row">
          <el-button
            v-if="batchJob.status !== 'stopping'"
            type="warning"
            plain
            :loading="batchStopLoading"
            :disabled="indexBuilding"
            @click="stopBatch"
          >
            停止
          </el-button>
          <span v-else>正在停止，当前章完成后停止</span>
        </div>
      </div>

      <el-alert
        v-if="batchJob?.status === 'interrupted'"
        class="batch-alert"
        type="warning"
        :closable="false"
        show-icon
        title="上次任务被中断"
        description="服务重启导致任务中断；再次开始会从断点继续。"
      />
      <el-alert
        v-if="batchJob?.status === 'failed'"
        class="batch-alert"
        type="error"
        :closable="false"
        show-icon
        title="上次批量任务失败"
        :description="batchJob.error || '请检查模型服务后重试。'"
      />
      <div v-if="batchJob?.status === 'done'" class="batch-history">
        上次批量完成：成功 {{ batchJob.generated }}，失败 {{ batchJob.failed }}。
      </div>
      <div v-if="batchJob?.status === 'stopped'" class="batch-history">
        上次已停止；下次从第
        {{ overview.next_chapter_number || batchJob.next_chapter_number || '—' }}
        章继续。
      </div>
    </section>

    <section class="panel summary-index-panel" v-loading="indexStatusLoading">
      <div class="panel-head index-head">
        <div>
          <h2>摘要索引</h2>
          <p>用本机 bge-m3 为摘要建立 dense + sparse 向量；不调用 LLM。</p>
        </div>
        <el-tag :type="indexTag.type" effect="plain" round>
          {{ indexTag.label }}
        </el-tag>
      </div>

      <div class="index-body">
        <div class="index-metrics">
          <div>
            <span>已索引</span>
            <strong>{{ indexStatus.indexed_count }}</strong>
          </div>
          <div>
            <span>摘要数</span>
            <strong>{{ indexStatus.summary_count }}</strong>
          </div>
          <div>
            <span>正文数</span>
            <strong>{{ indexStatus.total_main }}</strong>
          </div>
          <div>
            <span>维度</span>
            <strong>{{ indexStatus.dim }}</strong>
          </div>
        </div>

        <div class="index-actions">
          <div class="index-action-row">
            <el-button
              type="primary"
              :loading="indexBuilding"
              :disabled="!canBuildIndex"
              @click="buildIndex"
            >
              <el-icon><Refresh /></el-icon>
              重建摘要索引
            </el-button>
            <span class="index-note">
              {{ indexStatus.collection || 'novel_summary' }} · 首次可能加载模型 10–15 秒
            </span>
          </div>

          <div v-if="isPartial" class="index-partial">
            <span>摘要未完整（{{ indexStatus.summary_count }}/{{ indexStatus.total_main }}）</span>
            <el-checkbox
              v-model="allowPartial"
              :disabled="indexBuilding || !indexStatus.available"
            >
              仅试跑，允许部分索引
            </el-checkbox>
          </div>
          <div v-if="indexStatus.summary_count === 0" class="index-hint">
            还没有可索引的摘要，先生成章节摘要或完成一章跟读。
          </div>
          <div v-if="dirty" class="index-hint">有未保存修改，保存后再重建索引。</div>
          <div v-if="indexStatus.needs_rebuild" class="index-warning">
            摘要已更新，建议重建索引。
          </div>
          <el-alert
            v-if="!indexStatus.available"
            class="index-alert"
            type="error"
            :closable="false"
            show-icon
            title="摘要索引不可用"
            :description="indexStatus.error || '本地 Qdrant 无法访问。'"
          />
        </div>
      </div>
    </section>

    <section class="panel summary-search-panel">
      <div class="panel-head search-head">
        <div>
          <h2>摘要检索</h2>
          <p>在本机摘要向量中检索；结果按章节回表，不调用 LLM 做问答。</p>
        </div>
        <span
          v-if="isPartial && indexStatus.indexed_count > 0"
          class="search-partial"
        >
          部分索引：只覆盖 {{ indexStatus.indexed_count }}/{{ indexStatus.total_main }} 章
        </span>
      </div>

      <div class="summary-search-bar">
        <el-input
          v-model="searchQuery"
          clearable
          placeholder="例如：林晚是谁？她调查了什么案件？"
          :disabled="searchDisabled"
          @keyup.enter="runSummarySearch"
        />
        <el-select
          v-model="searchTopK"
          class="search-topk"
          :disabled="searchDisabled"
        >
          <el-option :value="5" label="Top 5" />
          <el-option :value="10" label="Top 10" />
          <el-option :value="20" label="Top 20" />
        </el-select>
        <el-checkbox
          v-model="searchRerank"
          class="rerank-toggle"
          title="打开后并排显示 RRF 与 bge-reranker 两套排序；首次检索会加载本地模型，约 20 秒"
          :disabled="searchDisabled"
        >
          重排对比
        </el-checkbox>
        <el-button
          type="primary"
          :loading="searching"
          :disabled="!canSearch"
          @click="runSummarySearch"
        >
          检索
        </el-button>
      </div>

      <div v-if="!indexStatus.available" class="search-hint danger-text">
        摘要索引不可用，暂不能检索。
      </div>
      <div v-else-if="indexStatus.indexed_count === 0" class="search-hint">
        先重建摘要索引，再检索。
      </div>
      <div v-else-if="indexStatus.needs_rebuild" class="search-hint warning-text">
        索引可能已过期，建议重建后再检索。
      </div>

      <div v-if="searchCompare" class="rerank-meta">
        <span v-if="searchCompare.rerank_applied">
          RRF 召回 {{ searchCompare.recall }} 条 → 重排，耗时 {{ searchCompare.rerank_latency_ms }}ms
        </span>
        <span v-else class="warning-text">
          重排未生效（{{ searchCompare.rerank_error || '模型不可用' }}），两栏一致
        </span>
      </div>

      <div
        v-if="searchColumns.length"
        class="search-columns"
        :class="{ split: searchColumns.length > 1 }"
      >
        <div
          v-for="column in searchColumns"
          :key="column.key"
          class="search-column"
        >
          <div v-if="searchColumns.length > 1" class="search-column-head">
            <strong>{{ column.title }}</strong>
            <small>{{ column.note }}</small>
          </div>
          <div class="search-results">
            <article
              v-for="(item, rank) in column.items"
              :key="`${column.key}-${item.summary_id}`"
              class="search-result-card"
              @click="selectChapterByIndex(item.chapter_idx)"
            >
              <div class="search-result-head">
                <span class="search-rank">{{ rank + 1 }}</span>
                <div class="search-result-main">
                  <strong>第{{ item.chapter_idx + 1 }}章 {{ item.chapter_title }}</strong>
                  <span class="search-scores">
                    <template v-if="column.key === 'rerank'">
                      重排 {{ scoreText(item.rerank_score) }}
                      <em
                        v-if="item.rank_delta"
                        class="rank-delta"
                        :class="item.rank_delta > 0 ? 'up' : 'down'"
                      >
                        {{ item.rank_delta > 0 ? '↑' : '↓' }}{{ Math.abs(item.rank_delta) }}
                      </em>
                      · 原第 {{ item.rrf_rank }} 名
                    </template>
                    <template v-else>
                      RRF {{ scoreText(item.rrf_score) }} · dense {{ scoreText(item.dense_score) }} · sparse {{ scoreText(item.sparse_score) }}
                    </template>
                  </span>
                </div>
              </div>
              <p class="search-summary">{{ summaryPreview(item.summary) }}</p>
              <el-collapse class="search-extra" @click.stop>
                <el-collapse-item title="关键事件 / 人物 / 章末钩子">
                  <div class="search-extra-body">
                    <div>
                      <span>关键事件</span>
                      <ul v-if="item.key_events?.length">
                        <li v-for="event in item.key_events" :key="event">{{ event }}</li>
                      </ul>
                      <p v-else>—</p>
                    </div>
                    <div>
                      <span>人物</span>
                      <p>{{ (item.characters || []).join('、') || '—' }}</p>
                    </div>
                    <div>
                      <span>章末钩子</span>
                      <p>{{ item.cliffhanger || '—' }}</p>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </article>
          </div>
        </div>
      </div>
      <el-empty
        v-else-if="searchCompleted"
        description="没有命中摘要"
        :image-size="70"
      />
    </section>

    <div class="summary-stats">
      <div class="summary-stat">
        <span>正文章节</span>
        <strong>{{ overview.total }}</strong>
        <small>只处理 main</small>
      </div>
      <div class="summary-stat">
        <span>已生成</span>
        <strong class="green">{{ overview.done }}</strong>
        <small>可编辑</small>
      </div>
      <div class="summary-stat">
        <span>失败</span>
        <strong class="danger">{{ overview.failed }}</strong>
        <small>保留旧摘要</small>
      </div>
      <div class="summary-stat">
        <span>待处理</span>
        <strong>{{ overview.pending }}</strong>
        <small>手动单章生成</small>
      </div>
    </div>

    <div class="summary-layout">
      <section class="panel list-panel">
        <div class="panel-head">
          <div>
            <h2>章节目录</h2>
            <p>点击章节查看、生成或编辑。</p>
          </div>
          <el-select v-model="statusFilter" size="small" style="width: 104px" @change="changeFilter">
            <el-option value="all" label="全部" />
            <el-option value="done" label="已生成" />
            <el-option value="pending" label="待处理" />
            <el-option value="failed" label="失败" />
          </el-select>
        </div>

        <div class="chapter-list">
          <button
            v-for="item in items"
            :key="item.chapter_idx"
            type="button"
            class="chapter-row"
            :class="{ active: item.chapter_idx === selectedIdx }"
            @click="selectChapter(item)"
          >
            <div class="chapter-row-main">
              <div class="chapter-row-title">
                <strong>{{ item.title }}</strong>
                <el-tag size="small" :type="statusTag(item.status).type" effect="plain">
                  {{ statusTag(item.status).label }}
                </el-tag>
                <el-tag v-if="item.follow_complete !== undefined" size="small" type="info" effect="plain">{{ item.follow_complete ? '跟读概览' : '部分跟读概览' }}</el-tag>
                <el-tag v-if="item.edited" size="small" type="warning" effect="plain">
                  已编辑
                </el-tag>
              </div>
              <p>{{ item.summary_preview || (item.status === 'failed' ? item.issue : '尚未生成') }}</p>
            </div>
            <span class="chapter-no">第 {{ item.chapter_idx + 1 }} 章</span>
          </button>
          <el-empty
            v-if="!items.length && !listLoading"
            description="没有符合条件的章节"
            :image-size="70"
          />
        </div>

        <div class="list-foot">
          <span>{{ listTotal }} 章</span>
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="listTotal"
            layout="prev, pager, next"
            small
            @current-change="loadList"
          />
        </div>
      </section>

      <section class="panel detail-panel" v-loading="detailLoading">
        <div v-if="!detail" class="detail-empty">
          <el-empty description="选择左侧章节开始" :image-size="90" />
        </div>

        <template v-else>
          <div class="detail-head">
            <div>
              <div class="eyebrow">第 {{ detail.chapter_idx + 1 }} 章</div>
              <h2>{{ detail.title }}</h2>
            </div>
            <div class="detail-head-actions">
              <el-tag :type="statusTag(detail.status).type" effect="plain" round>
                {{ statusTag(detail.status).label }}
              </el-tag>
              <el-button text @click="closeDetail">关闭</el-button>
            </div>
          </div>

          <div v-if="detailMeta" class="detail-meta">
            <span v-if="detailMeta.provider_name">
              {{ detailMeta.provider_name }} / {{ detailMeta.model }}
            </span>
            <span v-if="detailMeta.elapsed_ms">{{ detailMeta.elapsed_ms }} ms</span>
            <el-tag v-if="detailMeta.text_truncated" size="small" type="warning" effect="plain">
              正文截断
            </el-tag>
            <el-tag v-if="detailMeta.output_trimmed" size="small" type="warning" effect="plain">
              输出裁剪
            </el-tag>
            <el-tag v-if="detail.summary?.summary_truncated" size="small" type="warning" effect="plain">
              摘要截断
            </el-tag>
            <el-tag v-if="detail.summary?.edited" size="small" type="warning" effect="plain">
              已人工编辑
            </el-tag>
          </div>

          <el-alert
            v-if="detail.issue"
            class="section-alert"
            type="error"
            :closable="false"
            show-icon
            title="上次生成失败"
            :description="detail.issue.error"
          />

          <div v-if="detail.follow_overview" class="generate-row">
            <span class="muted">{{ followView ? (detail.follow_overview.complete ? '来源：完整跟读' : `来源：部分跟读 · ${detail.follow_overview.covered}/${detail.follow_overview.total} 字符`) : '来源：章节摘要' }}{{ followView ? ' · 未提取字段留空，当前为只读展示' : '' }}</span>
            <el-button v-if="detail.summary" text :disabled="dirty" @click="showFollow = !showFollow">{{ followView ? '返回章节摘要' : '查看跟读版本' }}</el-button>
          </div>
          <div v-if="detail.status === 'pending' && !detail.follow_overview" class="generate-empty">
            <el-empty :description="detail.follow_overview ? '尚未生成结构化摘要，可按需补充' : '这一章还没有摘要'" :image-size="72">
              <el-button
                type="primary"
                :loading="extracting"
                :disabled="!canExtract || batchRunning || indexBuilding"
                @click="generate"
              >
                用当前模型生成
              </el-button>
            </el-empty>
            <p v-if="!canExtract" class="disabled-copy">请先在设置页启用可用模型。</p>
          </div>

          <template v-else>
            <div class="generate-row">
              <el-button
                :loading="extracting"
                :disabled="!canExtract || batchRunning || indexBuilding"
                @click="generate"
              >
                <el-icon><Refresh /></el-icon>
                {{ !detail.summary ? '生成结构化摘要' : detail.status === 'failed' ? '重试生成' : '重新生成' }}
              </el-button>
              <span class="muted">
                {{ detail.summary ? '覆盖本章已有摘要；失败时保留旧摘要。' : '按需生成更多结构化字段，跟读成果保留。' }}
              </span>
            </div>

            <div v-if="detail.status === 'failed'" class="raw-panel">
              <h3>失败信息</h3>
              <p>{{ detail.issue?.error || '模型没有返回可用结果。' }}</p>
              <template v-if="detail.extraction?.raw_content">
                <h3>模型原始返回</h3>
                <pre>{{ detail.extraction.raw_content }}</pre>
              </template>
            </div>

            <template v-if="detail.summary || detail.follow_overview">
              <el-form label-position="top" class="summary-form">
                <el-form-item label="摘要">
                  <el-input
                    v-model="form.summary"
                    type="textarea"
                    :rows="5"
                    :maxlength="followView ? undefined : 400"
                    :show-word-limit="!followView"
                    :disabled="batchRunning" :readonly="followView"
                    @input="markDirty"
                  />
                </el-form-item>
                <div class="form-grid">
                  <el-form-item label="关键事件（一行一条）">
                    <el-input
                      v-model="form.key_events"
                      type="textarea"
                      :rows="4"
                      :disabled="batchRunning" :readonly="followView"
                      @input="markDirty"
                    />
                  </el-form-item>
                  <el-form-item label="出场人物（逗号分隔）">
                    <el-input
                      v-model="form.characters"
                      type="textarea"
                      :rows="4"
                      :disabled="batchRunning" :readonly="followView"
                      @input="markDirty"
                    />
                  </el-form-item>
                </div>
                <el-form-item label="章末钩子">
                  <el-input
                    v-model="form.cliffhanger"
                    maxlength="80"
                    :show-word-limit="!followView"
                    :disabled="batchRunning" :readonly="followView"
                    @input="markDirty"
                  />
                </el-form-item>

                <div v-if="!followView" class="save-row">
                  <el-button
                    type="primary"
                    :loading="saving"
                    :disabled="batchRunning || indexBuilding"
                    @click="saveSummary"
                  >
                    保存修改
                  </el-button>
                  <span v-if="dirty" class="dirty-copy">有未保存修改</span>
                </div>
              </el-form>

              <div v-if="detail.extraction?.parsed" class="readonly-block">
                <div class="readonly-head">
                  <h3>原始人物结构</h3>
                  <div class="parsed-tools">
                    <el-tag size="small" type="info" effect="plain">可修改</el-tag>
                    <el-button size="small" text @click="resetParsed">
                      重置
                    </el-button>
                    <el-button
                      size="small"
                      type="primary"
                      plain
                      :loading="parsedSaving"
                      :disabled="batchRunning || indexBuilding"
                      @click="saveParsed"
                    >
                      保存结构
                    </el-button>
                  </div>
                </div>
                <el-input
                  v-model="parsedText.characters"
                  type="textarea"
                  :rows="10"
                  spellcheck="false"
                  class="parsed-input"
                  :class="{ invalid: parsedError.characters }"
                  :disabled="batchRunning || indexBuilding"
                />
                <p v-if="parsedError.characters" class="parsed-error">
                  {{ parsedError.characters }}
                </p>

                <div class="readonly-head">
                  <h3>原始世界观条目</h3>
                  <span class="parsed-hint">保存后需重建实体层才生效</span>
                </div>
                <el-input
                  v-model="parsedText.lore"
                  type="textarea"
                  :rows="10"
                  spellcheck="false"
                  class="parsed-input"
                  :class="{ invalid: parsedError.lore }"
                  :disabled="batchRunning || indexBuilding"
                />
                <p v-if="parsedError.lore" class="parsed-error">
                  {{ parsedError.lore }}
                </p>

                <div
                  v-if="
                    detail.extraction?.alias_check &&
                    (detail.extraction.alias_check.dropped ||
                      detail.extraction.alias_check.model ||
                      detail.extraction.alias_check.flagged)
                  "
                  class="alias-check-block"
                >
                  <div class="readonly-head">
                    <h3>称谓复核</h3>
                    <span
                      class="parsed-hint"
                      :title="detail.extraction.alias_check.model || '未调用模型'"
                    >
                      <template v-if="detail.extraction.alias_check.model">
                        模型复核 · 送检 {{ detail.extraction.alias_check.checked ?? 0 }} 条 ·
                        删除 {{ detail.extraction.alias_check.dropped ?? 0 }} 条 ·
                        待核对 {{ detail.extraction.alias_check.flagged ?? 0 }} 条
                      </template>
                      <template v-else>
                        仅规则层 · 删除 {{ detail.extraction.alias_check.dropped ?? 0 }} 条
                      </template>
                    </span>
                  </div>
                  <p
                    v-if="detail.extraction.alias_check.error"
                    class="alias-check-error"
                  >
                    {{ detail.extraction.alias_check.error }}
                  </p>
                  <el-table
                    v-if="aliasIssues.length"
                    :data="aliasIssues"
                    size="small"
                    class="alias-check-table"
                  >
                    <el-table-column prop="alias" label="称谓" width="110" />
                    <el-table-column prop="owner" label="归属" width="130" />
                    <el-table-column prop="from" label="来源" width="90" />
                    <el-table-column label="处理" width="90">
                      <template #default="{ row }">
                        <el-tag
                          size="small"
                          effect="plain"
                          :type="row.action === 'dropped' ? 'danger' : 'warning'"
                        >
                          {{ row.action === 'dropped' ? '已删除' : '待核对' }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column prop="reason" label="原因" min-width="160" />
                    <el-table-column label="依据" min-width="180">
                      <template #default="{ row }">
                        <span v-if="row.refers_to">指向 {{ row.refers_to }} </span>
                        <span v-if="row.quote" class="alias-quote">「{{ row.quote }}」</span>
                        <span v-if="!row.refers_to && !row.quote">—</span>
                      </template>
                    </el-table-column>
                  </el-table>
                  <p v-else class="parsed-hint">本章没有需要处理的称谓。</p>
                </div>
              </div>
            </template>
          </template>
        </template>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getLlmSettingsApi } from '@/api/llmSettings'
import {
  buildSummaryIndexApi,
  extractChapterApi,
  getLatestBatchJobApi,
  getSummaryDetailApi,
  getSummaryIndexStatusApi,
  getSummaryOverviewApi,
  listSummariesApi,
  searchSummariesApi,
  startBatchJobApi,
  stopBatchJobApi,
  updateExtractionApi,
  updateSummaryApi
} from '@/api/summary'

const route = useRoute()
const router = useRouter()
const bookId = computed(() => Number(route.params.id))

const loading = ref(false)
const listLoading = ref(false)
const detailLoading = ref(false)
const extracting = ref(false)
const saving = ref(false)
const providers = ref([])
const activeId = ref(null)
const overview = ref({ total: 0, done: 0, failed: 0, pending: 0 })
const items = ref([])
const listTotal = ref(0)
const page = ref(1)
const pageSize = 20
const statusFilter = ref('all')
const selectedIdx = ref(null)
const detail = ref(null)
const showFollow = ref(false)
const followView = computed(() => !!detail.value?.follow_overview && (!detail.value?.summary || showFollow.value))
const dirty = ref(false)
const parsedText = reactive({ characters: '', lore: '' })
const parsedError = reactive({ characters: '', lore: '' })
const parsedSaving = ref(false)
const batchJob = ref(null)
const batchMode = ref('continue')
const batchLimit = ref(5)
const batchStopAt = ref(null)
const batchForce = ref(false)
const batchRetryFailed = ref(false)
const batchCheckProviderId = ref(null)
const batchStarting = ref(false)
const batchStopLoading = ref(false)
const indexStatus = ref({
  available: true,
  total_main: 0,
  summary_count: 0,
  indexed_count: 0,
  complete: false,
  needs_rebuild: false,
  collection: 'novel_summary',
  dim: 1024,
  error: null
})
const indexStatusLoading = ref(false)
const indexBuilding = ref(false)
const allowPartial = ref(false)
const searchQuery = ref('')
const searchTopK = ref(5)
const searching = ref(false)
const searchResults = ref([])
const searchCompleted = ref(false)
const searchRerank = ref(false)
const searchCompare = ref(null)
let batchTimer = null
let batchPollInFlight = false
let batchDisposed = false

const form = reactive({
  summary: '',
  key_events: '',
  characters: '',
  cliffhanger: ''
})

const activeProvider = computed(() =>
  providers.value.find((provider) => provider.id === activeId.value)
)
const canExtract = computed(
  () =>
    Boolean(activeProvider.value) &&
    (activeProvider.value.is_local || activeProvider.value.has_key)
)
const batchRunning = computed(() =>
  ['queued', 'running', 'stopping'].includes(batchJob.value?.status)
)
const isPartial = computed(
  () =>
    indexStatus.value.summary_count < indexStatus.value.total_main
)
const indexTag = computed(() => {
  if (!indexStatus.value.available) {
    return { label: '不可用', type: 'danger' }
  }
  if (indexStatus.value.indexed_count === 0) {
    return { label: '未建立', type: 'info' }
  }
  if (indexStatus.value.needs_rebuild) {
    return { label: '需重建', type: 'warning' }
  }
  if (indexStatus.value.complete) {
    return { label: '已建立', type: 'success' }
  }
  return { label: '部分索引', type: 'warning' }
})
const canBuildIndex = computed(
  () =>
    indexStatus.value.available &&
    indexStatus.value.summary_count > 0 &&
    !indexBuilding.value &&
    !batchRunning.value &&
    !dirty.value &&
    (!isPartial.value || allowPartial.value)
)
const searchDisabled = computed(
  () =>
    !indexStatus.value.available ||
    indexStatus.value.indexed_count === 0 ||
    indexBuilding.value
)
const canSearch = computed(
  () =>
    !searchDisabled.value &&
    !searching.value &&
    Boolean(searchQuery.value.trim())
)
// 关掉重排时只有一栏，行为和以前完全一致
const searchColumns = computed(() => {
  const compare = searchCompare.value
  if (!compare) {
    return searchResults.value.length
      ? [{ key: 'rrf', title: 'RRF', note: '', items: searchResults.value }]
      : []
  }
  return [
    {
      key: 'rrf',
      title: 'RRF 融合',
      note: `召回 ${compare.recall} 条，按融合分排序`,
      items: compare.rrf_items || []
    },
    {
      key: 'rerank',
      title: '重排后',
      note: compare.rerank_applied
        ? `bge-reranker，${compare.rerank_latency_ms}ms`
        : '未生效，与左栏一致',
      items: compare.rerank_items || []
    }
  ].filter((column) => column.items.length)
})
const canStartBatch = computed(
  () =>
    canExtract.value &&
    !batchRunning.value &&
    !batchStarting.value &&
    !extracting.value &&
    !saving.value &&
    !indexBuilding.value
)
const batchTag = computed(() => {
  const status = batchJob.value?.status
  if (status === 'queued') return { label: '排队中', type: 'info' }
  if (status === 'running') return { label: '生成中', type: 'warning' }
  if (status === 'stopping') return { label: '停止中', type: 'warning' }
  if (status === 'done') return { label: '批量完成', type: 'success' }
  if (status === 'stopped') return { label: '已停止', type: 'info' }
  if (status === 'failed') return { label: '失败', type: 'danger' }
  if (status === 'interrupted') return { label: '已中断', type: 'warning' }
  return { label: '', type: 'info' }
})
const detailMeta = computed(() => {
  if (!detail.value) return null
  const meta = detail.value.summary
    ? { ...detail.value.summary }
    : { ...(detail.value.extraction || {}) }
  if (detail.value.extraction) {
    meta.text_truncated = detail.value.extraction.text_truncated
    meta.output_trimmed = detail.value.extraction.output_trimmed
  }
  return Object.keys(meta).length ? meta : null
})
const aliasIssues = computed(
  () => detail.value?.extraction?.alias_check?.issues || []
)

function statusTag(status) {
  if (status === 'done') return { label: '已生成', type: 'success' }
  if (status === 'failed') return { label: '失败', type: 'danger' }
  return { label: '待处理', type: 'info' }
}

function pretty(value) {
  return JSON.stringify(value || {}, null, 2)
}

function fillParsed(extraction) {
  const parsed = extraction?.parsed || {}
  parsedText.characters = JSON.stringify(parsed.characters ?? {}, null, 2)
  parsedText.lore = JSON.stringify(parsed.lore ?? [], null, 2)
  parsedError.characters = ''
  parsedError.lore = ''
}

watch(() => detail.value?.extraction, fillParsed, { immediate: true })

function resetParsed() {
  fillParsed(detail.value?.extraction)
}

async function saveParsed() {
  const chapterIdx = detail.value?.chapter_idx
  if (chapterIdx === null || chapterIdx === undefined) return
  const payload = {}
  for (const key of ['characters', 'lore']) {
    const raw = parsedText[key].trim()
    if (!raw) {
      parsedError[key] = '不能为空'
      return
    }
    try {
      payload[key] = JSON.parse(raw)
      parsedError[key] = ''
    } catch {
      parsedError[key] = '不是合法的 JSON'
      return
    }
  }
  parsedSaving.value = true
  try {
    const response = await updateExtractionApi(
      bookId.value,
      chapterIdx,
      payload
    )
    detail.value.extraction = response.data
    ElMessage.success('已保存；人物与世界观索引需要重建后生效')
  } catch (error) {
    if (error?.isBusiness) {
      ElMessage.error(error.message || '保存失败')
    }
  } finally {
    parsedSaving.value = false
  }
}

async function loadSettings() {
  const response = await getLlmSettingsApi()
  providers.value = response.data.providers || []
  activeId.value = response.data.active_id
  // 模型复核默认关闭：规则层始终生效；只有手动选模型才调用 LLM。
}

async function loadOverview() {
  const response = await getSummaryOverviewApi(bookId.value)
  overview.value = response.data
}

async function loadList() {
  listLoading.value = true
  try {
    const response = await listSummariesApi(bookId.value, {
      page: page.value,
      page_size: pageSize,
      status: statusFilter.value
    })
    items.value = response.data.items || []
    listTotal.value = response.data.total || 0
  } finally {
    listLoading.value = false
  }
}

async function loadLatestBatch(silent = true) {
  const response = await getLatestBatchJobApi(bookId.value, silent)
  batchJob.value = response.data || null
  if (batchRunning.value) scheduleBatchPolling()
  return batchJob.value
}

async function loadIndexStatus() {
  const response = await getSummaryIndexStatusApi(bookId.value)
  indexStatus.value = response.data
  if (indexStatus.value.indexed_count === 0) {
    searchResults.value = []
    searchCompare.value = null
    searchCompleted.value = false
  }
  return indexStatus.value
}

async function loadAll() {
  loading.value = true
  indexStatusLoading.value = true
  try {
    await Promise.all([
      loadSettings(),
      loadOverview(),
      loadList(),
      loadLatestBatch(true).catch(() => {
        batchJob.value = null
      }),
      loadIndexStatus()
        .catch(() => {
          indexStatus.value = {
            ...indexStatus.value,
            available: false,
            error: '无法获取摘要索引状态'
          }
        })
        .finally(() => {
          indexStatusLoading.value = false
        })
    ])
    if (batchStopAt.value === null) {
      batchStopAt.value = overview.value.next_chapter_number || 1
    }
  } catch {
    // 请求层已提示
  } finally {
    loading.value = false
  }
}

function clearBatchTimer() {
  if (batchTimer) {
    clearTimeout(batchTimer)
    batchTimer = null
  }
}

function scheduleBatchPolling() {
  clearBatchTimer()
  if (batchDisposed || !batchRunning.value) return
  batchTimer = setTimeout(pollBatch, 1000)
}

async function refreshAfterBatch() {
  await Promise.all([loadOverview(), loadList()])
  if (selectedIdx.value !== null) await loadDetail()
}

async function handleBatchTerminal(job) {
  if (job.status === 'done') {
    if (job.failed > 0) {
      ElMessage.warning(`批量完成，${job.failed} 章失败`)
    } else {
      ElMessage.success('批量完成')
    }
  } else if (job.status === 'stopped') {
    ElMessage.info('已停止，下次继续')
  } else if (job.status === 'failed') {
    ElMessage.error(job.error || '批量任务失败')
  } else if (job.status === 'interrupted') {
    ElMessage.warning('任务被中断，可重新开始')
  }
  try {
    await refreshAfterBatch()
    await loadIndexStatus()
    if (job.generated > 0) {
      ElMessage.info('摘要已增加，建议重建摘要索引')
    }
  } catch {
    // 请求层已提示
  }
}

function summaryPreview(text) {
  const value = String(text || '')
  return value.length > 150 ? `${value.slice(0, 150)}…` : value
}

function scoreText(value) {
  return Number(value || 0).toFixed(6)
}

async function buildIndex() {
  if (indexBuilding.value) return
  if (dirty.value) {
    ElMessage.warning('有未保存修改，保存后再重建索引')
    return
  }
  if (!indexStatus.value.available) {
    ElMessage.warning('摘要索引当前不可用')
    return
  }
  if (isPartial.value && !allowPartial.value) {
    ElMessage.warning('请勾选“仅试跑，允许部分索引”')
    return
  }
  indexBuilding.value = true
  try {
    const response = await buildSummaryIndexApi(bookId.value, {
      allow_partial: allowPartial.value
    })
    await loadIndexStatus()
    searchResults.value = []
    searchCompare.value = null
    searchCompleted.value = false
    ElMessage.success(
      `摘要索引已重建：${response.data.indexed_count} 个 point`
    )
  } catch {
    // 请求层已提示
  } finally {
    indexBuilding.value = false
  }
}

async function runSummarySearch() {
  const query = searchQuery.value.trim()
  if (!query || !canSearch.value) return
  searching.value = true
  searchResults.value = []
  searchCompare.value = null
  searchCompleted.value = false
  try {
    const response = await searchSummariesApi(bookId.value, {
      query,
      top_k: searchTopK.value,
      rerank: searchRerank.value,
      recall: 20
    })
    if (searchRerank.value && response.data && !Array.isArray(response.data)) {
      searchCompare.value = response.data
      searchResults.value = response.data.rrf_items || []
    } else {
      searchResults.value = response.data || []
    }
    searchCompleted.value = true
  } catch {
    // 请求层已提示
  } finally {
    searching.value = false
  }
}

async function selectChapterByIndex(chapterIdx) {
  if (!(await confirmDiscard())) return
  if (statusFilter.value !== 'all') {
    statusFilter.value = 'all'
  }
  const targetPage = Math.floor(chapterIdx / pageSize) + 1
  if (page.value !== targetPage) {
    page.value = targetPage
  }
  await loadList()
  selectedIdx.value = chapterIdx
  await loadDetail()
  await nextTick()
  document.querySelector('.detail-panel')?.scrollIntoView({
    behavior: 'smooth',
    block: 'start'
  })
}

async function pollBatch() {
  batchTimer = null
  if (batchPollInFlight) {
    scheduleBatchPolling()
    return
  }
  batchPollInFlight = true
  const previous = batchJob.value?.status
  try {
    const response = await getLatestBatchJobApi(bookId.value, true)
    batchJob.value = response.data || null
    if (
      ['queued', 'running', 'stopping'].includes(previous) &&
      batchJob.value &&
      ['done', 'stopped', 'failed', 'interrupted'].includes(
        batchJob.value.status
      )
    ) {
      handleBatchTerminal(batchJob.value)
    }
  } catch {
    // 轮询失败静默，下一轮继续
  } finally {
    batchPollInFlight = false
  }
  if (!batchDisposed && batchRunning.value) scheduleBatchPolling()
}

async function startBatch() {
  if (!canStartBatch.value) return
  const payload = {
    force: batchForce.value,
    retry_failed: batchRetryFailed.value
  }
  if (batchCheckProviderId.value) {
    payload.check_provider_id = batchCheckProviderId.value
  }
  if (batchMode.value === 'limit') {
    if (!batchLimit.value || batchLimit.value < 1) {
      ElMessage.warning('请填写本次处理的章数')
      return
    }
    payload.limit = Number(batchLimit.value)
  } else if (batchMode.value === 'stop_at') {
    if (!batchStopAt.value || batchStopAt.value < 1) {
      ElMessage.warning('请填写停止章节')
      return
    }
    payload.stop_at = Number(batchStopAt.value)
  }

  if (batchForce.value) {
    try {
      await ElMessageBox.confirm(
        '强制重跑会覆盖已生成和已人工编辑的摘要，是否继续？',
        '强制重跑',
        {
          type: 'warning',
          confirmButtonText: '强制重跑',
          cancelButtonText: '取消'
        }
      )
    } catch {
      return
    }
  }
  if (dirty.value && !(await confirmDiscard())) return

  batchStarting.value = true
  try {
    const response = await startBatchJobApi(bookId.value, payload)
    batchJob.value = response.data
    ElMessage.success('批量任务已启动')
    scheduleBatchPolling()
  } catch {
    try {
      await loadLatestBatch(true)
    } catch {
      // 请求层已提示
    }
    scheduleBatchPolling()
  } finally {
    batchStarting.value = false
  }
}

async function stopBatch() {
  if (
    indexBuilding.value ||
    !batchRunning.value ||
    batchJob.value?.status === 'stopping'
  ) {
    return
  }
  batchStopLoading.value = true
  try {
    const response = await stopBatchJobApi(bookId.value)
    batchJob.value = response.data
    ElMessage.info('当前章完成后停止')
    scheduleBatchPolling()
  } catch {
    // 请求层已提示
  } finally {
    batchStopLoading.value = false
  }
}

function displaySummary() {
  if (!followView.value) return detail.value?.summary
  const data = detail.value.follow_overview
  return { summary: data.parts.join('\n\n'), key_events: data.events.map(e => e.claim), characters: [], cliffhanger: '' }
}
watch(showFollow, () => fillForm(displaySummary()))
function fillForm(summary) {
  form.summary = summary?.summary || ''
  form.key_events = (summary?.key_events || []).join('\n')
  form.characters = (summary?.characters || []).join('，')
  form.cliffhanger = summary?.cliffhanger || ''
  dirty.value = false
}

async function loadDetail() {
  if (selectedIdx.value === null) return
  detailLoading.value = true
  try {
    const response = await getSummaryDetailApi(bookId.value, selectedIdx.value)
    detail.value = response.data
    showFollow.value = false
    fillForm(displaySummary())
  } finally {
    detailLoading.value = false
  }
}

async function confirmDiscard() {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      '当前摘要还有未保存的修改，确定放弃吗？',
      '未保存修改',
      {
        type: 'warning',
        confirmButtonText: '放弃修改',
        cancelButtonText: '继续编辑'
      }
    )
    dirty.value = false
    return true
  } catch {
    return false
  }
}

async function selectChapter(item) {
  if (item.chapter_idx === selectedIdx.value) return
  if (!(await confirmDiscard())) return
  selectedIdx.value = item.chapter_idx
  await loadDetail()
}

async function closeDetail() {
  if (!(await confirmDiscard())) return
  selectedIdx.value = null
  detail.value = null
  dirty.value = false
}

async function changeFilter() {
  page.value = 1
  await loadList()
}

function markDirty() {
  if (detail.value?.summary && !followView.value) dirty.value = true
}

async function generate() {
  if (
    selectedIdx.value === null ||
    !canExtract.value ||
    batchRunning.value ||
    indexBuilding.value
  ) {
    return
  }
  if (dirty.value && !(await confirmDiscard())) return
  extracting.value = true
  try {
    const response = await extractChapterApi(
      bookId.value,
      selectedIdx.value,
      null,
      batchCheckProviderId.value
    )
    const data = response.data
    if (data.status === 'success') {
      const fixed = data.alias_check?.dropped || 0
      const flagged = data.alias_check?.flagged || 0
      const notes = []
      if (fixed) notes.push(`自动修正 ${fixed} 条称谓`)
      if (flagged) notes.push(`${flagged} 条待核对`)
      ElMessage.success(
        notes.length
          ? `摘要生成完成，${notes.join('；')}；索引需要重建`
          : '摘要生成完成；索引需要重建'
      )
    } else {
      ElMessage.error(data.error || '抽取失败')
    }
    await Promise.all([
      loadDetail(),
      loadOverview(),
      loadList(),
      loadIndexStatus().catch(() => {})
    ])
    if (data.status === 'success') {
      indexStatus.value = { ...indexStatus.value, needs_rebuild: true }
    }
  } catch {
    // 请求层已提示
  } finally {
    extracting.value = false
  }
}

async function saveSummary() {
  if (followView.value || !detail.value?.summary || batchRunning.value || indexBuilding.value) {
    return
  }
  let payload
  try {
    payload = {
      summary: form.summary.trim(),
      key_events: form.key_events
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean),
      characters: form.characters
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean),
      cliffhanger: form.cliffhanger.trim()
    }
  } catch (error) {
    ElMessage.error(error.message)
    return
  }

  saving.value = true
  try {
    const response = await updateSummaryApi(
      bookId.value,
      selectedIdx.value,
      payload
    )
    detail.value = response.data
    fillForm(response.data.summary)
    indexStatus.value = { ...indexStatus.value, needs_rebuild: true }
    ElMessage.success('摘要已保存；建议重建摘要索引')
    await Promise.all([loadOverview(), loadList()])
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

function goDetail() {
  router.push(`/books/${bookId.value}`)
}

function goSettings() {
  router.push('/settings')
}

onMounted(loadAll)

onUnmounted(() => {
  batchDisposed = true
  clearBatchTimer()
})

onBeforeRouteLeave(async () => {
  return await confirmDiscard()
})
</script>

<style scoped>
.summary-page {
  max-width: 1420px;
}

.detail-top {
  margin-bottom: 16px;
}

.back-button {
  margin-bottom: 10px;
  color: var(--muted);
}

.summary-header {
  align-items: center;
  margin-bottom: 0;
}

.eyebrow {
  margin-bottom: 7px;
  color: var(--amber);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
}

.header-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.section-alert {
  margin-bottom: 12px;
}

.batch-panel {
  padding: 16px 18px;
  margin-bottom: 14px;
}

.batch-head {
  margin-bottom: 11px;
}

.batch-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px 12px;
}

.batch-modes {
  flex-wrap: wrap;
}

.batch-number {
  width: 128px;
}

.batch-progress {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--card-soft);
}

.batch-progress-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 11px;
}

.batch-progress-head strong {
  color: var(--gold);
}

.batch-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 8px;
  color: var(--muted);
  font-size: 10px;
}

.batch-stop-row {
  margin-top: 8px;
  color: var(--gold);
  font-size: 10px;
}

.batch-alert {
  margin-top: 10px;
}

.batch-history {
  margin-top: 9px;
  color: var(--muted);
  font-size: 10px;
}

.summary-index-panel,
.summary-search-panel {
  padding: 16px 18px;
  margin-bottom: 14px;
}

.index-head,
.search-head {
  margin-bottom: 11px;
}

.index-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 0.8fr);
  gap: 18px;
  align-items: start;
}

.index-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.index-metrics > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--card-soft);
}

.index-metrics span {
  color: var(--muted);
  font-size: 10px;
}

.index-metrics strong {
  color: var(--text);
  font-size: 18px;
}

.index-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.index-action-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
}

.index-note,
.index-hint {
  color: var(--muted);
  font-size: 10px;
  line-height: 1.6;
}

.index-partial {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  color: var(--gold);
  font-size: 11px;
}

.index-warning {
  padding: 7px 10px;
  border: 1px solid var(--gold-dim);
  border-radius: 8px;
  background: var(--gold-soft-bg);
  color: var(--gold);
  font-size: 11px;
}

.index-alert {
  margin-top: 2px;
}

.search-partial {
  color: var(--gold);
  font-size: 10px;
}

.summary-search-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
}

.summary-search-bar > .el-input {
  flex: 1;
  min-width: 240px;
}

.search-topk {
  width: 96px;
}

.rerank-toggle {
  margin-right: 2px;
  white-space: nowrap;
}

.batch-check {
  width: 170px;
}

.search-hint {
  margin-top: 8px;
  color: var(--muted);
  font-size: 11px;
}

.danger-text {
  color: var(--danger-hi);
}

.warning-text {
  color: var(--gold);
}

.rerank-meta {
  margin-top: 8px;
  color: var(--muted);
  font-size: 11px;
}

.search-columns {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}

.search-columns.split {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.search-column-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 10px;
}

.search-column-head strong {
  color: var(--text);
  font-size: 12px;
  letter-spacing: 0.04em;
}

.search-columns.split .search-results {
  max-height: 460px;
}

.search-result-main {
  flex: 1;
  min-width: 0;
}

.rank-delta {
  margin: 0 2px;
  color: var(--gold);
  font-style: normal;
  font-weight: 700;
}

.rank-delta.down {
  color: var(--muted);
}

.search-results {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 560px;
  margin-top: 12px;
  overflow: auto;
  padding-right: 4px;
}

.search-result-card {
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 12px;
  background: var(--card-soft);
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease;
}

.search-result-card:hover {
  border-color: var(--gold-dim);
  background: var(--surface-hover);
}

.search-result-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.search-rank {
  display: grid;
  place-items: center;
  flex: none;
  width: 25px;
  height: 25px;
  border-radius: 7px;
  background: var(--gold-soft-bg);
  color: var(--gold);
  font-size: 11px;
  font-weight: 700;
}

.search-result-head strong {
  display: block;
  color: var(--text);
  font-size: 13px;
}

.search-scores {
  display: block;
  margin-top: 3px;
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 9px;
}

.search-summary {
  margin-top: 9px;
  color: #d4cebd;
  font-family: 'Songti SC', 'SimSun', serif;
  font-size: 13px;
  line-height: 1.8;
}

.search-extra {
  margin-top: 8px;
  border-top: 1px solid var(--line-soft);
  border-bottom: 0;
}

.search-extra :deep(.el-collapse-item__header) {
  min-height: 30px;
  height: auto;
  border-bottom: 0;
  background: transparent;
  color: var(--muted);
  font-size: 10px;
}

.search-extra :deep(.el-collapse-item__wrap) {
  border-bottom: 0;
  background: transparent;
}

.search-extra :deep(.el-collapse-item__content) {
  padding-bottom: 2px;
}

.search-extra-body {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  color: #d4cebd;
  font-size: 11px;
  line-height: 1.7;
}

.search-extra-body > div > span {
  display: block;
  margin-bottom: 3px;
  color: var(--gold);
  font-size: 10px;
}

.search-extra-body p {
  margin: 0;
  color: var(--muted);
}

.search-extra-body ul {
  margin: 0;
  padding-left: 16px;
  list-style: disc;
}

.summary-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 14px 0;
}

.summary-stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 11px 14px;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: var(--card);
}

.summary-stat span {
  color: var(--muted);
  font-size: 11px;
}

.summary-stat strong {
  font-size: 20px;
}

.summary-stat small {
  color: var(--muted);
  font-size: 10px;
}

.summary-stat .green {
  color: var(--green);
}

.summary-stat .danger {
  color: var(--danger);
}

.summary-layout {
  display: grid;
  grid-template-columns: 370px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.list-panel,
.detail-panel {
  min-height: 600px;
  padding: 16px;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.panel-head h2 {
  font-size: 16px;
}

.panel-head p {
  margin-top: 4px;
  color: var(--muted);
  font-size: 10px;
}

.chapter-list {
  max-height: 700px;
  overflow: auto;
}

.chapter-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 10px 11px;
  border: 0;
  border-bottom: 1px solid var(--line-soft);
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.chapter-row:hover {
  background: var(--card-soft);
}

.chapter-row.active {
  background: var(--gold-soft-bg);
}

.chapter-row-main {
  min-width: 0;
}

.chapter-row-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
}

.chapter-row-title strong {
  max-width: 220px;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chapter-row p {
  display: -webkit-box;
  margin-top: 4px;
  overflow: hidden;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.chapter-no {
  flex: none;
  color: var(--muted);
  font-size: 9px;
}

.list-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 10px;
  color: var(--muted);
  font-size: 10px;
}

.detail-empty {
  display: grid;
  place-items: center;
  min-height: 560px;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.detail-head h2 {
  font-size: 18px;
}

.detail-head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px 12px;
  margin: 10px 0 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-soft);
  color: var(--muted);
  font-size: 10px;
}

.generate-empty {
  padding: 40px 0;
}

.disabled-copy {
  color: var(--gold);
  font-size: 11px;
  text-align: center;
}

.generate-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  margin: 10px 0 14px;
}

.generate-row .muted {
  font-size: 10px;
}

.summary-form {
  margin-top: 4px;
}

.summary-form :deep(.el-form-item__label) {
  padding-bottom: 4px;
  color: var(--muted);
  font-size: 11px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.save-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
}

.dirty-copy {
  color: var(--gold);
  font-size: 10px;
}

.raw-panel {
  margin: 12px 0;
  padding: 12px 14px;
  border: 1px solid rgba(176, 85, 96, 0.45);
  border-radius: 10px;
  background: rgba(122, 37, 48, 0.14);
}

.raw-panel h3 {
  margin: 8px 0 5px;
  color: var(--danger-hi);
  font-size: 12px;
}

.raw-panel p {
  color: var(--danger-hi);
  font-size: 11px;
  line-height: 1.7;
}

.raw-panel pre {
  max-height: 260px;
  margin: 0;
  overflow: auto;
  padding: 9px;
  border-radius: 7px;
  background: var(--card-soft);
  color: var(--muted);
  font-size: 10px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.readonly-block {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.readonly-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.readonly-head h3 {
  font-size: 12px;
}

.readonly-block pre {
  max-height: 260px;
  margin: 0 0 12px;
  overflow: auto;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--card-soft);
  color: var(--muted);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.parsed-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.parsed-hint {
  color: var(--muted);
  font-size: 11px;
}

.parsed-input {
  margin-bottom: 4px;
}

.parsed-input :deep(.el-textarea__inner) {
  background: var(--card-soft);
  color: var(--text);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  line-height: 1.65;
  white-space: pre;
  overflow-x: auto;
}

.parsed-input.invalid :deep(.el-textarea__inner) {
  border-color: var(--danger-hi);
}

.parsed-error {
  margin: 4px 0 14px;
  color: var(--danger-hi);
  font-size: 12px;
}

.alias-check-block {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.alias-check-error {
  margin: 4px 0 10px;
  color: var(--danger-hi);
  font-size: 12px;
}

.alias-check-table {
  margin-top: 8px;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: var(--card-soft);
  --el-table-border-color: var(--line);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
}

.alias-quote {
  color: var(--muted);
}

@media (max-width: 1000px) {
  .summary-layout {
    grid-template-columns: 1fr;
  }

  .list-panel,
  .detail-panel {
    min-height: 0;
  }

  .chapter-list {
    max-height: 430px;
  }
}

@media (max-width: 900px) {
  .summary-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .batch-form {
    align-items: flex-start;
  }

  .index-body {
    grid-template-columns: 1fr;
  }

  .index-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .summary-search-bar > .el-input {
    min-width: 0;
  }

  .search-columns.split {
    grid-template-columns: 1fr;
  }

  .search-extra-body {
    grid-template-columns: 1fr;
  }

  .form-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .summary-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
