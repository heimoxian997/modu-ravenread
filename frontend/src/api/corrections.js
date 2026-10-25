import request from '@/utils/request'

async function call(url, method = 'get', data) {
  const response = await request({ url, method, data, silent: true })
  const result = response.data || {}
  if (!result.ok) throw new Error(result.data?.message || result.error || '操作失败')
  return result.data
}

export const listCorrections = (bookId, page = 1, status) =>
  call(`/books/${bookId}/corrections?page=${page}${status ? `&status=${status}` : ''}`)
export const approveCorrection = (bookId, id, replacement) =>
  call(`/books/${bookId}/corrections/${id}/approve`, 'post', { replacement, rebuild: true })
export const rejectCorrection = (bookId, id) =>
  call(`/books/${bookId}/corrections/${id}/reject`, 'post')
export const rebuildCorrection = (bookId, id) =>
  call(`/books/${bookId}/corrections/${id}/rebuild`, 'post')
