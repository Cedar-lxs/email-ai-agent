<template>
  <div class="knowledge-container" v-loading="loading">
    <div class="page-header">
      <div>
        <span class="eyebrow">KNOWLEDGE BASE</span>
        <h1>知识库管理</h1>
        <p>按目录维护 AI 回复使用的本地技术资料与检索索引。</p>
      </div>
      <div class="header-actions">
        <el-button :icon="EditPen" @click="$router.push('/knowledge/new')">新建知识</el-button>
        <el-button :icon="Refresh" :loading="rebuilding" @click="rebuildIndex">重建索引</el-button>
        <el-button type="primary" :icon="Upload" :loading="uploading" @click="fileInput?.click()">上传文件</el-button>
      </div>
    </div>

    <div class="stat-grid">
      <el-card shadow="never"><span>知识文件</span><strong>{{ files.length }}</strong><small>{{ directories.length }} 个目录</small></el-card>
      <el-card shadow="never"><span>知识片段</span><strong>{{ index.entries || 0 }}</strong><small>当前可检索内容</small></el-card>
      <el-card shadow="never"><span>索引来源</span><strong>{{ index.sources ?? files.length }}</strong><small>已纳入索引的文件</small></el-card>
      <el-card shadow="never"><span>检索状态</span><strong class="status-text">{{ indexMode }}</strong><small>{{ index.errors?.length ? '存在解析错误' : '索引可用' }}</small></el-card>
    </div>

    <el-alert v-for="error in index.errors || []" :key="error" :title="error" type="error" show-icon :closable="false" />

    <el-card shadow="never" class="files-card">
      <template #header>
        <div class="card-header">
          <div><h2>知识文件</h2><p>按目录浏览资料；上传文件会保存到根目录，删除后自动重建索引。</p></div>
          <el-button type="danger" plain :disabled="!selected.length" :loading="deleting" @click="deleteSelected">删除所选<span v-if="selected.length">（{{ selected.length }}）</span></el-button>
        </div>
      </template>

      <input ref="fileInput" class="hidden-upload" type="file" multiple accept=".json,.txt,.md,.docx,.xlsx" @change="uploadFiles" />
      <div class="directory-filter">
        <span>目录</span>
        <el-select v-model="selectedDirectory" clearable placeholder="全部目录">
          <el-option v-for="directory in directories" :key="directory" :label="directoryLabel(directory)" :value="directory" />
        </el-select>
        <small>当前显示 {{ visibleFiles.length }} / {{ files.length }} 个文件</small>
      </div>

      <el-table :data="visibleFiles" row-key="name" empty-text="暂无知识文件，请先上传资料" @selection-change="selected = $event">
        <el-table-column type="selection" width="52" />
        <el-table-column label="目录" min-width="180">
          <template #default="{ row }"><div class="directory-name"><el-icon><FolderOpened /></el-icon><span>{{ directoryLabel(row.directory) }}</span></div></template>
        </el-table-column>
        <el-table-column label="文件名" min-width="300">
          <template #default="{ row }"><div class="file-name"><el-icon><Document /></el-icon><span>{{ row.filename || row.name }}</span><small>{{ row.name }}</small></div></template>
        </el-table-column>
        <el-table-column prop="suffix" label="格式" width="100"><template #default="{ row }"><el-tag effect="plain">{{ row.suffix }}</el-tag></template></el-table-column>
        <el-table-column label="大小" width="120"><template #default="{ row }">{{ formatBytes(row.size) }}</template></el-table-column>
        <el-table-column label="操作" width="100" fixed="right"><template #default="{ row }"><el-button v-if="row.editable" link type="primary" @click="$router.push(`/knowledge/edit/${encodeURIComponent(row.name)}`)">编辑</el-button><span v-else class="readonly-label">只读</span></template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, EditPen, FolderOpened, Refresh, Upload } from '@element-plus/icons-vue'
import { knowledgeApi } from '@/api/knowledge'

const files = ref([])
const index = ref({})
const selected = ref([])
const selectedDirectory = ref('')
const loading = ref(false)
const rebuilding = ref(false)
const deleting = ref(false)
const uploading = ref(false)
const fileInput = ref()
const indexMode = computed(() => index.value.mode || (index.value.vector ? 'Hybrid' : 'Lexical'))
const directories = computed(() => [...new Set(files.value.map(file => file.directory || 'root'))].sort())
const visibleFiles = computed(() => selectedDirectory.value ? files.value.filter(file => (file.directory || 'root') === selectedDirectory.value) : files.value)
const directoryLabel = directory => directory === 'root' ? '根目录' : directory

const loadOverview = async () => {
  loading.value = true
  try {
    const data = await knowledgeApi.getOverview()
    files.value = data.files || []
    index.value = data.index || {}
    selected.value = []
  } finally { loading.value = false }
}

const uploadFiles = async event => {
  const selectedFiles = Array.from(event.target.files || [])
  if (uploading.value || !selectedFiles.length) return
  uploading.value = true
  try {
    const data = await knowledgeApi.upload(selectedFiles)
    ElMessage.success(data.message)
    await loadOverview()
  } finally {
    event.target.value = ''
    uploading.value = false
  }
}

const deleteSelected = async () => {
  try {
    await ElMessageBox.confirm(`将永久删除所选 ${selected.value.length} 个文件并重建索引，是否继续？`, '删除知识文件', { type: 'warning' })
  } catch { return }
  deleting.value = true
  try {
    const data = await knowledgeApi.delete(selected.value.map(item => item.name))
    ElMessage.success(data.message)
    await loadOverview()
  } finally { deleting.value = false }
}

const rebuildIndex = async () => {
  rebuilding.value = true
  try {
    const data = await knowledgeApi.rebuild()
    ElMessage.success(data.message)
    index.value = data.index || {}
  } finally { rebuilding.value = false }
}

const formatBytes = bytes => {
  if (!Number.isFinite(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

onMounted(loadOverview)
</script>

<style scoped>
.knowledge-container { max-width: 1500px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 24px; }
.eyebrow { display: block; margin-bottom: 8px; color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .14em; }
h1 { margin: 0; color: #182230; font-size: 28px; }
.page-header p, .card-header p { margin: 7px 0 0; color: #667085; font-size: 13px; }
.header-actions { display: flex; gap: 10px; }
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
.stat-grid :deep(.el-card__body) { display: flex; flex-direction: column; padding: 20px; }
.stat-grid span { color: #667085; font-size: 13px; }
.stat-grid strong { margin: 7px 0 4px; color: #101828; font-size: 28px; }
.stat-grid small, .directory-filter small { color: #98a2b3; }
.status-text { font-size: 20px !important; text-transform: capitalize; }
.el-alert { margin-bottom: 12px; }
.files-card { margin-top: 20px; border: 1px solid #e5eaf2; }
.card-header, .directory-filter { display: flex; justify-content: space-between; align-items: center; gap: 14px; }
.card-header h2 { margin: 0; color: #182230; font-size: 18px; }
.directory-filter { justify-content: flex-start; padding: 0 0 16px; }
.directory-filter > span { color: #475467; font-weight: 600; }
.directory-filter .el-select { width: 260px; }
.directory-name, .file-name { display: flex; align-items: center; gap: 9px; color: #344054; }
.directory-name { color: #475467; font-size: 13px; }
.directory-name .el-icon { color: #d97706; }
.file-name { flex-wrap: wrap; font-weight: 600; }
.file-name .el-icon { color: #2563eb; font-size: 18px; }
.file-name small { width: 100%; margin-left: 27px; color: #98a2b3; font-size: 11px; font-weight: 400; word-break: break-all; }
.hidden-upload { display: none; }
.readonly-label { color: #98a2b3; font-size: 12px; }
@media (max-width: 900px) { .stat-grid { grid-template-columns: repeat(2, 1fr); } .page-header { align-items: flex-start; flex-direction: column; } }
@media (max-width: 560px) { .stat-grid { grid-template-columns: 1fr; } .header-actions { width: 100%; } .header-actions .el-button { flex: 1; } .card-header { align-items: flex-start; flex-direction: column; gap: 14px; } .card-header .el-button, .directory-filter .el-select { width: 100%; } .directory-filter { align-items: stretch; flex-direction: column; } }
</style>
