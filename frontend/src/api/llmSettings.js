import request from '@/utils/request'

export function getLlmSettingsApi() {
  return request({
    url: '/settings/llm',
    method: 'get'
  })
}

export function createProviderApi(payload) {
  return request({
    url: '/settings/llm/providers',
    method: 'post',
    data: payload
  })
}

export function updateProviderApi(id, payload) {
  return request({
    url: `/settings/llm/providers/${id}`,
    method: 'put',
    data: payload
  })
}

export function deleteProviderApi(id) {
  return request({
    url: `/settings/llm/providers/${id}`,
    method: 'delete'
  })
}

export function testProviderApi(id) {
  return request({
    url: `/settings/llm/providers/${id}/test`,
    method: 'post',
    timeout: 60000
  })
}

export function setActiveProviderApi(providerId) {
  return request({
    url: '/settings/llm/active',
    method: 'post',
    data: { provider_id: providerId }
  })
}

export function getSettingsStatusApi(silent = false) {
  return request({
    url: '/settings/status',
    method: 'get',
    silent
  })
}
