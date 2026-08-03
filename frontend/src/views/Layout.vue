<template>
  <el-container class="layout-container">
    <el-aside width="260px" class="sidebar">
      <div class="brand">
        <div class="logo">EA</div>
        <div>
          <strong>Email AI Agent</strong>
          <small>Technical Support</small>
        </div>
      </div>
      
      <el-menu
        :default-active="activeMenu"
        router
        class="sidebar-menu"
      >
        <el-menu-item index="/mails">
          <el-icon><Message /></el-icon>
          <span>邮件列表</span>
          <el-badge v-if="counts.draft_ready" :value="counts.draft_ready" class="menu-badge" />
        </el-menu-item>
        
        <el-menu-item index="/knowledge">
          <el-icon><Document /></el-icon>
          <span>知识库</span>
        </el-menu-item>
        
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
      </el-menu>
      
      <div class="sidebar-footer">
        <div class="mode-info">
          <h6>运行模式</h6>
          <div class="mode-tag">
            <el-tag :type="mode === 'full_auto' ? 'success' : 'warning'">
              {{ mode === 'full_auto' ? '全自动' : '半自动' }}
            </el-tag>
          </div>
        </div>
        
        <div class="user-info">
          <el-icon><User /></el-icon>
          <span>{{ authStore.username }}</span>
          <el-button link @click="handleLogout">
            <el-icon><SwitchButton /></el-icon>
          </el-button>
        </div>
      </div>
    </el-aside>
    
    <el-main class="main-content">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Message, Document, Setting, User, SwitchButton } from '@element-plus/icons-vue'
import { useAuthStore } from '@/store/auth'
import { mailApi } from '@/api/mail'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const counts = ref({
  draft_ready: 0,
  escalated: 0,
  replied: 0
})

const mode = ref('semi_auto')

const activeMenu = computed(() => {
  if (route.path.startsWith('/mails')) return '/mails'
  if (route.path.startsWith('/knowledge')) return '/knowledge'
  return route.path
})

const loadStats = async () => {
  try {
    const data = await mailApi.getStats()
    counts.value = data.counts || {}
    mode.value = data.mode || 'semi_auto'
  } catch (error) {
    console.error('加载统计信息失败:', error)
  }
}

const handleLogout = async () => {
  try {
    await ElMessageBox.confirm('确认退出登录？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    authStore.logout()
    router.push('/login')
  } catch (error) {
    // 用户取消
  }
}

const handleModeChange = event => { mode.value = event.detail || 'semi_auto' }
let statsTimer

onMounted(() => {
  loadStats()
  // 每30秒刷新一次统计
  window.addEventListener('workflow-mode-changed', handleModeChange)
  statsTimer = setInterval(loadStats, 30000)
})
onBeforeUnmount(() => {
  clearInterval(statsTimer)
  window.removeEventListener('workflow-mode-changed', handleModeChange)
})
</script>

<style scoped>
.layout-container {
  min-height: 100vh;
}

.sidebar {
  background: linear-gradient(#111c2d, #0d1726);
  color: white;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}

.brand {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 26px 18px 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.logo {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 13px;
  background: linear-gradient(145deg, #3b82f6, #1d4ed8);
  box-shadow: 0 8px 20px rgba(11, 63, 155, 0.4);
  font-weight: 800;
  font-size: 18px;
  flex-shrink: 0;
}

.brand strong {
  display: block;
  font-size: 14px;
  margin-bottom: 2px;
}

.brand small {
  display: block;
  color: #8fa0b6;
  font-size: 12px;
}

.sidebar-menu {
  flex: 1;
  border: none;
  background: transparent;
  margin-top: 22px;
  padding: 0 18px;
}

.sidebar-menu .el-menu-item {
  color: #aebdce;
  border-radius: 10px;
  margin-bottom: 5px;
  position: relative;
}

.sidebar-menu .el-menu-item:hover,
.sidebar-menu .el-menu-item.is-active {
  background: linear-gradient(90deg, #2563eb, #2f6feb);
  color: white;
}

.menu-badge {
  position: absolute;
  right: 12px;
}

.sidebar-footer {
  padding: 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.mode-info h6 {
  margin: 0 0 8px;
  color: #718299;
  font-size: 11px;
  letter-spacing: 0.12em;
}

.mode-tag {
  margin-bottom: 16px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #72849a;
  font-size: 12px;
}

.user-info span {
  flex: 1;
}

.main-content {
  min-width: 0;
  background: #f5f7fb;
  padding: 34px 40px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
@media (max-width: 760px) {
  .layout-container { display: block; }
  .sidebar { width: 100% !important; height: auto; position: static; overflow: visible; }
  .brand { padding: 16px 18px; }
  .sidebar-menu { display: flex; margin: 0; padding: 10px 12px; overflow-x: auto; }
  .sidebar-menu .el-menu-item { flex: 1; min-width: 100px; justify-content: center; margin: 0 4px; }
  .sidebar-footer { display: none; }
  .main-content { padding: 20px 14px; }
  .menu-badge { right: 4px; }
}
</style>
