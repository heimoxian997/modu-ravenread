import request from '@/utils/request'

export function listBooksApi() {
  return request({ url: '/books', method: 'get' })
}

export function getBookApi(bookId) {
  return request({ url: `/books/${bookId}`, method: 'get' })
}

export function listChaptersApi(bookId) {
  return request({ url: `/books/${bookId}/chapters`, method: 'get' })
}

export function getChapterApi(bookId, idx) {
  return request({ url: `/books/${bookId}/chapters/${idx}`, method: 'get' })
}

export function updateBookApi(bookId, payload) {
  return request({ url: `/books/${bookId}`, method: 'patch', data: payload })
}

export function deleteBookApi(bookId) {
  return request({ url: `/books/${bookId}`, method: 'delete' })
}
