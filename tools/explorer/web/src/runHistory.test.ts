import { describe, expect, it } from 'vitest'
import { captureRun, exportHistory, importHistory, prependRun, replayGate } from './runHistory'
import type { ExplorerTarget, SearchPayload, SearchResponse, ReadinessReport } from './types'

const target: ExplorerTarget = {
  id: 'default', backend: 'solr', repository: 'demo/catalog', collection: 'demo',
  revision: 'r1', capabilities: { lexical: true }, retrieval: null,
}
const request: SearchPayload = {
  target_id: 'default', repository: 'demo/catalog', mode: 'discovery',
  knowledge_types: ['code'], limit: 5, intent: { schema_version: 2, canonical_query: 'eligibility' },
}
const response: SearchResponse = {
  backend: 'solr', status: 'ok', duration_ms: 10, metadata: {},
  results: [{
    id: 'one', source: 'eligibility.py', source_ref: 'repository://demo/catalog/eligibility.py',
    knowledge_type: 'code', score: 4, content: 'sample function', revision: 'r1',
    checksum: 'checksum', query_matches: [{ signal: {}, match_type: 'lexical',
      channels: ['lexical'], matched_values: ['eligibility'],
      source_ref: 'repository://demo/catalog/eligibility.py' }],
  }],
  telemetry: {
    mode: 'lexical', duration_ms: 10, candidates_examined: 2,
    candidates_returned: 1, returned_chars: 25, stages: [],
    fingerprints: { projection: 'fp1' },
  },
}
const readiness: ReadinessReport = {
  schema_version: 1, status: 'ready', ready: true, backend: 'solr',
  repository: 'demo/catalog', requested_revision: 'r1',
  indexed_revision: 'r1', mode: 'lexical',
  checks: { snapshot: { state: 'ready' } }, fingerprints: { projection: 'fp1' },
}
const recorded = () => captureRun('run-one', request, target, response, null, '2026-10-10T02:00:00.000Z')

describe('Versioned session run manifests', () => {
  it('captures candidate rank and provenance without storing indexed source content', () => {
    const value = recorded()
    expect(value.outcome.results[0].rank).toBe(1)
    expect(value.outcome.results[0].checksum).toBe('checksum')
    expect(value.outcome.fingerprints.projection).toBe('fp1')
    expect(exportHistory([value])).not.toContain('sample function')
    expect(importHistory(exportHistory([value]))).toEqual([value])
  })
  it('captures typed failures without manufactured telemetry', () => {
    const value = captureRun('failed', request, target, null, 'schema_mismatch')
    expect(value.outcome.state).toBe('error')
    expect(value.outcome.telemetry).toBeNull()
    expect(value.outcome.results).toHaveLength(0)
  })
  it('bounds session retention and output result count', () => {
    let values = [recorded()]
    for (let i = 0; i < 22; i++) values = prependRun(values, captureRun('item' + i, request, target, response, null))
    expect(values).toHaveLength(20)
    const many = captureRun('many', request, target, {
      ...response, results: Array.from({ length: 120 }, (_, i) => ({ ...response.results[0], id: 'hit' + i })),
    }, null)
    expect(many.outcome.results).toHaveLength(100)
    expect(many.outcome.truncated).toBe(true)
  })
  it('rejects incompatible versions, malformed provenance and duplicate IDs', () => {
    const bundle = JSON.parse(exportHistory([recorded()]))
    bundle.schema_version = 2
    expect(() => importHistory(JSON.stringify(bundle))).toThrow(/incompatível/)
    bundle.schema_version = 1
    bundle.runs.push(bundle.runs[0])
    expect(() => importHistory(JSON.stringify(bundle))).toThrow(/duplic/)
    bundle.runs.pop()
    bundle.runs[0].outcome.results[0].source_ref = '/unbound/file'
    expect(() => importHistory(JSON.stringify(bundle))).toThrow(/evidência/)
    bundle.runs[0].outcome.results[0].source_ref = 'repository://demo/catalog/eligibility.py'
    bundle.runs[0].request.intent.schema_version = 5
    expect(() => importHistory(JSON.stringify(bundle))).toThrow(/schema_version/)
    expect(() => importHistory(' '.repeat(520_000))).toThrow(/512 KB/)
  })
})
describe('Safe replay preflight', () => {
  it('requires the same authorized bound revision and verified fingerprint', () => {
    expect(replayGate(recorded(), [target], readiness).allowed).toBe(true)
    expect(replayGate(recorded(), [], readiness).allowed).toBe(false)
    expect(replayGate(recorded(), [target], { ...readiness, indexed_revision: 'r2' }).allowed).toBe(false)
    expect(replayGate(recorded(), [target], { ...readiness, fingerprints: { projection: 'other' } }).allowed).toBe(false)
    expect(replayGate(recorded(), [target], { ...readiness, fingerprints: {} }).allowed).toBe(false)
    expect(replayGate(recorded(), [{ ...target, collection: 'other' }], readiness).allowed).toBe(false)
    expect(replayGate(recorded(), [target], { ...readiness, ready: false }).allowed).toBe(false)
  })
})
