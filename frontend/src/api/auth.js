import request from '@/utils/request'

export const authApi = {
  // 登录
  login(username, password) {
    return request.post('/auth/login', { username, password })
  },
  
  // 验证 token
  verify() {
    return request.get('/auth/verify')
  },
  
  // 登出
  logout() {
    return request.post('/auth/logout')
  }
}
