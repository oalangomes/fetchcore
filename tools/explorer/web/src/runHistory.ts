/** Versioned, bounded, session-memory only observed retrieval run envelopes.
 * No indexed source bodies, raw backend metadata, host workspaces, tokens,
 * backend URLs, or passwords are retained. A manifest is evidence, not a verdict.
 */
import { parseValidatedIntent } from './intentBuilder'
import type { ExplorerTarget, ReadinessReport, SearchPayload, SearchResponse, QueryMatch } from './types'

export const HISTORY_VERSION = 1 as const
export const HISTORY_LIMIT = 20
export const HISTORY_IMPORT_LIMIT = 512_000
const RESULT_LIMIT = 100
const MATCH_LIMIT = 12
const TEXT_LIMIT = 2048

export interface RecordedMatch {
  match_type: string
  channels: string[]
  matched_values: string[]
  source_ref: string | null
}
export interface RecordedHit {
  rank: number
  id: string
  source_ref: string | null
  revision: string | null
  checksum: string | null
  knowledge_type: string
  score: number
  query_matches: RecordedMatch[]
  observations: { category: string; subtype: string }[]
}
export interface RunManifest {
  schema_version: typeof HISTORY_VERSION
  id: string
  recorded_at: string
  request: SearchPayload
  target: {
    id: string
    repository: string
    backend: string
    collection: string | null
    revision: string | null
    retrieval: Record<string, unknown> | null
  }
  outcome: {
    state: 'ok' | 'error'
    category: string | null
    backend: string | null
    total_candidates: number | null
    truncated: boolean
    results: RecordedHit[]
    telemetry: {
      mode: string
      duration_ms: number
      candidates_examined: number
      candidates_returned: number
      returned_chars: number
      time_to_first_candidate_ms: number | null
      stages: { name: string; duration_ms: number; candidates_in: number | null; candidates_out: number | null }[]
    } | null
    fingerprints: Record<string, string>
  }
}
export interface HistoryBundle { schema_version: typeof HISTORY_VERSION; kind: 'fetchcore-explorer-runs'; runs: RunManifest[] }
export type ReplayGate = { allowed: boolean; reasons: string[] }

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function text(value: unknown, maximum = TEXT_LIMIT): string {
  return typeof value === 'string' ? value.slice(0, maximum) : ''
}
function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
function optionalNumber(value: unknown): number | null { return finite(value) }
function reference(value: unknown): string | null {
  // Never export arbitrary backend source-ref values that might be absolute host paths.
  if (typeof value !== 'string' || value.length > 2048 || !value.startsWith('repository://')) return null
  return value
}
const FINGERPRINT_KEYS = /^(?:indexing|projection|embedding|serializer|model|dimensions|query|retrieval)(?:[_.-][a-z0-9_.-]+)?$/i
function fingerprints(value: unknown): Record<string, string> {
  if (!isObject(value)) return {}
  return Object.fromEntries(Object.entries(value)
    .filter(([key, entry]) => FINGERPRINT_KEYS.test(key) && typeof entry === 'string'
      && entry.length > 0 && entry.length <= 256 && !/[\s/:\\]/.test(entry))
    .slice(0, 24)) as Record<string, string>
}
function policy(value: unknown): Record<string, unknown> | null {
  // The operator-configured retrieval policy is already exposed in /api/config;
  // capture only known public routing switches, never a provider URL/key.
  if (!isObject(value)) return null
  const allowed = ['mode', 'lexical', 'dense', 'hybrid', 'reranking', 'candidate_budget']
  const result: Record<string, unknown> = {}
  for (const key of allowed) {
    if (!(key in value)) continue
    // Backend policy config could contain provider secrets/URLs; store a stable
    // semantic digest? No native stable SHA required: redact nested values entirely.
    if (typeof value[key] === 'boolean' || typeof value[key] === 'string' && /^[a-z_-]{1,32}$/.test(value[key])) {
      result[key] = value[key]
    } else if (typeof value[key] === 'number' && Number.isFinite(value[key])) {
      result[key] = value[key]
    }
  }
  return result
}
function matches(value: QueryMatch[] | undefined): RecordedMatch[] {
  return (value ?? []).slice(0, MATCH_LIMIT).map(match => ({
    match_type: text(match.match_type, 80),
    channels: (match.channels ?? []).slice(0, 8).map(value => text(value, 80)),
    matched_values: (match.matched_values ?? []).slice(0, 8).map(value => text(value, 128)),
    source_ref: reference(match.source_ref),
  }))
}

function captureResponse(response: SearchResponse | null, errorCategory: string | null): RunManifest['outcome'] {
  if (response === null) return {
    state: 'error', category: text(errorCategory, 100) || 'retrieval_failed',
    backend: null, total_candidates: null, truncated: false, results: [], telemetry: null, fingerprints: {},
  }
  const telemetry = response.telemetry
  const evidence = response.results.slice(0, RESULT_LIMIT).map((hit, index) => ({
    rank: index + 1,
    id: text(hit.id, 256),
    source_ref: reference(hit.source_ref),
    revision: text(hit.revision, 256) || null,
    checksum: text(hit.checksum, 256) || null,
    knowledge_type: text(hit.knowledge_type, 100),
    score: finite(hit.score) ?? 0,
    query_matches: matches(hit.query_matches),
    observations: (hit.observations ?? []).slice(0, MATCH_LIMIT).map(obs => ({
      category: text(obs.category, 80),
      subtype: text(obs.subtype, 80),
    })),
  }))
  return {
    state: 'ok',
    category: null,
    backend: text(response.backend, 80) || null,
    total_candidates: response.results.length,
    truncated: response.results.length > RESULT_LIMIT,
    results: evidence,
    telemetry: telemetry ? {
      mode: text(telemetry.mode, 100),
      duration_ms: finite(telemetry.duration_ms) ?? 0,
      candidates_examined: finite(telemetry.candidates_examined) ?? 0,
      candidates_returned: finite(telemetry.candidates_returned) ?? 0,
      returned_chars: finite(telemetry.returned_chars) ?? 0,
      time_to_first_candidate_ms: optionalNumber(telemetry.time_to_first_candidate_ms),
      stages: (telemetry.stages ?? []).slice(0, 32).map(stage => ({
        name: text(stage.name, 80),
        duration_ms: finite(stage.duration_ms) ?? 0,
        candidates_in: optionalNumber(stage.candidates_in),
        candidates_out: optionalNumber(stage.candidates_out),
      })),
    } : null,
    fingerprints: fingerprints(telemetry?.fingerprints),
  }
}

export function captureRun(
  id: string, request: SearchPayload, target: ExplorerTarget,
  response: SearchResponse | null, errorCategory: string | null,
  recordedAt = new Date().toISOString(),
): RunManifest {
  // Fresh copies prevent imported/edited drafts from changing the recorded run.
  const intent = parseValidatedIntent(JSON.stringify(request.intent))
  if (!id.trim() || id.length > 96 || Number.isNaN(Date.parse(recordedAt))) throw new Error('Run ID/time inválidos')
  if (request.target_id !== target.id || request.repository !== target.repository) throw new Error('Target não corresponde à requisição')
  if (!Number.isInteger(request.limit) || request.limit < 1 || request.limit > 100) throw new Error('Limit inválido')
  const manifest: RunManifest = {
    schema_version: HISTORY_VERSION,
    id,
    recorded_at: recordedAt,
    request: {
      repository: target.repository, target_id: target.id,
      mode: request.mode, limit: request.limit,
      knowledge_types: [...request.knowledge_types], intent,
    },
    target: {
      id: target.id, repository: target.repository, backend: target.backend,
      collection: target.collection, revision: target.revision, retrieval: policy(target.retrieval),
    },
    outcome: captureResponse(response, errorCategory),
  }
  // A bounded history is a finite context budget, not a source document archive.
  while (JSON.stringify(manifest).length > 24_000 && manifest.outcome.results.length) {
    manifest.outcome.results.pop()
    manifest.outcome.truncated = true
  }
  if (JSON.stringify(manifest).length > 24_000) {
    throw new Error('Manifest excessivo: reduza o tamanho da query')
  }
  return manifest
}

export function prependRun(history: RunManifest[], run: RunManifest): RunManifest[] {
  return [run, ...history.filter(existing => existing.id !== run.id)].slice(0, HISTORY_LIMIT)
}
function formatBundle(runs: RunManifest[]): HistoryBundle {
  return { schema_version: HISTORY_VERSION, kind: 'fetchcore-explorer-runs', runs: runs.slice(0, HISTORY_LIMIT) }
}
export function exportHistory(runs: RunManifest[]): string {
  const payload = JSON.stringify(formatBundle(runs), null, 2)
  if (payload.length > HISTORY_IMPORT_LIMIT) throw new Error('Histórico ultrapassa 512 KB: exporte menos execuções')
  return payload + '\n'
}
export function importHistory(raw: string): RunManifest[] {
  if (raw.length > HISTORY_IMPORT_LIMIT) throw new Error('Importação excede 512 KB')
  const bundle: unknown = JSON.parse(raw)
  if (!isObject(bundle) || bundle.schema_version !== HISTORY_VERSION || bundle.kind !== 'fetchcore-explorer-runs'
    || !Array.isArray(bundle.runs) || bundle.runs.length > HISTORY_LIMIT
    || Object.keys(bundle).some(key => !['schema_version', 'kind', 'runs'].includes(key))) {
    throw new Error('Bundle incompatível: schema/version/limite')
  }
  const ids = new Set<string>()
  const imported: RunManifest[] = []
  for (const entry of bundle.runs) {
    if (!isObject(entry) || entry.schema_version !== HISTORY_VERSION ||
      Object.keys(entry).some(key => !['schema_version','id','recorded_at','request','target','outcome'].includes(key))) {
      throw new Error('Manifest v1 inválido')
    }
    const rawRequest = entry.request
    const rawTarget = entry.target
    const rawOutcome = entry.outcome
    if (!isObject(rawRequest) || !isObject(rawTarget) || !isObject(rawOutcome)
        || typeof entry.id !== 'string' || entry.id.length > 96 || !entry.id.trim()
        || ids.has(entry.id) || typeof entry.recorded_at !== 'string'
        || Number.isNaN(Date.parse(entry.recorded_at))
        || typeof rawRequest.repository !== 'string'
        || typeof rawTarget.repository !== 'string'
        || typeof rawTarget.id !== 'string'
        || rawRequest.repository !== rawTarget.repository
        || rawRequest.target_id !== rawTarget.id
        || typeof rawTarget.backend !== 'string'
        || !(rawTarget.collection === null || typeof rawTarget.collection === 'string')
        || !(rawTarget.revision === null || typeof rawTarget.revision === 'string')
        || !['discovery','targeted','repository_source'].includes(String(rawRequest.mode))
        || !Number.isInteger(rawRequest.limit) || (rawRequest.limit as number) < 1 || (rawRequest.limit as number) > 100
        || !Array.isArray(rawRequest.knowledge_types) || rawRequest.knowledge_types.length > 30
        || rawRequest.knowledge_types.some(value => typeof value !== 'string' || value.length > 128)) {
      throw new Error('Manifest incompleto ou destino não verificável')
    }
    ids.add(entry.id)
    parseValidatedIntent(JSON.stringify(rawRequest.intent))
    if (rawOutcome.state !== 'ok' && rawOutcome.state !== 'error') throw new Error('Outcome desconhecido')
    if (!Array.isArray(rawOutcome.results) || rawOutcome.results.length > RESULT_LIMIT) throw new Error('Resultados fora do limite')
    if (rawOutcome.results.some(result => !isObject(result) || !Number.isInteger(result.rank)
      || typeof result.id !== 'string' || !(result.source_ref === null || reference(result.source_ref) === result.source_ref)
      || !Array.isArray(result.query_matches) || result.query_matches.length > MATCH_LIMIT
      || !Array.isArray(result.observations) || result.observations.length > MATCH_LIMIT)) {
      throw new Error('Identidades de evidência inválidas')
    }
    if (!isObject(rawOutcome.fingerprints) || Object.entries(rawOutcome.fingerprints)
      .some(([key, value]) => !FINGERPRINT_KEYS.test(key) || typeof value !== 'string' || value.length > 256)) {
      throw new Error('Fingerprint inválido')
    }
    if (!(rawTarget.retrieval === null || isObject(rawTarget.retrieval))) throw new Error('Retrieval policy inválida')
    if (!['ok', 'error'].includes(String(rawOutcome.state))
      || !(rawOutcome.category === null || typeof rawOutcome.category === 'string')
      || !(rawOutcome.backend === null || typeof rawOutcome.backend === 'string')
      || !isObject(rawOutcome.fingerprints)
      || !(rawOutcome.telemetry === null || isObject(rawOutcome.telemetry))) {
      throw new Error('Outcome inválido')
    }
    // Normalize every imported field. Never reflect unknown nested metadata,
    // provider settings, absolute paths or injected source body in a re-export.
    const stored: RunManifest = {
      schema_version: HISTORY_VERSION,
      id: entry.id,
      recorded_at: entry.recorded_at,
      request: {
        repository: rawTarget.repository,
        target_id: rawTarget.id,
        mode: rawRequest.mode as SearchPayload['mode'],
        limit: rawRequest.limit as number,
        knowledge_types: [...rawRequest.knowledge_types] as string[],
        intent: parseValidatedIntent(JSON.stringify(rawRequest.intent)),
      },
      target: {
        id: rawTarget.id,
        repository: rawTarget.repository,
        backend: text(rawTarget.backend, 80),
        collection: rawTarget.collection === null ? null : text(rawTarget.collection, 256),
        revision: rawTarget.revision === null ? null : text(rawTarget.revision, 256),
        retrieval: policy(rawTarget.retrieval),
      },
      outcome: {
        state: rawOutcome.state as 'ok' | 'error',
        category: rawOutcome.category === null ? null : text(rawOutcome.category, 100),
        backend: rawOutcome.backend === null ? null : text(rawOutcome.backend, 80),
        total_candidates: optionalNumber(rawOutcome.total_candidates),
        truncated: rawOutcome.truncated === true,
        results: rawOutcome.results.map((result: unknown, rank: number) => {
          const hit = result as Record<string, unknown>
          return {
            rank: rank + 1, id: text(hit.id, 256), source_ref: reference(hit.source_ref),
            revision: text(hit.revision, 256) || null, checksum: text(hit.checksum, 256) || null,
            knowledge_type: text(hit.knowledge_type, 100), score: finite(hit.score) ?? 0,
            query_matches: (hit.query_matches as unknown[]).map(item => {
              const match = isObject(item) ? item : {}
              return {
                match_type: text(match.match_type, 80),
                channels: Array.isArray(match.channels) ? match.channels.slice(0, 8).map(x => text(x, 80)) : [],
                matched_values: Array.isArray(match.matched_values)
                  ? match.matched_values.slice(0, 8).map(x => text(x, 128)) : [],
                source_ref: reference(match.source_ref),
              }
            }),
            observations: (hit.observations as unknown[]).map(item => ({
              category: isObject(item) ? text(item.category, 80) : '',
              subtype: isObject(item) ? text(item.subtype, 80) : '',
            })),
          }
        }),
        telemetry: rawOutcome.telemetry === null ? null : (() => {
          const tel = rawOutcome.telemetry as Record<string, unknown>
          return {
            mode: text(tel.mode, 80), duration_ms: finite(tel.duration_ms) ?? 0,
            candidates_examined: finite(tel.candidates_examined) ?? 0,
            candidates_returned: finite(tel.candidates_returned) ?? 0,
            returned_chars: finite(tel.returned_chars) ?? 0,
            time_to_first_candidate_ms: optionalNumber(tel.time_to_first_candidate_ms),
            stages: Array.isArray(tel.stages) ? tel.stages.slice(0, 32).map(item => {
              const stage = isObject(item) ? item : {}
              return {
                name: text(stage.name, 80), duration_ms: finite(stage.duration_ms) ?? 0,
                candidates_in: optionalNumber(stage.candidates_in),
                candidates_out: optionalNumber(stage.candidates_out),
              }
            }) : [],
          }
        })(),
        fingerprints: fingerprints(rawOutcome.fingerprints),
      },
    }
    if (JSON.stringify(stored).length > 24_000) throw new Error('Manifest importado fora do budget')
    imported.push(stored)
  }
  return imported
}

/** Strong replay: no fingerprint/revision guesswork and no auto fallback. */
export function replayGate(run: RunManifest, targets: ExplorerTarget[], readiness: ReadinessReport): ReplayGate {
  const reasons: string[] = []
  const target = targets.find(item => item.id === run.target.id)
  if (!target) return { allowed: false, reasons: ['Destino não autorizado ou removido'] }
  if (target.repository !== run.target.repository || target.backend !== run.target.backend
    || target.collection !== run.target.collection
    || JSON.stringify(policy(target.retrieval)) !== JSON.stringify(run.target.retrieval)) {
    reasons.push('Backend, coleção, repositório ou política alterados')
  }
  if (target.revision !== run.target.revision) reasons.push('Binding de revisão mudou')
  if (run.target.revision == null) reasons.push('Revisão original não vinculada; não há garantia de replay equivalente')
  if (!readiness.ready || readiness.status !== 'ready') reasons.push('Índice não está comprovadamente ready')
  if (readiness.repository !== run.target.repository
    || readiness.backend !== run.target.backend
    || readiness.requested_revision !== run.target.revision
    || readiness.indexed_revision !== run.target.revision) {
    reasons.push('Snapshot/revisão atual difere da execução gravada')
  }
  const stored = run.outcome.fingerprints
  const current = readiness.fingerprints
  const verified = Object.entries(stored).filter(([key]) =>
    Object.prototype.hasOwnProperty.call(current, key) && typeof current[key] === 'string' && current[key] !== '')
  if (!Object.keys(stored).length || !verified.length) reasons.push('Fingerprint comparável não disponível (sem prova de equivalência)')
  for (const [key, value] of Object.entries(stored)) {
    if (!(key in current)) reasons.push('Fingerprint não verificável: ' + key)
    else if (current[key] !== value) reasons.push('Fingerprint divergente: ' + key)
  }
  if (run.outcome.state !== 'ok') reasons.push('Execução original falhou: não é baseline comparável')
  return { allowed: reasons.length === 0, reasons }
}
