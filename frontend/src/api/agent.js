import request from '@/utils/request'

export function getAgentCapabilitiesApi(bookId) {
  return request({
    url: `/books/${bookId}/agent/capabilities`,
    method: 'get',
    silent: true
  })
}

export function getAgentSessionApi(bookId) {
  return request({
    url: `/books/${bookId}/agent/session`,
    method: 'get',
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
