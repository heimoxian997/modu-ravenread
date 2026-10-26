import request from '@/utils/request'

async function call(url, method, data, signal) {
  const response = await request({ url, method, data, signal, silent: true })
  const result = response.data || {}
  if (!result.ok) {
    const error = new Error(result.data?.message || ({
      no_reading_context: '请先在阅读器打开这本书，再查看剧情线。',
      not_found: '当前阅读进度内没有这条剧情线。',
      invalid_cursor: '阅读进度或记录已变化，请刷新后重新浏览。'
    })[result.error] || result.error || '加载失败')
    error.code = result.error
    throw error
  }
  return result.data
}

export const listStorylines = (bookId, filters = {}, signal) => {
  const params = new URLSearchParams(Object.entries({ limit: 10, ...filters }).filter(([, value]) => value !== undefined && value !== null))
  return call(`/books/${bookId}/storylines?${params}`, 'get', undefined, signal)
}
export const getStoryline = (bookId, noteId, signal) =>
  call(`/books/${bookId}/storylines/${encodeURIComponent(noteId)}`, 'get', undefined, signal)
export const getStorylineEvidence = (bookId, noteId, nodeId, signal) =>
  call(`/books/${bookId}/storylines/${encodeURIComponent(noteId)}/nodes/${encodeURIComponent(nodeId)}/evidence`, 'get', undefined, signal)
