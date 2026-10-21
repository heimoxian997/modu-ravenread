export function countChars(text) {
  return Array.from(text || '').length
}

export function parseProtagonists(text) {
  const seen = new Set()
  const result = []
  for (const part of String(text || '').split(/[,，、;；\n]+/)) {
    const name = part.trim()
    if (name && !seen.has(name)) {
      seen.add(name)
      result.push(name)
    }
    if (result.length >= 3) break
  }
  return result
}

export function formatNumber(value) {
  const number = Number(value || 0)
  return number.toLocaleString('zh-CN')
}

export function formatCharCount(value) {
  const number = Number(value || 0)
  if (number >= 10000) {
    return `${(number / 10000).toFixed(number >= 100000 ? 0 : 1)} 万字`
  }
  return `${formatNumber(number)} 字`
}

export function formatFileSize(bytes) {
  const size = Number(bytes || 0)
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

export function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = (part) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`
}
