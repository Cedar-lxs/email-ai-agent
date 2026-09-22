import request from '@/utils/request'

const jobPath = jobId => encodeURIComponent(String(jobId || ''))
const deliveryPath = deliveryId => encodeURIComponent(String(deliveryId || ''))

export const operationsApi = {
  list(params) {
    return request.get('/operations/mail-jobs', { params })
  },
  detail(jobId) {
    return request.get(`/operations/mail-jobs/${jobPath(jobId)}`)
  },
  retry(jobId, reason) {
    return request.post(`/operations/mail-jobs/${jobPath(jobId)}/retry`, { reason })
  },
  escalate(jobId, reason) {
    return request.post(`/operations/mail-jobs/${jobPath(jobId)}/escalate`, { reason })
  },
  confirmSent(deliveryId, reason) {
    return request.post(
      `/operations/deliveries/${deliveryPath(deliveryId)}/confirm-sent`,
      { reason }
    )
  },
  authorizeResend(deliveryId, reason) {
    return request.post(
      `/operations/deliveries/${deliveryPath(deliveryId)}/authorize-resend`,
      { reason }
    )
  }
}
