<template>
  <div class="operations-page">
    <header class="page-header">
      <div>
        <span class="eyebrow">OPERATIONS</span>
        <h1>异常处理中心</h1>
      </div>
      <el-button :icon="RefreshRight" :loading="loading" @click="refreshAll">
        刷新
      </el-button>
    </header>

    <div class="status-switch" role="tablist" aria-label="异常任务状态">
      <button
        v-for="item in statusOptions"
        :key="item.value"
        type="button"
        :class="['status-option', { active: currentStatus === item.value }]"
        @click="changeStatus(item.value)"
      >
        <span>{{ item.label }}</span>
        <strong>{{ operationCounts[item.value] || 0 }}</strong>
      </button>
    </div>

    <el-alert
      v-if="loadError"
      class="load-error"
      :title="loadError"
      type="error"
      show-icon
      :closable="false"
    />

    <section class="queue-panel" aria-label="异常邮件任务">
      <div class="queue-summary">
        <span>共 {{ total }} 项</span>
        <small v-if="operationCounts.oldest_attention_at">
          最早等待：{{ formatTime(operationCounts.oldest_attention_at) }}
        </small>
      </div>
      <div class="table-scroll">
        <el-table
          v-loading="loading"
          :data="jobs"
          row-key="id"
          class="operations-table"
          empty-text="当前状态下没有待处理任务"
          @row-click="openDetail"
        >
          <el-table-column label="客户邮件" min-width="240">
            <template #default="{ row }">
              <div class="mail-identity">
                <strong>{{ row.subject || '（主题尚未写入）' }}</strong>
                <span>{{ row.sender || row.message_id }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="116">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" effect="light">
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="attempt_count" label="尝试" width="64" align="center" />
          <el-table-column label="最近错误" min-width="190">
            <template #default="{ row }">
              <el-tooltip
                v-if="row.last_error"
                :content="row.last_error"
                placement="top"
                :show-after="350"
              >
                <span class="error-text">{{ row.last_error }}</span>
              </el-tooltip>
              <span v-else class="muted">无错误详情</span>
            </template>
          </el-table-column>
          <el-table-column label="等待时间" width="96">
            <template #default="{ row }">{{ waitingTime(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="最后更新" width="144">
            <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="206" fixed="right">
            <template #default="{ row }">
              <div class="row-actions" @click.stop>
                <el-button
                  v-if="row.status === 'dead_letter'"
                  link
                  type="primary"
                  :icon="RefreshRight"
                  :disabled="pending"
                  @click="requestAction('retry', row)"
                >重新分析</el-button>
                <el-button
                  v-if="row.status !== 'awaiting_confirmation'"
                  link
                  :icon="UserFilled"
                  :disabled="pending"
                  @click="requestAction('escalate', row)"
                >转人工</el-button>
                <el-button
                  v-if="row.status === 'awaiting_confirmation'"
                  link
                  type="warning"
                  :icon="Warning"
                  @click="openDetail(row)"
                >处置发送</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <footer class="pagination-row">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="loadJobs"
        />
      </footer>
    </section>

    <el-drawer
      v-model="drawerVisible"
      title="任务详情"
      size="min(720px, 100%)"
      destroy-on-close
    >
      <div v-loading="detailLoading" class="detail-content">
        <template v-if="detail">
          <section class="detail-section">
            <h2>收件标识</h2>
            <dl class="identity-grid">
              <div><dt>主题</dt><dd>{{ detail.job.subject || '（无主题）' }}</dd></div>
              <div><dt>发件人</dt><dd>{{ detail.job.sender || '未知' }}</dd></div>
              <div><dt>Message-ID</dt><dd>{{ detail.job.message_id }}</dd></div>
              <div><dt>IMAP UID</dt><dd>{{ detail.job.uid_validity }} / {{ detail.job.imap_uid }}</dd></div>
            </dl>
          </section>

          <section v-if="activeDelivery" class="detail-section">
            <h2>发送标识</h2>
            <dl class="identity-grid">
              <div><dt>投递状态</dt><dd>{{ deliveryStatusLabel(activeDelivery.status) }}</dd></div>
              <div><dt>收件地址</dt><dd>{{ activeDelivery.recipient }}</dd></div>
              <div class="wide"><dt>Outgoing Message-ID</dt><dd>{{ activeDelivery.email_message_id }}</dd></div>
              <div class="wide"><dt>SMTP 结果</dt><dd>{{ activeDelivery.smtp_response || '无返回详情' }}</dd></div>
            </dl>
          </section>

          <section class="detail-section timeline-section">
            <h2>处理记录</h2>
            <el-timeline>
              <el-timeline-item
                v-for="event in detail.events"
                :key="event.id"
                :timestamp="formatTime(event.created_at)"
                placement="top"
              >
                <div class="timeline-entry">
                  <strong>{{ eventLabel(event.event_type) }}</strong>
                  <span v-if="event.from_status || event.to_status">
                    {{ event.from_status || '开始' }} → {{ event.to_status || '完成' }}
                  </span>
                  <p v-if="event.reason">{{ event.reason }}</p>
                  <small v-if="event.actor">操作人：{{ event.actor }}</small>
                </div>
              </el-timeline-item>
            </el-timeline>
          </section>

          <div class="drawer-actions">
            <el-button
              v-if="detail.job.status === 'dead_letter'"
              type="primary"
              :icon="RefreshRight"
              :disabled="pending"
              @click="requestAction('retry', detail.job)"
            >重新分析</el-button>
            <el-button
              v-if="activeDelivery && activeDelivery.status === 'uncertain'"
              type="success"
              :icon="CircleCheck"
              :disabled="pending"
              @click="requestAction('confirm', detail.job, activeDelivery)"
            >确认已发送</el-button>
            <el-button
              v-if="activeDelivery && activeDelivery.status === 'uncertain'"
              type="warning"
              :icon="Promotion"
              :disabled="pending"
              @click="requestAction('resend', detail.job, activeDelivery)"
            >确认未发送并重发</el-button>
            <el-button
              v-if="['retry_wait', 'dead_letter', 'awaiting_confirmation'].includes(detail.job.status)"
              :icon="UserFilled"
              :disabled="pending"
              @click="requestAction('escalate', detail.job)"
            >转人工</el-button>
          </div>
        </template>
      </div>
    </el-drawer>

    <el-dialog
      v-model="reasonVisible"
      :title="actionConfig.title"
      width="min(480px, calc(100% - 28px))"
      :close-on-click-modal="!pending"
      :close-on-press-escape="!pending"
    >
      <el-alert
        v-if="pendingAction.type === 'resend'"
        title="此操作会创建一封新的外发邮件，请先确认原邮件未发送。"
        type="warning"
        show-icon
        :closable="false"
      />
      <label class="reason-label" for="operation-reason">操作原因</label>
      <el-input
        id="operation-reason"
        v-model="reason"
        type="textarea"
        :rows="4"
        maxlength="500"
        show-word-limit
        placeholder="请填写核对结果或处置依据（至少 3 个字符）"
        :disabled="pending"
      />
      <template #footer>
        <el-button :disabled="pending" @click="reasonVisible = false">取消</el-button>
        <el-button
          :type="actionConfig.buttonType"
          :icon="actionConfig.icon"
          :loading="pending"
          :disabled="reason.trim().length < 3"
          @click="submitAction"
        >{{ actionConfig.confirmText }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  CircleCheck,
  Promotion,
  RefreshRight,
  UserFilled,
  Warning
} from '@element-plus/icons-vue'
import { mailApi } from '@/api/mail'
import { operationsApi } from '@/api/operations'

const statusOptions = [
  { value: 'retry_wait', label: '待重试' },
  { value: 'dead_letter', label: '死信' },
  { value: 'awaiting_confirmation', label: '待确认发送' }
]

const statusLabels = {
  retry_wait: '待重试',
  dead_letter: '死信',
  awaiting_confirmation: '待确认发送',
  pending: '待处理',
  escalated: '已转人工',
  sent: '已发送'
}

const eventLabels = {
  discovered: '发现邮件',
  claimed: '开始处理',
  retry_scheduled: '安排重试',
  dead_lettered: '进入死信',
  dead_letter_requeued: '重新进入队列',
  operator_escalated: '人工转交',
  delivery_prepared: '准备发送',
  delivery_started: '开始 SMTP 发送',
  delivery_accepted: '确认发送成功',
  delivery_uncertain: '发送结果待确认',
  delivery_resend_authorized: '授权重新发送',
  imap_seen_synced: '同步已读状态'
}

const currentStatus = ref('dead_letter')
const jobs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const loadError = ref('')
const operationCounts = ref({
  retry_wait: 0,
  dead_letter: 0,
  awaiting_confirmation: 0,
  total_attention: 0,
  oldest_attention_at: ''
})
const drawerVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const reasonVisible = ref(false)
const reason = ref('')
const pending = ref(false)
const pendingAction = ref({ type: '', job: null, delivery: null })

const activeDelivery = computed(() => {
  const deliveries = detail.value?.deliveries || []
  return [...deliveries].reverse().find(item =>
    ['prepared', 'sending', 'uncertain'].includes(item.status)
  ) || deliveries[deliveries.length - 1] || null
})

const actionConfigs = {
  retry: { title: '重新分析邮件', confirmText: '重新进入队列', buttonType: 'primary', icon: RefreshRight },
  escalate: { title: '转交人工处理', confirmText: '确认转人工', buttonType: 'primary', icon: UserFilled },
  confirm: { title: '确认邮件已发送', confirmText: '确认已发送', buttonType: 'success', icon: CircleCheck },
  resend: { title: '确认未发送并重发', confirmText: '创建新邮件并发送', buttonType: 'warning', icon: Promotion }
}

const actionConfig = computed(() => actionConfigs[pendingAction.value.type] || actionConfigs.escalate)
const statusLabel = status => statusLabels[status] || status
const eventLabel = event => eventLabels[event] || event
const statusType = status => ({
  retry_wait: 'warning',
  dead_letter: 'danger',
  awaiting_confirmation: 'warning'
}[status] || 'info')
const deliveryStatusLabel = status => ({
  uncertain: '结果待确认',
  accepted: '已接受',
  failed_safe: '发送前失败',
  cancelled: '已取消',
  sending: '发送中',
  prepared: '待发送'
}[status] || status)

const parseTime = value => new Date(String(value || '').replace(' ', 'T'))
const formatTime = value => {
  if (!value) return '—'
  const parsed = parseTime(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit'
  })
}
const waitingTime = value => {
  const parsed = parseTime(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  const minutes = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 60000))
  if (minutes < 60) return `${minutes} 分钟`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时`
  return `${Math.floor(minutes / 1440)} 天`
}

const loadJobs = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const data = await operationsApi.list({
      status: currentStatus.value,
      page: page.value,
      page_size: pageSize
    })
    jobs.value = data.jobs || []
    total.value = data.total || 0
  } catch (error) {
    loadError.value = error.response?.data?.error || '异常任务加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

const loadStats = async () => {
  try {
    const data = await mailApi.getStats()
    operationCounts.value = data.operations || operationCounts.value
  } catch (error) {
    console.error('加载异常任务统计失败:', error)
  }
}

const refreshAll = async () => Promise.all([loadJobs(), loadStats()])
const changeStatus = status => {
  currentStatus.value = status
  page.value = 1
  loadJobs()
}

const openDetail = async row => {
  drawerVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await operationsApi.detail(row.id)
  } catch (error) {
    drawerVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

const requestAction = (type, job, delivery = null) => {
  pendingAction.value = { type, job, delivery }
  reason.value = ''
  reasonVisible.value = true
}

const submitAction = async () => {
  const cleanReason = reason.value.trim()
  if (cleanReason.length < 3) return
  const { type, job, delivery } = pendingAction.value
  pending.value = true
  try {
    if (type === 'retry') await operationsApi.retry(job.id, cleanReason)
    if (type === 'escalate') await operationsApi.escalate(job.id, cleanReason)
    if (type === 'confirm') await operationsApi.confirmSent(delivery.id, cleanReason)
    if (type === 'resend') await operationsApi.authorizeResend(delivery.id, cleanReason)
    ElMessage.success(actionConfig.value.confirmText + '成功')
    reasonVisible.value = false
    drawerVisible.value = false
    await refreshAll()
    window.dispatchEvent(new CustomEvent('operations-updated'))
  } finally {
    pending.value = false
  }
}

onMounted(refreshAll)
</script>

<style scoped>
.operations-page { max-width: 1600px; }
.page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; margin-bottom: 22px; }
.eyebrow { display: block; margin-bottom: 6px; color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .14em; }
.page-header h1 { margin: 0; color: #182230; font-size: 28px; line-height: 1.25; }
.status-switch { display: inline-flex; gap: 2px; padding: 3px; margin-bottom: 16px; border: 1px solid #dfe5ed; border-radius: 8px; background: #eef2f7; }
.status-option { min-width: 132px; height: 38px; padding: 0 12px; border: 0; border-radius: 6px; background: transparent; color: #59687c; cursor: pointer; }
.status-option span { margin-right: 8px; }
.status-option strong { display: inline-grid; min-width: 24px; height: 22px; padding: 0 6px; place-items: center; border-radius: 11px; background: #dce3ed; font-size: 12px; }
.status-option.active { background: #fff; color: #1f5bc4; box-shadow: 0 1px 4px rgba(25, 38, 58, .12); }
.status-option.active strong { background: #e5efff; color: #1f5bc4; }
.load-error { margin-bottom: 14px; }
.queue-panel { overflow: hidden; border: 1px solid #dfe5ed; border-radius: 8px; background: #fff; }
.queue-summary { display: flex; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid #e7ebf1; color: #536278; }
.queue-summary small { color: #7b899c; }
.table-scroll { width: 100%; overflow-x: auto; }
.operations-table { min-width: 1020px; cursor: pointer; }
.mail-identity { min-width: 0; }
.mail-identity strong, .mail-identity span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mail-identity strong { color: #182230; }
.mail-identity span { margin-top: 4px; color: #738197; font-size: 12px; }
.error-text { display: block; overflow: hidden; color: #9f3847; text-overflow: ellipsis; white-space: nowrap; }
.muted { color: #8a98aa; }
.row-actions { display: flex; align-items: center; white-space: nowrap; }
.pagination-row { display: flex; justify-content: flex-end; padding: 14px 16px; border-top: 1px solid #e7ebf1; }
.detail-content { min-height: 240px; }
.detail-section { padding: 0 0 22px; margin-bottom: 22px; border-bottom: 1px solid #e7ebf1; }
.detail-section h2 { margin: 0 0 14px; color: #26364a; font-size: 16px; }
.identity-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 20px; margin: 0; }
.identity-grid div { min-width: 0; }
.identity-grid .wide { grid-column: 1 / -1; }
.identity-grid dt { margin-bottom: 4px; color: #7b899c; font-size: 12px; }
.identity-grid dd { margin: 0; color: #253348; overflow-wrap: anywhere; }
.timeline-section { border-bottom: 0; }
.timeline-entry strong, .timeline-entry span, .timeline-entry small { display: block; }
.timeline-entry span { margin-top: 3px; color: #66758a; font-size: 12px; overflow-wrap: anywhere; }
.timeline-entry p { margin: 7px 0 0; color: #8d3e49; overflow-wrap: anywhere; }
.timeline-entry small { margin-top: 5px; color: #7b899c; }
.drawer-actions { position: sticky; bottom: 0; display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 0 4px; background: #fff; border-top: 1px solid #e7ebf1; }
.reason-label { display: block; margin: 18px 0 8px; color: #344257; font-weight: 650; }
@media (max-width: 760px) {
  .page-header { align-items: flex-start; flex-direction: column; }
  .page-header h1 { font-size: 24px; }
  .status-switch { display: grid; grid-template-columns: repeat(3, minmax(112px, 1fr)); width: 100%; overflow-x: auto; }
  .status-option { min-width: 112px; }
  .queue-summary { align-items: flex-start; flex-direction: column; gap: 2px; }
  .pagination-row { justify-content: center; overflow-x: auto; }
  .identity-grid { grid-template-columns: 1fr; }
  .identity-grid .wide { grid-column: auto; }
  .drawer-actions { align-items: stretch; flex-direction: column; }
  .drawer-actions .el-button { width: 100%; margin-left: 0; }
}
</style>
