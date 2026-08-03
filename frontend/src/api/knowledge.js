import request from '@/utils/request'

export const knowledgeApi = {
  getOverview() {
    return request.get('/knowledge')
  },

  upload(files, onProgress) {
    const form = new FormData()
    files.forEach(file => form.append('files', file.raw || file))
    return request.post('/knowledge/upload', form, {
      timeout: 120000,
      onUploadProgress: onProgress
    })
  },

  delete(names) {
    return request.post('/knowledge/delete', { names }, { timeout: 120000 })
  },

  rebuild() {
    return request.post('/knowledge/rebuild', null, { timeout: 120000 })
  },

  previewArticle(article) {
    return request.post('/knowledge/articles/preview', article)
  },

  createArticle(article) {
    return request.post('/knowledge/articles', article, { timeout: 120000 })
  },

  getArticle(filename) {
    return request.get(`/knowledge/articles/${encodeURIComponent(filename)}`)
  },

  updateArticle(filename, article) {
    return request.put(`/knowledge/articles/${encodeURIComponent(filename)}`, article, { timeout: 120000 })
  }
}
