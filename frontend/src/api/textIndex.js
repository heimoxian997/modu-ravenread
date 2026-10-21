import request from '@/utils/request'

export function getTextIndexStatusApi(bookId, silent = false) {
  return request({
    url: `/books/${bookId}/text-index`,
    method: 'get',
    silent
  })
}

export function startTextIndexApi(bookId) {
  return request({
    url: `/books/${bookId}/text-index`,
    method: 'post'
  })
}

export function searchTextIndexApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/search`,
    method: 'post',
    data: payload,
    timeout: 60000
  })
}

export function deleteTextIndexApi(bookId) {
  return request({
    url: `/books/${bookId}/text-index`,
    method: 'delete'
  })
}
