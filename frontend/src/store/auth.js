import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('auth_token') || '')
  const username = ref(localStorage.getItem('username') || '')
  
  const isAuthenticated = computed(() => !!token.value)
  
  const login = async (loginUsername, password) => {
    const data = await authApi.login(loginUsername, password)
    token.value = data.token
    username.value = loginUsername
    localStorage.setItem('auth_token', data.token)
    localStorage.setItem('username', loginUsername)
    return data
  }
  
  const logout = () => {
    token.value = ''
    username.value = ''
    localStorage.removeItem('auth_token')
    localStorage.removeItem('username')
  }
  
  const checkAuth = async () => {
    try {
      await authApi.verify()
      return true
    } catch (error) {
      logout()
      return false
    }
  }
  
  return {
    token,
    username,
    isAuthenticated,
    login,
    logout,
    checkAuth
  }
})
