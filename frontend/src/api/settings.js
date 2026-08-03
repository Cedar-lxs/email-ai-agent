import request from '@/utils/request'

export const settingsApi = {
  get() {
    return request.get('/settings')
  },

  setMode(mode) {
    return request.put('/settings/mode', { mode })
  },

  testMail() {
    return request.post('/settings/test-mail', null, { timeout: 70000 })
  },

  testAi() {
    return request.post('/settings/test-ai', null, { timeout: 70000 })
  }
}
