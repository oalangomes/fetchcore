import { afterEach, describe, expect, it, vi } from 'vitest'
import { getConfig, parseIntent, search, ApiError } from './api'

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
