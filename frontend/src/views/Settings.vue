<template>
  <div class="settings-container" v-loading="loading">
    <div class="page-header">
      <div><span class="eyebrow">SETTINGS</span><h1>系统设置</h1><p>检查运行配置、验证上游连接并控制邮件发送策略。</p></div>
    </div>

    <el-alert title="密码与 API Key 不会通过此页面返回。服务器、模型等静态配置请在 config.yaml 或 .env 修改后重启服务。" type="info" show-icon :closable="false" />

    <div class="settings-grid">
      <el-card shadow="never" class="mode-card">
        <template #header><div class="section-title"><el-icon><Switch /></el-icon><div><h2>发送模式</h2><p>此项保存后立即生效，无需重启。</p></div></div></template>
        <el-radio-group v-model="settings.mode" class="mode-options" :disabled="savingMode" @change="changeMode">
          <el-radio-button value="semi_auto"><strong>半自动</strong><small>AI 生成草稿，人工批准后发送</small></el-radio-button>
          <el-radio-button value="full_auto"><strong>全自动</strong><small>仅低风险技术类型可自动回复</small></el-radio-button>
        </el-radio-group>
        <el-alert v-if="settings.mode === 'full_auto'" title="全自动模式已启用：业务、高风险、低置信度和信息不足的邮件仍会转人工。" type="warning" show-icon :closable="false" />
      </el-card>

      <el-card shadow="never">
        <template #header><div class="section-title"><el-icon><Connection /></el-icon><div><h2>连接检测</h2><p>测试当前进程正在使用的上游配置。</p></div></div></template>
        <div class="test-row"><div><strong>IMAP 收件连接</strong><span>{{ settings.mail?.imap_server }}:{{ settings.mail?.imap_port }}</span></div><el-button :loading="testingMail" @click="testMail">测试邮箱</el-button></div>
        <el-divider />
        <div class="test-row"><div><strong>AI 模型连接</strong><span>{{ settings.ai?.provider }} / {{ settings.ai?.model }}</span></div><el-button :loading="testingAi" @click="testAi">测试 AI</el-button></div>
      </el-card>

      <el-card shadow="never">
        <template #header><div class="section-title"><el-icon><Message /></el-icon><div><h2>邮箱配置</h2><p>仅显示非敏感连接信息。</p></div></div></template>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="邮箱账号">{{ settings.mail?.account || '—' }}</el-descriptions-item>
          <el-descriptions-item label="IMAP">{{ endpoint(settings.mail?.imap_server, settings.mail?.imap_port) }}</el-descriptions-item>
          <el-descriptions-item label="SMTP">{{ endpoint(settings.mail?.smtp_server, settings.mail?.smtp_port) }}</el-descriptions-item>
          <el-descriptions-item label="轮询间隔">{{ settings.mail?.poll_interval ? `${settings.mail.poll_interval} 秒` : '—' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never">
        <template #header><div class="section-title"><el-icon><Cpu /></el-icon><div><h2>AI 与检索</h2><p>当前模型及知识召回参数。</p></div></div></template>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="AI Provider">{{ settings.ai?.provider || '—' }}</el-descriptions-item>
          <el-descriptions-item label="模型">{{ settings.ai?.model || '—' }}</el-descriptions-item>
          <el-descriptions-item label="API 地址">{{ settings.ai?.api_base || '—' }}</el-descriptions-item>
          <el-descriptions-item label="检索模式">{{ settings.rag?.mode || '—' }}</el-descriptions-item>
          <el-descriptions-item label="Top K">{{ settings.rag?.top_k ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="置信度阈值">{{ settings.rag?.min_confidence ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="Embedding">{{ settings.rag?.embedding_model || '未配置' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
    </div>

    <el-card shadow="never" class="types-card">
      <template #header><div class="section-title"><el-icon><CircleCheck /></el-icon><div><h2>全自动允许类型</h2><p>仅在全自动模式下，且同时通过其他安全检查后才会直接发送。</p></div></div></template>
      <el-tag v-for="type in settings.auto_reply_types || []" :key="type" effect="plain">{{ type }}</el-tag>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, Connection, Cpu, Message, Switch } from '@element-plus/icons-vue'
import { settingsApi } from '@/api/settings'

const settings = ref({ mode: 'semi_auto', mail: {}, ai: {}, rag: {}, auto_reply_types: [] })
const loading = ref(false)
const savingMode = ref(false)
const testingMail = ref(false)
const testingAi = ref(false)

const loadSettings = async () => {
  loading.value = true
  try { settings.value = await settingsApi.get() } finally { loading.value = false }
}

const changeMode = async mode => {
  const previous = mode === 'full_auto' ? 'semi_auto' : 'full_auto'
  if (mode === 'full_auto') {
    try {
      await ElMessageBox.confirm(
        '全自动模式可能直接发送符合安全策略的技术回复。确认启用？',
        '启用全自动模式',
        { type: 'warning', confirmButtonText: '确认启用', cancelButtonText: '取消' }
      )
    } catch {
      settings.value.mode = previous
      return
    }
  }
  savingMode.value = true
  try {
    const data = await settingsApi.setMode(mode)
    settings.value.mode = data.mode
    window.dispatchEvent(new CustomEvent('workflow-mode-changed', { detail: data.mode }))
    ElMessage.success(data.message)
  } catch (error) { settings.value.mode = previous } finally { savingMode.value = false }
}

const testMail = async () => {
  testingMail.value = true
  try { ElMessage.success((await settingsApi.testMail()).message) } finally { testingMail.value = false }
}
const testAi = async () => {
  testingAi.value = true
  try { ElMessage.success((await settingsApi.testAi()).message) } finally { testingAi.value = false }
}
const endpoint = (host, port) => host ? `${host}${port ? `:${port}` : ''}` : '—'

onMounted(loadSettings)
</script>

<style scoped>
.settings-container { max-width: 1500px; margin: 0 auto; }
.page-header { margin-bottom: 24px; }
.eyebrow { display: block; margin-bottom: 8px; color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .14em; }
h1 { margin: 0; color: #182230; font-size: 28px; }
.page-header p { margin: 7px 0 0; color: #667085; font-size: 13px; }
.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-top: 20px; }
.settings-grid .el-card, .types-card { border: 1px solid #e5eaf2; }
.section-title { display: flex; align-items: center; gap: 12px; }
.section-title > .el-icon { width: 38px; height: 38px; border-radius: 10px; background: #eff6ff; color: #2563eb; font-size: 19px; }
.section-title h2 { margin: 0; color: #182230; font-size: 17px; }
.section-title p { margin: 4px 0 0; color: #667085; font-size: 12px; }
.mode-options { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; width: 100%; margin-bottom: 18px; }
.mode-options :deep(.el-radio-button__inner) { display: flex; flex-direction: column; align-items: flex-start; width: 100%; height: 82px; padding: 17px; border: 1px solid #d0d5dd !important; border-radius: 10px !important; box-shadow: none !important; text-align: left; }
.mode-options :deep(.is-active .el-radio-button__inner) { border-color: #2563eb !important; background: #eff6ff; color: #175cd3; }
.mode-options strong { font-size: 14px; }
.mode-options small { margin-top: 7px; color: #667085; white-space: normal; }
.test-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.test-row strong, .test-row span { display: block; }
.test-row strong { color: #344054; font-size: 14px; }
.test-row span { margin-top: 5px; color: #98a2b3; font-size: 12px; }
.types-card { margin-top: 20px; }
.types-card .el-tag { margin: 0 10px 10px 0; }
@media (max-width: 900px) { .settings-grid { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .mode-options { grid-template-columns: 1fr; } }
@media (max-width: 560px) {
  .mode-options { grid-template-columns: 1fr; }
  .test-row { align-items: flex-start; flex-direction: column; }
  .test-row .el-button { width: 100%; }
  .section-title { align-items: flex-start; }
  :deep(.el-descriptions__label) { width: 105px; }
  :deep(.el-descriptions__content) { overflow-wrap: anywhere; }
}
</style>
