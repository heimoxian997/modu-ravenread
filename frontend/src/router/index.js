import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      component: () => import('@/layouts/Layout.vue'),
      children: [
        {
          path: '',
          name: 'library',
          component: () => import('@/views/LibraryView.vue'),
          meta: { title: '我的书库' }
        },
        {
          path: 'upload',
          name: 'upload',
          component: () => import('@/views/UploadView.vue'),
          meta: { title: '导入新书' }
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
          meta: { title: '设置 · 模型服务' }
        },
        {
          path: 'books/:id(\\d+)/corrections',
          name: 'book-corrections',
          component: () => import('@/views/CorrectionsView.vue'),
          meta: { title: '纠错审核' }
        },
        {
          path: 'books/:id(\\d+)/entities',
          name: 'book-entities',
          component: () => import('@/views/EntityView.vue'),
          meta: { title: '人物与世界' }
        },
        {
          path: 'books/:id(\\d+)/summaries',
          name: 'book-summaries',
          component: () => import('@/views/SummaryView.vue'),
          meta: { title: '章节摘要' }
        },
        {
          path: 'books/:id(\\d+)/index',
          name: 'book-index',
          component: () => import('@/views/IndexView.vue'),
          meta: { title: '索引与检索' }
        },
        {
          path: 'books/:id(\\d+)/chunks',
          name: 'book-chunks',
          component: () => import('@/views/ChunkView.vue'),
          meta: { title: '正文切块' }
        },
        {
          path: 'books/:id(\\d+)',
          name: 'book-detail',
          component: () => import('@/views/BookDetailView.vue'),
          meta: { title: '书籍详情' }
        }
      ]
    },
    {
      path: '/books/:id(\\d+)/read/:idx(\\d+)?',
      name: 'book-reader',
      component: () => import('@/views/ReaderView.vue'),
      meta: { title: '阅读' }
    },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ],
  scrollBehavior() {
    return { top: 0 }
  }
})

export default router
