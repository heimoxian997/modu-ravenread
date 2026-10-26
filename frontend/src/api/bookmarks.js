import request from '@/utils/request'

export const listBookmarks = (bookId, page = 1) => request({ url: `/books/${bookId}/bookmarks`, params: { page, limit: 20 }, silent: true })
export const createBookmark = (bookId, position) => request({ url: `/books/${bookId}/bookmarks`, method: 'post', data: position, silent: true })
export const updateBookmark = (bookId, id, label) => request({ url: `/books/${bookId}/bookmarks/${id}`, method: 'patch', data: { label }, silent: true })
export const deleteBookmark = (bookId, id) => request({ url: `/books/${bookId}/bookmarks/${id}`, method: 'delete', silent: true })
