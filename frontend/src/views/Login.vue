<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-header">
        <div class="logo">EA</div>
        <h1>Email AI Agent</h1>
        <p>Technical Support System</p>
      </div>
      
      <el-form
        ref="loginFormRef"
        :model="loginForm"
        :rules="rules"
        class="login-form"
        @keyup.enter="handleLogin"
      >
        <el-form-item prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="用户名"
            size="large"
            :prefix-icon="User"
          />
        </el-form-item>
        
        <el-form-item prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="密码"
            size="large"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        
        <el-button
          type="primary"
          size="large"
          :loading="loading"
          class="login-button"
          @click="handleLogin"
        >
          登录
        </el-button>
      </el-form>
      
      <div class="login-footer">
        <el-text size="small" type="info">
          默认用户名: admin / 密码: admin123
        </el-text>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/store/auth'

const router = useRouter()
const authStore = useAuthStore()

const loginFormRef = ref(null)
const loading = ref(false)

const loginForm = reactive({
  username: '',
  password: ''
})

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度不能少于6位', trigger: 'blur' }
  ]
}

const handleLogin = async () => {
  if (!loginFormRef.value) return
  
  await loginFormRef.value.validate(async (valid) => {
    if (valid) {
      loading.value = true
      try {
        await authStore.login(loginForm.username, loginForm.password)
        ElMessage.success('登录成功')
        router.push('/')
      } catch (error) {
        console.error('登录失败:', error)
      } finally {
        loading.value = false
      }
    }
  })
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(circle at 85% -10%, #e8f0ff, transparent 28%), #f5f7fb;
}

.login-box {
  width: 420px;
  padding: 48px 40px;
  background: white;
  border-radius: 16px;
  box-shadow: 0 12px 36px rgba(20, 33, 55, 0.07);
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.logo {
  width: 64px;
  height: 64px;
  margin: 0 auto 16px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  background: linear-gradient(145deg, #3b82f6, #1d4ed8);
  box-shadow: 0 8px 20px rgba(11, 63, 155, 0.4);
  font-size: 28px;
  font-weight: 800;
  color: white;
}

.login-header h1 {
  margin: 0 0 8px;
  font-size: 24px;
  color: #182230;
}

.login-header p {
  margin: 0;
  color: #65758b;
  font-size: 14px;
}

.login-form {
  margin-top: 32px;
}

.login-button {
  width: 100%;
  margin-top: 8px;
}

.login-footer {
  margin-top: 24px;
  text-align: center;
}
</style>
