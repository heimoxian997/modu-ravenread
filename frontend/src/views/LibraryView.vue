<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="eyebrow">本地书库</div>
        <h1 class="page-title">我的书库</h1>
        <p class="page-subtitle">
          小说只保存在这台电脑上。第一步先把章节结构整理好，之后才能建立索引与 AI 伴读。
        </p>
      </div>
      <el-button type="primary" size="large" @click="router.push('/upload')">
        <el-icon><Plus /></el-icon>
        导入新书
      </el-button>
    </div>

    <div class="summary-grid">
      <div class="summary-item">
        <span class="summary-icon"><Reading /></span>
        <div>
          <strong>{{ books.length }}</strong>
          <span>本机藏书</span>
        </div>
      </div>
      <div class="summary-item">
        <span class="summary-icon"><Document /></span>
        <div>
          <strong>{{ totalChapters }}</strong>
          <span>个章节</span>
        </div>
      </div>
      <div class="summary-item">
        <span class="summary-icon"><DataLine /></span>
        <div>
          <strong>{{ formatCharCount(totalChars) }}</strong>
          <span>总字数</span>
        </div>
      </div>
      <div class="summary-item">
        <span class="summary-icon"><Lock /></span>
        <div>
          <strong>离线</strong>
          <span>无云端同步</span>
        </div>
      </div>
    </div>

    <div v-loading="loading" class="panel library-panel">
      <template v-if="!loading && !books.length">
        <el-empty description="书架还是空的" :image-size="118">
          <p class="empty-copy">
            上传一份中文小说 txt，填写 1–2 个章节标题样例，我们会帮你解析章节。
          </p>
          <el-button type="primary" size="large" @click="router.push('/upload')">
            <el-icon><Upload /></el-icon>
            上传第一本书
          </el-button>
        </el-empty>
      </template>

      <el-table
        v-else
        :data="books"
        class="book-table"
        row-key="id"
        @row-click="goDetail"
      >
        <el-table-column label="书名" min-width="230">
          <template #default="{ row }">
            <div class="book-title-cell">
              <span class="book-spine"></span>
              <div>
                <button class="book-link" type="button" @click.stop="goDetail(row)">
                  {{ row.title }}
                </button>
                <span class="book-author">{{ row.author || '未填写作者' }}</span>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="章节" width="180">
          <template #default="{ row }">
            <div class="chapter-count">
              <strong>{{ row.chapter_count }}</strong>
              <span>正文 {{ row.main_chapter_count }} / 番外 {{ row.extra_chapter_count }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="字数" width="130">
          <template #default="{ row }">
            <span class="word-count">{{ formatCharCount(row.char_count) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="创建时间" width="165">
          <template #default="{ row }">
            <span class="muted">{{ formatDateTime(row.create_time) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="170" align="right">
          <template #default="{ row }">
            <el-button text class="detail-gold" @click.stop="goDetail(row)">详情</el-button>
            <el-button text type="danger" @click.stop="removeBook(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteBookApi, listBooksApi } from '@/api/book'
import { formatCharCount, formatDateTime } from '@/utils/text'

const router = useRouter()
const books = ref([])
const loading = ref(false)

const totalChars = computed(() =>
  books.value.reduce((sum, book) => sum + (book.char_count || 0), 0)
)
const totalChapters = computed(() =>
  books.value.reduce((sum, book) => sum + (book.chapter_count || 0), 0)
)

async function loadBooks() {
  loading.value = true
  try {
    const response = await listBooksApi()
    books.value = response.data || []
  } finally {
    loading.value = false
  }
}

function goDetail(book) {
  router.push(`/books/${book.id}`)
}

async function removeBook(book) {
  try {
    await ElMessageBox.confirm(
      `删除《${book.title}》后，书籍、章节与源文件都会从本机移除，且无法恢复。`,
      '确认删除书籍',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }
  try {
    await deleteBookApi(book.id)
    ElMessage.success('书籍已删除')
    await loadBooks()
  } catch {
    // 请求层已提示错误
  }
}

onMounted(loadBooks)
</script>

<style scoped>
.eyebrow {
  margin-bottom: 7px;
  color: var(--amber);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.2em;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}

.summary-item {
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 16px 18px;
  border: 1px solid var(--line-soft);
  border-radius: 13px;
  background: var(--card-soft);
  box-shadow: var(--shadow-soft);
}

.summary-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 11px;
  background: var(--gold-soft-bg);
  color: var(--gold);
}

.summary-icon svg {
  width: 18px;
  height: 18px;
}

.summary-item div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.summary-item strong {
  font-size: 19px;
  font-weight: 700;
}

.summary-item div span {
  color: var(--muted);
  font-size: 11px;
}

.library-panel {
  min-height: 320px;
  overflow: hidden;
}

.empty-copy {
  max-width: 500px;
  margin: -4px auto 18px;
  color: var(--ink-soft);
  line-height: 1.8;
}

.book-table {
  width: 100%;
}

:deep(.book-table .el-table__row) {
  cursor: pointer;
}

.book-title-cell {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.book-spine {
  flex: none;
  width: 4px;
  height: 36px;
  border-radius: 3px;
  background: linear-gradient(180deg, var(--teal), var(--green));
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.45);
}

:deep(.book-table .el-table__row:nth-child(even) .book-spine) {
  background: linear-gradient(180deg, var(--violet-hi), var(--violet));
}

:deep(.book-table .el-table__row:nth-child(3n) .book-spine) {
  background: linear-gradient(180deg, var(--green-soft), var(--green));
}

.book-title-cell > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.book-link {
  max-width: 100%;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-size: 15px;
  font-weight: 700;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.book-link:hover {
  color: var(--gold);
}

.book-author {
  color: var(--muted);
  font-size: 11px;
}

.chapter-count {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.chapter-count strong {
  font-size: 16px;
}

.chapter-count span {
  color: var(--muted);
  font-size: 11px;
}

.word-count {
  color: #d4cebd;
  font-size: 13px;
}

@media (max-width: 1000px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
