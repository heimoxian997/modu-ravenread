import request from '@/utils/request'

export function buildChunksApi(bookId) {
  return request({
    url: `/books/${bookId}/chunks`,
    method: 'post'
  })
}

export function getChunkOverviewApi(bookId) {
  return request({
    url: `/books/${bookId}/chunks/overview`,
    method: 'get'
  })
}

export function getChapterChunksApi(bookId, chapterIdx) {
  return request({
    url: `/books/${bookId}/chunks`,
    method: 'get',
    params: { chapter_idx: chapterIdx }
  })
}

export function clearChunksApi(bookId) {
  return request({
    url: `/books/${bookId}/chunks`,
    method: 'delete'
  })
}
