import request from '@/utils/request'

export function uploadDraftApi(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: '/drafts',
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000
  })
}

export function parseDraftApi(token, payload) {
  return request({
    url: `/drafts/${token}/parse`,
    method: 'post',
    data: payload
  })
}

export function getDraftContentApi(token, start, end) {
  return request({
    url: `/drafts/${token}/content`,
    method: 'get',
    params: { start, end }
  })
}

export function confirmDraftApi(token, payload) {
  return request({
    url: `/drafts/${token}/confirm`,
    method: 'post',
    data: payload
  })
}
