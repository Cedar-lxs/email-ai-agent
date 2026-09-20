import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const username = ref(localStorage.getItem('username') || '')

  const isAuthenticated = computed(() => !!username.value)

  const rememberUser = value => {
    username.value = value || ''
    if (username.value) localStorage.setItem('username', username.value)
    else localStorage.removeItem('username')
  }

  const status = async () => {
    const data = await authApi.status()
    rememberUser(data.authenticated ? data.username : '')
    return data
  }

  const setup = async (loginUsername, password, confirmPassword) => {
    const data = await authApi.setup(loginUsername, password, confirmPassword)
    rememberUser(data.username)
    return data
  }
  
  const login = async (loginUsername, password) => {
    const data = await authApi.login(loginUsername, password)
    rememberUser(data.username)
    return data
  }

  const logout = async () => {
    try {
      await authApi.logout()
    } finally {
      rememberUser('')
    }
  }

  const clearSession = () => rememberUser('')

  const checkAuth = async () => {
    try {
      const data = await authApi.verify()
      rememberUser(data.username)
      return true
    } catch (error) {
      rememberUser('')
      return false
    }
  }
  
  return {
    username,
    isAuthenticated,
    status,
    setup,
    login,
    logout,
    clearSession,
    checkAuth
  }
})
