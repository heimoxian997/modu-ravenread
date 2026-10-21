import { reactive } from 'vue'

/**
 * 上传向导状态。整个应用只有这一处跨页面共享的状态，
 * 一个模块级 reactive 对象就够了，没必要引入状态管理库。
 */
function createInitialState() {
  return {
    step: 0,
    file: null,
    draft: null,
    chapterSamples: [''],
    extraSamples: [],
    parseResult: null,
    chapters: [],
    selectedIndex: null,
    dirty: false,
    acknowledged: false,
    book: {
      title: '',
      author: '',
      synopsis: '',
      protagonistsText: ''
    }
  }
}

export const uploadState = reactive(createInitialState())

export function resetUploadState() {
  Object.assign(uploadState, createInitialState())
}

export function markDirty() {
  uploadState.dirty = true
}

export function renameChapter(index, title) {
  const chapter = uploadState.chapters[index]
  if (!chapter) return
  chapter.title = title
  uploadState.dirty = true
}

export function toggleChapterKind(index) {
  const chapter = uploadState.chapters[index]
  if (!chapter) return
  chapter.kind = chapter.kind === 'main' ? 'extra' : 'main'
  uploadState.dirty = true
}

export function mergeWithPrevious(index) {
  if (index <= 0 || index >= uploadState.chapters.length) return
  const previous = uploadState.chapters[index - 1]
  const current = uploadState.chapters[index]
  previous.end = current.end
  previous.word_count = previous.end - previous.start
  uploadState.chapters.splice(index, 1)
  uploadState.selectedIndex = index - 1
  uploadState.dirty = true
}

export function splitChapter(index, offset) {
  const chapter = uploadState.chapters[index]
  if (!chapter) return
  if (!Number.isInteger(offset) || offset <= chapter.start || offset >= chapter.end) {
    return
  }
  const newChapter = {
    start: offset,
    end: chapter.end,
    title: '新章节',
    kind: chapter.kind,
    word_count: chapter.end - offset
  }
  chapter.end = offset
  chapter.word_count = offset - chapter.start
  uploadState.chapters.splice(index + 1, 0, newChapter)
  uploadState.selectedIndex = index + 1
  uploadState.dirty = true
}

export function applyParseResult(result) {
  uploadState.parseResult = result
  uploadState.chapters = (result.chapters || []).map((chapter) => ({ ...chapter }))
  uploadState.selectedIndex = uploadState.chapters.length ? 0 : null
  uploadState.acknowledged = !(result.warnings || []).length
  uploadState.dirty = false
  uploadState.step = 2
}
