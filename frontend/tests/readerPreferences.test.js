import test from 'node:test'
import assert from 'node:assert/strict'
import { DEFAULT_READER_PREFERENCES, parseReaderPreferences } from '../src/utils/readerPreferences.js'

test('missing and malformed settings use comfortable defaults', () => {
  for (const raw of [null, '', '{broken', '[]', 'false']) assert.deepEqual(parseReaderPreferences(raw), DEFAULT_READER_PREFERENCES)
})

test('legacy font indexes preserve existing reader size without accepting invalid values', () => {
  assert.equal(parseReaderPreferences(null, '2').fontSize, 18)
  assert.equal(parseReaderPreferences(null, '5').fontSize, 23)
  for (const value of ['-1', '99', 'abc']) assert.equal(parseReaderPreferences(null, value).fontSize, 20)
})

test('new settings round-trip and take priority over legacy size', () => {
  const value = { fontSize: 24, lineHeight: 2.2, pageWidth: 940, fontFamily: 'sans' }
  assert.deepEqual(parseReaderPreferences(JSON.stringify(value), '0'), value)
})

test('untrusted storage cannot inject styles or unbounded dimensions', () => {
  const value = { fontSize: 9000, lineHeight: 'url(example)', pageWidth: -1, fontFamily: 'serif; color:red' }
  assert.deepEqual(parseReaderPreferences(JSON.stringify(value)), DEFAULT_READER_PREFERENCES)
})
