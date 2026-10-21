import request from '@/utils/request'

export function extractChapterApi(
  bookId,
  chapterIdx,
  providerId = null,
  checkProviderId = null
) {
  const params = {}
  if (providerId) params.provider_id = providerId
  if (checkProviderId) params.check_provider_id = checkProviderId
  return request({
    url: `/books/${bookId}/chapters/${chapterIdx}/extract`,
    method: 'post',
    params: Object.keys(params).length ? params : undefined,
    // 生成 300s + 解析重试 120s + 称谓复核 60s，前端要给足时间
    timeout: 600000
  })
}

export function getSummaryOverviewApi(bookId) {
  return request({
    url: `/books/${bookId}/summaries/overview`,
    method: 'get'
  })
}

export function listSummariesApi(bookId, params) {
  return request({
    url: `/books/${bookId}/summaries`,
    method: 'get',
    params
  })
}

export function getSummaryDetailApi(bookId, chapterIdx) {
  return request({
    url: `/books/${bookId}/summaries/${chapterIdx}`,
    method: 'get'
  })
}

export function updateSummaryApi(bookId, chapterIdx, payload) {
  return request({
    url: `/books/${bookId}/summaries/${chapterIdx}`,
    method: 'put',
    data: payload
  })
}

export function startBatchJobApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/summaries/jobs`,
    method: 'post',
    data: payload
  })
}

export function getLatestBatchJobApi(bookId, silent = false) {
  return request({
    url: `/books/${bookId}/summaries/jobs/latest`,
    method: 'get',
    silent
  })
}

export function getBatchJobApi(bookId, jobId) {
  return request({
    url: `/books/${bookId}/summaries/jobs/${jobId}`,
    method: 'get'
  })
}

export function stopBatchJobApi(bookId) {
  return request({
    url: `/books/${bookId}/summaries/jobs/stop`,
    method: 'post'
  })
}

export function getSummaryIndexStatusApi(bookId) {
  return request({
    url: `/books/${bookId}/summary-index`,
    method: 'get'
  })
}

export function buildSummaryIndexApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/summary-index`,
    method: 'post',
    data: payload,
    timeout: 120000
  })
}

export function updateExtractionApi(bookId, chapterIdx, payload) {
  return request({
    url: `/books/${bookId}/chapters/${chapterIdx}/extraction`,
    method: 'patch',
    data: payload
  })
}

export function searchSummariesApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/summary-search`,
    method: 'post',
    data: payload,
    timeout: 120000
  })
}
