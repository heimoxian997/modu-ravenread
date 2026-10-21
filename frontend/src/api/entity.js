import request from '@/utils/request'

export function getEntityIndexStatusApi(bookId) {
  return request({
    url: `/books/${bookId}/entity-index`,
    method: 'get',
    silent: true
  })
}

export function rebuildEntityLayerApi(bookId) {
  return request({
    url: `/books/${bookId}/entity-layer`,
    method: 'post',
    timeout: 120000
  })
}

export function buildEntityIndexApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/entity-index`,
    method: 'post',
    data: payload,
    timeout: 120000
  })
}

export function searchCharactersApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/character-search`,
    method: 'post',
    data: payload,
    timeout: 120000
  })
}

export function searchLoreApi(bookId, payload) {
  return request({
    url: `/books/${bookId}/lore-search`,
    method: 'post',
    data: payload,
    timeout: 120000
  })
}

export function getCharacterDetailApi(bookId, characterId, chapterIdx) {
  return request({
    url: `/books/${bookId}/characters/${characterId}`,
    method: 'get',
    params:
      chapterIdx === null || chapterIdx === undefined
        ? undefined
        : { chapter_idx: chapterIdx }
  })
}

export function getAliasCandidatesApi(bookId, alias, chapterIdx, limit = 20) {
  return request({
    url: `/books/${bookId}/entities/aliases/${encodeURIComponent(alias)}/candidates`,
    method: 'get',
    params: {
      chapter_idx: chapterIdx === null || chapterIdx === undefined ? undefined : chapterIdx,
      limit
    },
    silent: true
  })
}

export function updateCharacterCardApi(bookId, characterId, payload) {
  return request({
    url: `/books/${bookId}/characters/${characterId}/card`,
    method: 'put',
    data: payload
  })
}

export function resetCharacterCardApi(bookId, characterId, chapterIdx) {
  return request({
    url: `/books/${bookId}/characters/${characterId}/card`,
    method: 'delete',
    params:
      chapterIdx === null || chapterIdx === undefined
        ? undefined
        : { chapter_idx: chapterIdx }
  })
}

export function getLoreDetailApi(bookId, loreId, chapterIdx) {
  return request({
    url: `/books/${bookId}/lore/${loreId}`,
    method: 'get',
    params:
      chapterIdx === null || chapterIdx === undefined
        ? undefined
        : { chapter_idx: chapterIdx }
  })
}

export function updateLoreApi(bookId, loreId, payload) {
  return request({
    url: `/books/${bookId}/lore/${loreId}`,
    method: 'put',
    data: payload,
    params:
      payload?.chapter_idx === null || payload?.chapter_idx === undefined
        ? undefined
        : { chapter_idx: payload.chapter_idx }
  })
}

export function resetLoreApi(bookId, loreId, chapterIdx) {
  return request({
    url: `/books/${bookId}/lore/${loreId}`,
    method: 'delete',
    params:
      chapterIdx === null || chapterIdx === undefined
        ? undefined
        : { chapter_idx: chapterIdx }
  })
}
