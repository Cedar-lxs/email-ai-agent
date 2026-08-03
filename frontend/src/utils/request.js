import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'


const request = axios.create({
  baseURL: '/api',
  timeout: 30000
})

// 请求拦截器
request.interceptors.request.use(
  config => {
    const token = localStorage.getItem('auth_token')
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
    return config
  },
  error => {
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  response => {
    const contentType = response.headers?.['content-type'] || ''
    if (!contentType.includes('application/json')) {
      ElMessage.error('后端接口版本不匹配，请重启后端服务后刷新页面')
      return Promise.reject(new Error('API 返回了非 JSON 响应'))
    }
    return response.data
  },
  error => {
    if (error.response) {
      const { status, data } = error.response
      
      if (status === 401 && !error.config?.url?.includes('/auth/login')) {
        localStorage.removeItem('auth_token')
        localStorage.removeItem('username')

        if (router.currentRoute.value.path !== '/login') {
          router.replace({ path: '/login', query: { reason: 'expired' } })
          ElMessage.error(data?.error || '登录已过期，请重新登录')
        }
      } else if (status === 401) {
        ElMessage.error(data?.error || '用户名或密码错误')
      } else if (status === 403) {
        ElMessage.error('没有权限访问')
      } else {
        ElMessage.error(data?.error || data?.message || '请求失败')
      }
    } else {
      ElMessage.error(error.message || '网络错误，请确认后端服务已启动')
    }
    
    return Promise.reject(error)
  }
)

export default request
