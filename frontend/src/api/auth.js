import request from '@/utils/request'

export const authApi = {
  status() {
    return request.get('/auth/status')
  },

  setup(username, password, confirmPassword) {
    return request.post('/auth/setup', {
      username,
      password,
      confirm_password: confirmPassword
    })
  },

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
  },

  changePassword(currentPassword, newPassword, confirmPassword) {
    return request.post('/auth/password', {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword
    })
  }
}
