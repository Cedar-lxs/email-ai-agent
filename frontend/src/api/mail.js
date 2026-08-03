import request from '@/utils/request'

const messagePath = messageId => encodeURIComponent(String(messageId || ''))

export const mailApi = {
  // 获取邮件列表
  getList(params) {
    return request.get('/mails', { params })
  },
  
  // 获取邮件详情
  getDetail(messageId) {
    return request.get(`/mails/${messagePath(messageId)}`)
  },
  
  // 保存草稿
  saveDraft(messageId, body) {
    return request.post(`/mails/${messagePath(messageId)}/save`, { body })
  },
  
  // 批准并发送
  approve(messageId) {
    return request.post(`/mails/${messagePath(messageId)}/approve`)
  },
  
  // 拒绝草稿
  reject(messageId, reason) {
    return request.post(`/mails/${messagePath(messageId)}/reject`, { reason })
  },
  
  // 删除邮件
  delete(messageIds) {
    return request.post('/mails/delete', { message_ids: messageIds })
  },
  
  // 获取统计信息
  getStats() {
    return request.get('/mails/stats')
  }
}
