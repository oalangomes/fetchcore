import { afterEach, describe, expect, it, vi } from 'vitest'
import { exportArtifact, observed, validateArtifacts, type IndexArtifact } from './indexArtifacts'
import { readFileSync } from 'node:fs'
import path from 'node:path'
const fixture = JSON.parse(readFileSync(path.resolve('../fixtures/index-runs/noop.json'), 'utf8'))

afterEach(() => vi.unstubAllGlobals())
describe('index observation transport', () => {
  it('preserves unknown and observed zero separately', () => {
    expect(observed(undefined)).toBe('desconhecido')
    expect(observed(null)).toBe('desconhecido')
    expect(observed(0)).toBe('0')
    expect(observed(false)).toBe('false')
  })
  it('exports all evidence without recomputing it', () => {
    expect(JSON.parse(exportArtifact(fixture as IndexArtifact))).toEqual(fixture)
  })
  it('preserves application-validated failure diagnostics on export', () => {
    const failure: IndexArtifact = {
      ...fixture,
      command: {
        operation: 'rebuild', status: 'failed',
        error: { category: 'embedding_failed', message: 'Indexing operation failed', diagnostics: { http_status: 400, input_count: 32 } },
      },
    }
    expect(JSON.parse(exportArtifact(failure))).toEqual(failure)
  })
  it('delegates validation to application service without executing indexing', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ artifacts: [fixture], comparison: null }) })
    vi.stubGlobal('fetch', fetch)
    expect((await validateArtifacts([JSON.stringify(fixture)])).artifacts).toEqual([fixture])
    expect(fetch).toHaveBeenCalledWith('/api/indexing/artifacts', expect.objectContaining({ method: 'POST' }))
  })
  it('surfaces fail-closed rejection', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: { message: 'Unsupported schema' } }) }))
    await expect(validateArtifacts(['{}'])).rejects.toThrow('Unsupported schema')
  })
})
