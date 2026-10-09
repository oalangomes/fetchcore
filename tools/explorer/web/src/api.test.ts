import { afterEach, describe, expect, it, vi } from 'vitest'
import { getConfig, parseIntent, search, ApiError, sourcePayloadForHit } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('HTTP boundary contract', () => {
  it('sends a typed intent without changing anchors or branches', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ backend: 'local-semantic', status: 'ok', results: [] }) })
    vi.stubGlobal('fetch', fetch)
    const intent = { schema_version: 2, canonical_query: 'lookup', anchors: [{ type: 'anchor', kind: 'path', value: 'src/auth.py', origin: 'caller' }] }
    await search({ repository: 'example/catalog', intent, limit: 3, mode: 'targeted', knowledge_types: [] })
    const [, options] = fetch.mock.calls[0]
    expect(JSON.parse(options.body)).toEqual({ repository: 'example/catalog', intent, limit: 3, mode: 'targeted', knowledge_types: [] })
  })

  it('rejects malformed intents without interpreting them', () => {
    expect(() => parseIntent('[]')).toThrow('objeto JSON')
    expect(() => parseIntent('{ bad')).toThrow()
    expect(parseIntent('{"schema_version":2,"canonical_query":"abc"}')).toEqual({ schema_version: 2, canonical_query: 'abc' })
  })

  it('preserves categorized server errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 403,
      json: async () => ({ error: { category: 'unauthorized_repository', message: 'outside binding' } }),
    }))
    await expect(getConfig()).rejects.toMatchObject({ category: 'unauthorized_repository', status: 403 })
    expect(new ApiError('x', 'y', 422)).toBeInstanceOf(Error)
  })
})

describe('authorized source request', () => {
  const hit = {
    id: 'one', source: 'a.py', source_ref: 'repository://demo/catalog/a.py',
    knowledge_type: 'code', content: 'example', score: 1,
    revision: 'old-indexed-revision', line_start: 5, line_end: 10,
  }
  it('omits an indexed revision when the materialized binding is not the same', () => {
    expect(sourcePayloadForHit(hit, 'demo/catalog', null)).toEqual({
      source_ref: hit.source_ref, repository: 'demo/catalog',
      line_start: 5, line_end: 10, context_lines: 5,
    })
    expect(sourcePayloadForHit(hit, 'demo/catalog', 'different-revision')).not.toHaveProperty('revision')
  })
  it('preserves exact revision precondition only for a matching authorized binding', () => {
    expect(sourcePayloadForHit(hit, 'demo/catalog', 'old-indexed-revision').revision).toBe('old-indexed-revision')
  })
})
