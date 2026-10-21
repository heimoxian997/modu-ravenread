<template>
  <el-container class="app-shell">
    <el-aside width="232px" class="sidebar">
      <button class="brand" type="button" @click="router.push('/')">
        <img
          :src="logoUrl"
          class="brand-mark brand-mark-img"
          alt="墨读"
        />
        <span class="brand-text">
          <strong>墨读</strong>
          <small>RavenRead · 本地小说书库</small>
        </span>
      </button>

      <el-menu router :default-active="activeMenu" class="nav-menu">
        <el-menu-item index="/">
          <el-icon><Collection /></el-icon>
          <span>我的书库</span>
        </el-menu-item>
        <el-menu-item index="/upload">
          <el-icon><Upload /></el-icon>
          <span>导入新书</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
      </el-menu>

      <div class="privacy-card">
        <el-icon class="privacy-icon"><Lock /></el-icon>
        <div>
          <strong>只存本机</strong>
          <p>书稿、索引与聊天只存本机；模型服务由你选择。</p>
        </div>
      </div>
    </el-aside>

    <el-container class="main-wrap">
      <el-header class="topbar">
        <div class="topbar-title">
          <span class="section-name">{{ route.meta.title || '墨读' }}</span>
          <span class="divider">/</span>
          <span class="muted">本地书库 · 索引与模型</span>
        </div>
        <el-tag class="model-tag" :type="modelTag.type" effect="plain" round>
          <el-icon><Connection /></el-icon>
          {{ modelTag.label }}
        </el-tag>
      </el-header>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import logoUrl from '@/assets/brand/logo.svg'
import {
  refreshSettingsStatus,
  useSettingsStore
} from '@/stores/settings'

const route = useRoute()
const router = useRouter()
const settings = useSettingsStore()

const activeMenu = computed(() => {
  if (route.path.startsWith('/settings')) return '/settings'
  if (route.path.startsWith('/books/')) return '/'
  return route.path === '/upload' ? '/upload' : '/'
})

const modelTag = computed(() => {
  if (settings.loading || settings.error || !settings.status) {
    return { label: '本地数据', type: 'info' }
  }
  const active = settings.status.active_provider
  if (!active) return { label: '未配置模型', type: 'info' }
  if (active.is_local) return { label: '本地模型', type: 'success' }
  if (settings.status.active_has_key) {
    return { label: '云端模型', type: 'warning' }
  }
  return { label: '待填 Key', type: 'warning' }
})

onMounted(refreshSettingsStatus)
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  background:
    radial-gradient(circle at 82% 8%, rgba(74, 122, 110, 0.09), transparent 30%),
    radial-gradient(circle at 10% 0%, rgba(196, 163, 90, 0.05), transparent 24%),
    var(--bg);
}

.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 24px 16px 20px;
  background: var(--sidebar);
  color: var(--text);
  border-right: 1px solid var(--line-soft);
  overflow: hidden;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  border: 0;
  padding: 4px 8px 22px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.brand-mark-img {
  display: block;
  flex: none;
  width: 48px;
  height: 48px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(196, 163, 90, 0.06);
  object-fit: contain;
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.45);
}

.brand-text {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.brand-text strong {
  font-size: 23px;
  letter-spacing: 0.12em;
}

.brand-text small {
  color: var(--muted);
  font-size: 9px;
  line-height: 1.5;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.nav-menu {
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--muted);
  --el-menu-hover-bg-color: var(--surface-hover);
  --el-menu-active-color: var(--gold);
  border-right: 0;
  margin-top: 10px;
}

.nav-menu :deep(.el-menu-item) {
  height: 48px;
  margin-bottom: 6px;
  border-radius: 11px;
  font-size: 14px;
}

.nav-menu :deep(.el-menu-item.is-active) {
  background: var(--green-active-bg);
  color: var(--gold);
}

.privacy-card {
  display: flex;
  gap: 10px;
  margin-top: auto;
  padding: 14px;
  border: 1px solid var(--line-soft);
  border-radius: 12px;
  color: var(--muted);
  background: var(--card-soft);
  line-height: 1.7;
}

.privacy-icon {
  flex: none;
  margin-top: 2px;
  color: var(--gold);
}

.privacy-card strong {
  display: block;
  margin-bottom: 3px;
  color: var(--text);
  font-size: 12px;
}

.privacy-card p {
  font-size: 11px;
}

.main-wrap {
  min-width: 0;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 68px;
  padding: 0 32px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--topbar);
  backdrop-filter: blur(12px);
}

.topbar-title {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
  font-size: 13px;
}

.section-name {
  font-weight: 700;
  color: var(--text);
}

.divider {
  color: var(--line);
}

.topbar .el-tag {
  font-weight: 600;
}

.main-content {
  padding: 0;
  overflow: visible;
}

@media (max-width: 800px) {
  .sidebar {
    width: 76px !important;
    padding: 18px 10px;
  }

  .brand {
    justify-content: center;
    padding: 0 0 18px;
  }

  .brand-text,
  .nav-menu span,
  .privacy-card {
    display: none;
  }

  .nav-menu :deep(.el-menu-item) {
    justify-content: center;
    padding: 0 !important;
  }

  .topbar {
    padding: 0 16px;
  }

  .topbar-title .muted,
  .divider {
    display: none;
  }
}
</style>
