<template>
  <div class="mail-list-container">
    <div class="page-header">
      <div>
        <span class="eyebrow">MAIL REVIEW</span>
        <h1>邮件列表</h1>
      </div>
      <el-button type="primary" @click="handleRefresh" :loading="loading">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>
    
    <el-card class="stats-card">
      <el-row :gutter="20">
        <el-col :span="6">
          <div class="stat-item">
            <span>待审核</span>
            <strong>{{ stats.draft_ready || 0 }}</strong>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="stat-item">
            <span>已发送</span>
            <strong>{{ stats.replied || 0 }}</strong>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="stat-item">
            <span>转人工</span>
            <strong>{{ stats.escalated || 0 }}</strong>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="stat-item">
            <span>总计</span>
            <strong>{{ stats.total || 0 }}</strong>
          </div>
        </el-col>
      </el-row>
    </el-card>
    
    <el-card class="mail-card">
      <div class="toolbar">
        <el-input
          v-model="searchQuery"
          placeholder="搜索邮件主题、发件人..."
          :prefix-icon="Search"
          clearable
          @keyup.enter="handleSearch"
          @clear="handleSearch"
          style="width: 300px"
        />
        
        <el-select v-model="currentStatus" @change="handleStatusChange" style="width: 150px">
          <el-option label="待审核" value="draft_ready" />
          <el-option label="已发送" value="replied" />
          <el-option label="转人工" value="escalated" />
          <el-option label="已拒绝" value="rejected" />
          <el-option label="全部" value="all" />
        </el-select>
      </div>
      
      <el-table
        v-loading="loading"
        :data="mails"
        stripe
        @row-click="handleRowClick"
        style="cursor: pointer"
      >
        <el-table-column type="index" width="50" />
        
        <el-table-column label="发件人" width="250">
          <template #default="{ row }">
            <div class="sender-info">
              <el-avatar :size="36">{{ senderInitial(row.sender) }}</el-avatar>
              <div>
                <div class="sender-name">{{ senderName(row.sender) }}</div>
                <div class="sender-email">{{ row.sender || '未知发件人' }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column label="主题" min-width="300">
          <template #default="{ row }">
            <div class="mail-subject">
              <strong>{{ row.subject || '（无主题）' }}</strong>
              <el-tag v-if="row.intent" size="small" type="info" style="margin-left: 8px">
                {{ row.intent }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        
        <el-table-column label="接收时间" width="160">
          <template #default="{ row }">
            {{ formatTime(row.received_at) }}
          </template>
        </el-table-column>
        
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="handleView(row)">
              查看
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      
      <div class="pagination">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Refresh } from '@element-plus/icons-vue'
import { mailApi } from '@/api/mail'

const router = useRouter()

const loading = ref(false)
const mails = ref([])
const searchQuery = ref('')
const currentStatus = ref('draft_ready')
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

const stats = ref({
  draft_ready: 0,
  replied: 0,
  escalated: 0,
  rejected: 0,
  total: 0
})

const statusLabels = {
  draft_ready: '待审核',
  replied: '已发送',
  escalated: '转人工',
  rejected: '已拒绝',
  failed: '失败',
  pending: '处理中',
  skipped_self: '已跳过'
}

const getStatusLabel = (status) => statusLabels[status] || status

const getStatusType = (status) => {
  const typeMap = {
    draft_ready: 'warning',
    replied: 'success',
    escalated: 'info',
    rejected: 'danger',
    failed: 'danger',
    pending: '',
    skipped_self: 'info'
  }
  return typeMap[status] || ''
}

const senderName = sender => String(sender || '未知发件人').split('@')[0]
const senderInitial = sender => senderName(sender).charAt(0).toUpperCase() || '?'

const formatTime = (time) => {
  if (!time) return '—'
  const parsed = new Date(String(time).replace(' ', 'T'))
  if (Number.isNaN(parsed.getTime())) return time
  return parsed.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}


let requestSequence = 0

const loadMails = async () => {
  const sequence = ++requestSequence
  loading.value = true
  try {
    const data = await mailApi.getList({
      status: currentStatus.value,
      q: searchQuery.value,
      page: currentPage.value,
      page_size: pageSize.value
    })
    if (sequence !== requestSequence) return
    mails.value = data.mails || []
    total.value = data.total || 0
  } catch (error) {
    console.error('加载邮件列表失败:', error)
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

const loadStats = async () => {
  try {
    const data = await mailApi.getStats()
    stats.value = data.counts || {}
    stats.value.total = Object.values(data.counts || {}).reduce((a, b) => a + b, 0)
  } catch (error) {
    console.error('加载统计失败:', error)
  }
}

const handleSearch = () => {
  currentPage.value = 1
  loadMails()
}

const handleStatusChange = () => {
  currentPage.value = 1
  loadMails()
}

const handlePageChange = () => {
  loadMails()
}

const handleRefresh = () => {
  loadMails()
  loadStats()
}

const handleRowClick = (row) => {
  router.push(`/mails/${encodeURIComponent(row.message_id)}`)
}

const handleView = (row) => {
  router.push(`/mails/${encodeURIComponent(row.message_id)}`)
}

onMounted(() => {
  loadMails()
  loadStats()
})
</script>

<style scoped>
.mail-list-container {
  max-width: 1600px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.eyebrow {
  display: block;
  margin-bottom: 8px;
  color: #2563eb;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.14em;
}

.page-header h1 {
  margin: 0;
  font-size: 28px;
  color: #182230;
}

.stats-card {
  margin-bottom: 20px;
}

.stat-item {
  text-align: center;
  padding: 12px;
}

.stat-item span {
  display: block;
  color: #65758b;
  font-size: 14px;
  margin-bottom: 8px;
}

.stat-item strong {
  display: block;
  font-size: 32px;
  color: #182230;
}

.mail-card {
  box-shadow: 0 12px 36px rgba(20, 33, 55, 0.07);
}

.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.sender-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sender-name {
  font-weight: 600;
  color: #182230;
}

.sender-email {
  font-size: 12px;
  color: #65758b;
}

.mail-subject strong {
  color: #182230;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
@media (max-width: 700px) {
  .stats-card :deep(.el-row) { row-gap: 12px; }
  .stats-card :deep(.el-col) { max-width: 50%; flex: 0 0 50%; }
  .toolbar { flex-direction: column; }
  .toolbar :deep(.el-input), .toolbar :deep(.el-select) { width: 100% !important; }
  .pagination { justify-content: center; overflow-x: auto; }
  .sender-email { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}
</style>
