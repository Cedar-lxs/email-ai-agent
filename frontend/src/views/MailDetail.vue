<template>
  <div class="mail-detail-container" v-loading="loading">
    <div class="page-header">
      <el-button @click="goBack" text>
        <el-icon><ArrowLeft /></el-icon>
        返回列表
      </el-button>
    </div>
    
    <div v-if="mail" class="detail-content">
      <el-card class="info-card">
        <div class="mail-header">
          <div>
            <h1>{{ mail.subject || '（无主题）' }}</h1>
            <p class="mail-meta">
              <span>发件人: {{ mail.sender }}</span>
              <span style="margin: 0 12px">|</span>
              <span>{{ formatTime(mail.received_at) }}</span>
            </p>
          </div>
          <el-tag :type="getStatusType(mail.status)" size="large">
            {{ getStatusLabel(mail.status) }}
            <span v-if="mail.intent"> · {{ mail.intent }}</span>
          </el-tag>
        </div>
      </el-card>
      
      <!-- 并排对比视图 -->
      <el-row :gutter="20" class="comparison-row">
        <el-col :xs="24" :lg="12">
          <el-card class="comparison-card">
            <template #header>
              <div class="card-header">
                <span class="section-index">01</span>
                <h3>客户原始邮件</h3>
                <small>原文只读</small>
              </div>
            </template>
            <div class="mail-body readonly">{{ mail.original_body || '（无正文）' }}</div>
          </el-card>
        </el-col>
        
        <el-col :xs="24" :lg="12">
          <el-card class="comparison-card">
            <template #header>
              <div class="card-header">
                <span class="section-index">02</span>
                <h3>回复草稿</h3>
                <small>{{ replySubject }}</small>
              </div>
            </template>
            
            <el-input
              v-if="mail.status === 'draft_ready'"
              v-model="draftBody"
              type="textarea"
              :rows="20"
              placeholder="编辑回复内容..."
              class="draft-editor"
            />
            <div v-else class="mail-body readonly">{{ draftBody || '（无回复内容）' }}</div>
            
            <div v-if="mail.status === 'draft_ready'" class="editor-actions">
              <el-button @click="handleSave" :loading="saving">
                <el-icon><DocumentChecked /></el-icon>
                保存修改
              </el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>
      
      <!-- 知识依据和操作 -->
      <el-row :gutter="20" class="action-row">
        <el-col :xs="24" :lg="16">
          <el-card class="evidence-card">
            <template #header>
              <div class="card-header">
                <span class="section-index">03</span>
                <h3>知识依据</h3>
                <small>仅使用置信度 ≥ 75% 的内容</small>
              </div>
            </template>
            
            <el-alert
              v-if="retrieval.degraded_reason"
              :title="retrieval.degraded_reason"
              type="warning"
              :closable="false"
              style="margin-bottom: 16px"
            />
            
            <div v-if="retrieval.hits && retrieval.hits.length > 0" class="evidence-list">
              <div v-for="(hit, index) in retrieval.hits" :key="index" class="evidence-item">
                <div class="evidence-header">
                  <div>
                    <strong>{{ hit.source }}</strong>
                    <small>{{ hit.section }}</small>
                  </div>
                  <el-tag type="success">{{ Math.round((hit.score || 0) * 100) }}%</el-tag>
                </div>
                <el-progress
                  :percentage="Math.round((hit.score || 0) * 100)"
                  :show-text="false"
                  :stroke-width="6"
                />
              </div>
            </div>
            
            <el-empty v-else description="暂无合格知识依据" />
          </el-card>
        </el-col>
        
        <el-col :xs="24" :lg="8" v-if="mail.status === 'draft_ready'">
          <el-card class="action-card">
            <template #header>
              <div class="card-header">
                <span class="section-index">04</span>
                <h3>审核操作</h3>
              </div>
            </template>
            
            <el-space direction="vertical" :size="12" style="width: 100%">
              <el-button
                type="primary"
                size="large"
                :loading="approving"
                @click="handleApprove"
                style="width: 100%"
              >
                <el-icon><Select /></el-icon>
                确认并发送
              </el-button>
              
              <el-divider />
              
              <el-input
                v-model="rejectReason"
                placeholder="填写拒绝原因"
                type="textarea"
                :rows="3"
              />
              
              <el-button
                type="danger"
                :loading="rejecting"
                :disabled="!rejectReason.trim()"
                @click="handleReject"
                style="width: 100%"
              >
                <el-icon><Close /></el-icon>
                拒绝
              </el-button>
              
              <el-divider />
              
              <el-popconfirm
                title="确认永久删除此邮件及草稿？"
                @confirm="handleDelete"
              >
                <template #reference>
                  <el-button type="danger" text style="width: 100%">
                    <el-icon><Delete /></el-icon>
                    删除邮件
                  </el-button>
                </template>
              </el-popconfirm>
            </el-space>
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  DocumentChecked,
  Select,
  Close,
  Delete
} from '@element-plus/icons-vue'
import { mailApi } from '@/api/mail'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const saving = ref(false)
const approving = ref(false)
const rejecting = ref(false)

const mail = ref(null)
const draftBody = ref('')
const rejectReason = ref('')
const retrieval = ref({})

const replySubject = computed(() => {
  if (!mail.value) return ''
  const subject = mail.value.subject || ''
  return subject.startsWith('Re:') ? subject : `Re: ${subject}`
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

const formatTime = (time) => {
  if (!time) return '—'
  const parsed = new Date(String(time).replace(' ', 'T'))
  if (Number.isNaN(parsed.getTime())) return time
  return parsed.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

const loadDetail = async () => {
  loading.value = true
  try {
    const messageId = String(route.params.id || '')
    if (!messageId) {
      ElMessage.error('邮件标识无效')
      router.replace('/mails')
      return
    }
    const data = await mailApi.getDetail(messageId)
    mail.value = data.mail
    draftBody.value = data.draft_body || ''
    retrieval.value = data.retrieval || {}
  } catch (error) {
    console.error('加载邮件详情失败:', error)
    if (error?.response?.status === 404) router.replace('/mails')
  } finally {
    loading.value = false
  }
}

const handleSave = async () => {
  saving.value = true
  try {
    await mailApi.saveDraft(mail.value.message_id, draftBody.value)
    ElMessage.success('草稿已保存')
  } catch (error) {
    console.error('保存失败:', error)
  } finally {
    saving.value = false
  }
}

const handleApprove = async () => {
  try {
    await ElMessageBox.confirm(
      `确认发送给 ${mail.value.sender}？`,
      '确认操作',
      {
        confirmButtonText: '确认发送',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    
    approving.value = true
    await mailApi.approve(mail.value.message_id)
    ElMessage.success('邮件已成功发送')
    router.push('/mails')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      console.error('发送失败:', error)
    }
  } finally {
    approving.value = false
  }
}

const handleReject = async () => {
  if (!rejectReason.value.trim()) {
    ElMessage.warning('请填写拒绝原因')
    return
  }
  
  rejecting.value = true
  try {
    await mailApi.reject(mail.value.message_id, rejectReason.value)
    ElMessage.success('草稿已拒绝')
    router.push('/mails')
  } catch (error) {
    console.error('拒绝失败:', error)
  } finally {
    rejecting.value = false
  }
}

const handleDelete = async () => {
  try {
    await mailApi.delete([mail.value.message_id])
    ElMessage.success('已删除')
    router.push('/mails')
  } catch (error) {
    console.error('删除失败:', error)
  }
}

const goBack = () => {
  router.back()
}

onMounted(() => {
  loadDetail()
})
</script>

<style scoped>
.mail-detail-container {
  max-width: 1600px;
}

.page-header {
  margin-bottom: 20px;
}

.info-card {
  margin-bottom: 20px;
}

.mail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
}

.mail-header h1 {
  margin: 0 0 8px;
  font-size: 24px;
  color: #182230;
}

.mail-meta {
  margin: 0;
  color: #65758b;
  font-size: 14px;
}

.comparison-row {
  margin-bottom: 20px;
}

.comparison-card {
  height: 100%;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.section-index {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: #edf3ff;
  color: #2563eb;
  font-size: 12px;
  font-weight: 800;
  flex-shrink: 0;
}

.card-header h3 {
  margin: 0;
  font-size: 16px;
  flex: 1;
}

.card-header small {
  color: #65758b;
  font-size: 12px;
}

.mail-body {
  min-height: 450px;
  max-height: 600px;
  overflow-y: auto;
  padding: 16px;
  background: #fafbfd;
  border: 1px solid #edf0f4;
  border-radius: 8px;
  white-space: pre-wrap;
  word-wrap: break-word;
  line-height: 1.6;
}

.mail-body.readonly {
  background: #f8fafc;
}

.draft-editor :deep(textarea) {
  min-height: 450px !important;
  max-height: 600px !important;
  font-family: inherit;
  line-height: 1.6;
}

.editor-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.evidence-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.evidence-item {
  padding: 16px;
  background: #fafbfd;
  border: 1px solid #edf0f4;
  border-radius: 8px;
}

.evidence-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.evidence-header strong {
  display: block;
  color: #182230;
  margin-bottom: 4px;
}

.evidence-header small {
  color: #65758b;
  font-size: 12px;
}

.action-card {
  position: sticky;
  top: 20px;
}
@media (max-width: 760px) {
  .mail-header { align-items: flex-start; flex-direction: column; }
  .mail-meta span { display: block; margin: 4px 0 !important; overflow-wrap: anywhere; }
  .comparison-row .el-col, .action-row .el-col { margin-bottom: 16px; }
  .mail-body, .draft-editor :deep(textarea) { min-height: 280px !important; }
  .card-header { flex-wrap: wrap; }
  .card-header small { width: 100%; margin-left: 40px; overflow-wrap: anywhere; }
  .action-card { position: static; }
}
</style>
