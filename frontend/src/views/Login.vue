<template>
  <div class="login-container" v-loading="checking">
    <main class="login-box">
      <div class="login-header">
        <div class="logo">EA</div>
        <h1>{{ setupRequired ? '创建管理员' : '售后工作台' }}</h1>
        <p>{{ setupRequired ? '首次使用，请设置本机管理员账号' : '使用管理员账号登录' }}</p>
      </div>

      <el-alert
        v-if="setupRequired"
        title="密码至少 12 位，并包含大小写字母、数字和符号中的至少三类。"
        type="info"
        :closable="false"
        show-icon
      />

      <el-form
        ref="loginFormRef"
        :model="loginForm"
        :rules="rules"
        class="login-form"
        @keyup.enter="handleSubmit"
      >
        <el-form-item prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="用户名"
            size="large"
            :prefix-icon="User"
            autocomplete="username"
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            :placeholder="setupRequired ? '设置密码' : '密码'"
            size="large"
            :prefix-icon="Lock"
            :autocomplete="setupRequired ? 'new-password' : 'current-password'"
            show-password
          />
        </el-form-item>

        <el-form-item v-if="setupRequired" prop="confirmPassword">
          <el-input
            v-model="loginForm.confirmPassword"
            type="password"
            placeholder="确认密码"
            size="large"
            :prefix-icon="CircleCheck"
            autocomplete="new-password"
            show-password
          />
        </el-form-item>

        <el-button
          type="primary"
          size="large"
          :loading="loading"
          class="login-button"
          @click="handleSubmit"
        >
          {{ setupRequired ? '创建并进入系统' : '登录' }}
        </el-button>
      </el-form>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CircleCheck, Lock, User } from '@element-plus/icons-vue'
import { useAuthStore } from '@/store/auth'

const router = useRouter()
const authStore = useAuthStore()

const loginFormRef = ref(null)
const checking = ref(true)
const loading = ref(false)
const setupRequired = ref(false)

const loginForm = reactive({
  username: '',
  password: '',
  confirmPassword: ''
})

const validatePassword = (_, value, callback) => {
  if (!value) return callback(new Error('请输入密码'))
  if (!setupRequired.value) return callback()
  if (value.length < 12) return callback(new Error('密码长度不能少于 12 位'))
  const categories = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/]
    .filter(pattern => pattern.test(value)).length
  if (categories < 3) return callback(new Error('密码复杂度不足'))
  callback()
}

const validateConfirmation = (_, value, callback) => {
  if (!setupRequired.value) return callback()
  if (!value) return callback(new Error('请再次输入密码'))
  if (value !== loginForm.password) return callback(new Error('两次输入的密码不一致'))
  callback()
}

const rules = computed(() => ({
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 64, message: '用户名长度为 3-64 位', trigger: 'blur' }
  ],
  password: [{ validator: validatePassword, trigger: ['blur', 'change'] }],
  confirmPassword: [{ validator: validateConfirmation, trigger: ['blur', 'change'] }]
}))

const loadStatus = async () => {
  checking.value = true
  try {
    const data = await authStore.status()
    if (data.authenticated) {
      await router.replace('/')
      return
    }
    setupRequired.value = data.setup_required
    if (setupRequired.value) loginForm.username = 'admin'
  } finally {
    checking.value = false
  }
}

const handleSubmit = async () => {
  if (!loginFormRef.value) return
  const valid = await loginFormRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    if (setupRequired.value) {
      await authStore.setup(loginForm.username, loginForm.password, loginForm.confirmPassword)
      ElMessage.success('管理员创建成功')
    } else {
      await authStore.login(loginForm.username, loginForm.password)
      ElMessage.success('登录成功')
    }
    await router.replace('/')
  } catch (error) {
    if (error.response?.data?.setup_required) {
      setupRequired.value = true
      loginForm.username = 'admin'
      loginForm.password = ''
    }
  } finally {
    loading.value = false
  }
}

onMounted(loadStatus)
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: #f3f6fa;
}

.login-box {
  width: min(420px, 100%);
  padding: 42px 38px;
  background: white;
  border: 1px solid #dfe6ef;
  border-radius: 8px;
  box-shadow: 0 12px 36px rgba(20, 33, 55, 0.08);
}

.login-header {
  margin-bottom: 28px;
  text-align: center;
}

.logo {
  width: 58px;
  height: 58px;
  margin: 0 auto 16px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: #175cd3;
  box-shadow: 0 8px 20px rgba(23, 92, 211, 0.24);
  color: white;
  font-size: 24px;
  font-weight: 800;
}

.login-header h1 {
  margin: 0 0 8px;
  color: #182230;
  font-size: 24px;
}

.login-header p {
  margin: 0;
  color: #667085;
  font-size: 14px;
}

.login-form { margin-top: 24px; }
.login-button { width: 100%; margin-top: 8px; }

@media (max-width: 520px) {
  .login-container { align-items: flex-start; padding: 24px 14px; }
  .login-box { padding: 32px 22px; }
}
</style>
