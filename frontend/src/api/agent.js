import request from '@/utils/request'

export function getAgentCapabilitiesApi(bookId) {
  return request({
    url: `/books/${bookId}/agent/capabilities`,
    method: 'get',
    silent: true
  })
}

export function getReadingProgressApi(bookId) {
  return request({
    url: `/books/${bookId}/progress`,
    method: 'get',
    silent: true
  })
}

export function putReadingProgressApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/progress`,
    method: 'put',
    data: payload,
    silent: true
  })
}

export function getAgentSessionApi(bookId, chapterIdx, spoilers = false) {
  return request({
    url: `/books/${bookId}/agent/session`,
    method: 'get',
    params: { chapter_idx: chapterIdx, spoilers },
    silent: true
  })
}

export function chatAgentApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/agent/chat`,
    method: 'post',
    data: payload,
    timeout: 150000
  })
}

export function clearAgentSessionApi(bookId) {
  return request({
    url: `/books/${bookId}/agent/session`,
    method: 'delete'
  })
}
