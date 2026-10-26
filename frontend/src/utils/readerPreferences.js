export const READER_PREFERENCES_KEY = 'ravenread.reader.preferences'
export const FONT_SIZES = Array.from({ length: 11 }, (_, i) => 16 + i)
export const LINE_HEIGHTS = [1.7, 1.95, 2.2]
export const PAGE_WIDTHS = [640, 780, 940]
export const DEFAULT_READER_PREFERENCES = Object.freeze({ fontSize: 20, lineHeight: 1.95, pageWidth: 780, fontFamily: 'serif' })
const LEGACY_FONTS = [16, 17, 18, 19, 21, 23]

export function parseReaderPreferences(raw, legacyFontIndex = null) {
  let parsed = null
  try { parsed = JSON.parse(raw) } catch { /* Invalid browser storage uses defaults. */ }
  const value = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  const legacy = legacyFontIndex === null ? null : Number(legacyFontIndex)
  const fallbackFont = Number.isInteger(legacy) && legacy >= 0 && legacy < LEGACY_FONTS.length ? LEGACY_FONTS[legacy] : 20
  return {
    fontSize: FONT_SIZES.includes(value.fontSize) ? value.fontSize : fallbackFont,
    lineHeight: LINE_HEIGHTS.includes(value.lineHeight) ? value.lineHeight : DEFAULT_READER_PREFERENCES.lineHeight,
    pageWidth: PAGE_WIDTHS.includes(value.pageWidth) ? value.pageWidth : DEFAULT_READER_PREFERENCES.pageWidth,
    fontFamily: ['serif', 'sans'].includes(value.fontFamily) ? value.fontFamily : 'serif'
  }
}
