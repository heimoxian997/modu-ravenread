import request from '@/utils/request'

const root = (bookId) => `/books/${bookId}/role-chat`
async function call(url, method = 'get', data, params) {
  const response = await request({ url, method, data, params, silent: true })
  return response.data
}

export const rolePreparationApi = {
  book: (id) => call(`/books/${id}`),
  characters: (id, query = '') => call(`${root(id)}/characters`, 'get', undefined, { query, limit: 20 }),
  roles: (id, page = 1) => call(`${root(id)}/roles`, 'get', undefined, { offset: (page - 1) * 20, limit: 20 }),
  jobs: (id, page = 1) => call(`${root(id)}/preparation/jobs`, 'get', undefined, { offset: (page - 1) * 20, limit: 20 }),
  preview: (id, payload) => call(`${root(id)}/preparation/plans/preview`, 'post', payload),
  start: (id, payload) => call(`${root(id)}/preparation/plans`, 'post', payload),
  batches: (id, jobId, page = 1) => call(`${root(id)}/preparation/jobs/${jobId}/batches`, 'get', undefined, { offset: (page - 1) * 20, limit: 20 }),
  batch: (id, jobId, number) => call(`${root(id)}/preparation/jobs/${jobId}/batches/${number}`),
  editBatch: (id, jobId, number, payload) => call(`${root(id)}/preparation/jobs/${jobId}/batches/${number}`, 'put', payload),
  extract: (id, jobId, payload) => call(`${root(id)}/preparation/jobs/${jobId}/extract`, 'post', payload),
  synthesize: (id, jobId, payload) => call(`${root(id)}/preparation/jobs/${jobId}/synthesize`, 'post', payload),
  synthesisPreview: (id, jobId) => call(`${root(id)}/preparation/jobs/${jobId}/synthesis/preview`),
  synthesisResponse: (id, jobId) => call(`${root(id)}/preparation/jobs/${jobId}/synthesis/response`),
  editSynthesisResponse: (id, jobId, payload) => call(`${root(id)}/preparation/jobs/${jobId}/synthesis/response`, 'put', payload),
  deleteJob: (id, jobId) => call(`${root(id)}/preparation/jobs/${jobId}`, 'delete'),
  deleteRole: (id, roleId) => call(`${root(id)}/roles/${roleId}`, 'delete'),
  job: (id, jobId) => call(`${root(id)}/preparation/jobs/${jobId}`),
  pause: (id, jobId) => call(`${root(id)}/preparation/jobs/${jobId}/pause`, 'post'),
  resume: (id, jobId) => call(`${root(id)}/preparation/jobs/${jobId}/resume`, 'post', {}),
  profile: (id, roleId) => call(`${root(id)}/roles/${roleId}/profile`),
  evidence: (id, profileId, params = {}) => call(`${root(id)}/profiles/${profileId}/evidence`, 'get', undefined, params)
}
