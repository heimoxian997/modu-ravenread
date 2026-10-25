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

export const listStorylines = (bookId, filters = {}, signal) =>
  call(`/books/${bookId}/storylines/query`, 'post', { limit: 10, ...filters }, signal)
export const getStoryline = (bookId, noteId, signal) =>
  call(`/books/${bookId}/storylines/${encodeURIComponent(noteId)}`, 'get', undefined, signal)
export const getEvidenceNote = (bookId, noteId, version, signal) =>
  call(`/books/${bookId}/reading-notes/query`, 'post', { note_id: noteId, version, view: 'entry' }, signal)
