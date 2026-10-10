/** Read-only, bounded comparison of observed Explorer run manifests.
 * Retrieval evidence is never satisfaction or Ground Truth. No search here.
 */
import type { RunManifest, RecordedHit } from './runHistory'

export type ComparisonStatus = 'incompatible' | 'unverified' | 'snapshot-aligned'
export type CandidatePresence = 'shared' | 'only-a' | 'only-b'
export interface CandidateDiff {
  key: string
  identity: string
  identity_confidence: 'source-ref' | 'run-local-id'
  a: RecordedHit | null
  b: RecordedHit | null
  presence: CandidatePresence
  rank_delta: number | null
  channels_a: string[]
  channels_b: string[]
}
export interface ComparisonAssessment {
  status: ComparisonStatus
  reasons: string[]
  observations: string[]
  query_equal: boolean
  same_corpus: boolean
  same_index_binding: boolean
  same_projection: boolean | null
  same_index_fingerprint: boolean | null
  differing_channels: boolean
}
export interface ComparisonDiff {
  schema_version: 1
  arms: { a: string; b: string }
  assessment: ComparisonAssessment
  counts: { a: number; b: number; shared: number; only_a: number; only_b: number }
  candidates: CandidateDiff[]
  costs: {
    a: RunManifest['outcome']['telemetry']
    b: RunManifest['outcome']['telemetry']
  }
}
const MAX_CANDIDATES = 100

// Canonicalize maps, preserve ordered arrays (AND/OR branch order is meaningful).
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ':' + canonical((value as Record<string, unknown>)[key])).join(',') + '}'
  return JSON.stringify(value) ?? 'null'
}
function nonblank(s: string | null | undefined): boolean {
  return typeof s === 'string' && s.trim().length > 0
}
function sourceIdentity(hit: RecordedHit): { key: string; identity: string; confidence: 'source-ref' | 'run-local-id' } {
  // Candidate granularity is a source ref + revision + checksum, not just a
  // filename. A new checksum must not masquerade as an unchanged candidate.
  if (nonblank(hit.source_ref)) {
    return {
      key: canonical(['source-ref', hit.source_ref, hit.revision, hit.checksum]),
      identity: hit.source_ref!,
      confidence: 'source-ref',
    }
  }
  return { key: canonical(['run-local-id', hit.id]), identity: hit.id, confidence: 'run-local-id' }
}
function channels(hit: RecordedHit | null): string[] {
  return hit ? Array.from(new Set(hit.query_matches.flatMap(match => match.channels))).sort() : []
}
function compareFingerprint(a: RunManifest, b: RunManifest, keys: string[]): boolean | null {
  const left = a.outcome.fingerprints, right = b.outcome.fingerprints
  const present = keys.filter(key => nonblank(left[key]) && nonblank(right[key]))
  if (!present.length) return null
  return present.every(key => left[key] === right[key])
}
function sameIntent(a: RunManifest, b: RunManifest): boolean {
  return canonical(a.request.intent) === canonical(b.request.intent)
}
function comparePolicy(a: RunManifest, b: RunManifest): boolean {
  return canonical(a.target.retrieval) === canonical(b.target.retrieval)
}
function stableChannels(a: RunManifest, b: RunManifest): boolean {
  return a.outcome.telemetry?.mode !== b.outcome.telemetry?.mode
    || !comparePolicy(a, b)
}
function snapshotSignature(a: RunManifest, b: RunManifest): boolean | null {
  return compareFingerprint(a, b, ['indexing', 'indexing_fingerprint', 'snapshot', 'snapshot_fingerprint'])
}
function projectionSignature(a: RunManifest, b: RunManifest): boolean | null {
  return compareFingerprint(a, b, ['projection', 'projection_fingerprint'])
}
function reasonsFor(a: RunManifest, b: RunManifest): ComparisonAssessment {
  const reasons: string[] = []
  const observations: string[] = []
  const query_equal = sameIntent(a, b)
  if (!query_equal) reasons.push('SearchIntent difere: comparação não é controlada')
  const same_corpus = a.target.repository === b.target.repository &&
    nonblank(a.target.revision) && a.target.revision === b.target.revision
  if (!same_corpus) reasons.push('Corpus/repository revision não comprovadamente idênticos')
  const same_index_binding = a.target.backend === b.target.backend
    && a.target.collection === b.target.collection && a.target.repository === b.target.repository
  if (!same_index_binding) reasons.push('Backend/collection binding diferentes: snapshot único não demonstrado')
  if (a.request.mode !== b.request.mode
    || canonical(a.request.knowledge_types) !== canonical(b.request.knowledge_types)) {
    reasons.push('Search scope mode ou knowledge types diferem')
  }
  if (a.request.limit !== b.request.limit) reasons.push('Top-K/result limit difere')
  const budgetA = a.target.retrieval?.candidate_budget
  const budgetB = b.target.retrieval?.candidate_budget
  if (canonical(budgetA) !== canonical(budgetB)) reasons.push('Candidate budget difere ou não é comparável')
  if (a.outcome.state !== 'ok' || b.outcome.state !== 'ok') {
    reasons.push('Uma ou mais execuções falharam; nenhum vazio significa ausência de evidência')
  }
  if (a.outcome.truncated || b.outcome.truncated) {
    reasons.push('Um manifest foi truncado: contagens são apenas dos candidatos armazenados')
  }
  const same_projection = projectionSignature(a, b)
  const same_index_fingerprint = snapshotSignature(a, b)
  if (same_projection === false) reasons.push('Fingerprint de Projection divergente')
  if (same_index_fingerprint === false) reasons.push('Fingerprint de indexing/snapshot divergente')
  if (same_projection === null) observations.push('Projection fingerprint não comparável')
  if (same_index_fingerprint === null) observations.push('Index/snapshot fingerprint não comparável')
  for (const key of new Set([...Object.keys(a.outcome.fingerprints), ...Object.keys(b.outcome.fingerprints)])) {
    if (!/(?:embedding|serializer|model|dimensions)/i.test(key)) continue
    const left = a.outcome.fingerprints[key], right = b.outcome.fingerprints[key]
    if (!nonblank(left) || !nonblank(right)) observations.push('Identidade vetorial/serializer não comprovada: ' + key)
    else if (left !== right) reasons.push('Identidade vetorial/serializer divergente: ' + key)
  }
  const differing_channels = stableChannels(a, b)
  if (differing_channels) observations.push('Política/modo efetivo variam: experimento de estratégia, não equivalência de ranking')
  if (!a.outcome.telemetry || !b.outcome.telemetry) observations.push('Telemetry ausente em uma ou mais execuções; custos não estimados')
  if (!nonblank(a.target.revision) || !nonblank(b.target.revision)) observations.push('Revisão não vinculada: sem prova de mesma fonte')
  const incompatible = reasons.length > 0
  const proven = same_index_fingerprint === true && same_projection === true
  return {
    status: incompatible ? 'incompatible' : proven ? 'snapshot-aligned' : 'unverified',
    reasons, observations, query_equal, same_corpus, same_index_binding,
    same_projection, same_index_fingerprint, differing_channels,
  }
}

export function compareRuns(a: RunManifest, b: RunManifest): ComparisonDiff {
  const assessment = reasonsFor(a, b)
  const left = a.outcome.results.slice(0, MAX_CANDIDATES)
  const right = b.outcome.results.slice(0, MAX_CANDIDATES)
  const mapA = new Map(left.map(hit => [sourceIdentity(hit).key, hit]))
  const mapB = new Map(right.map(hit => [sourceIdentity(hit).key, hit]))
  const keys = Array.from(new Set([...mapA.keys(), ...mapB.keys()]))
  const candidates: CandidateDiff[] = keys.map(key => {
    const hitA = mapA.get(key) ?? null
    const hitB = mapB.get(key) ?? null
    const who = sourceIdentity((hitA ?? hitB)!)
    const shared = hitA !== null && hitB !== null
    return {
      key, identity: who.identity, identity_confidence: who.confidence,
      a: hitA, b: hitB,
      presence: shared ? 'shared' : hitA ? 'only-a' : 'only-b',
      rank_delta: shared && who.confidence === 'source-ref' && assessment.status === 'snapshot-aligned'
        ? hitB.rank - hitA.rank : null,
      channels_a: channels(hitA), channels_b: channels(hitB),
    }
  })
  const shared = candidates.filter(x => x.presence === 'shared').length
  return {
    schema_version: 1,
    arms: { a: a.id, b: b.id }, assessment,
    counts: {
      a: left.length, b: right.length, shared,
      only_a: candidates.filter(x => x.presence === 'only-a').length,
      only_b: candidates.filter(x => x.presence === 'only-b').length,
    }, candidates, costs: { a: a.outcome.telemetry, b: b.outcome.telemetry },
  }
}

/** Export observed diff only. No backend URLs, request credentials or source bodies. */
export function exportComparison(diff: ComparisonDiff): string {
  return JSON.stringify({ kind: 'fetchcore-retrieval-comparison', ...diff }, null, 2) + '\n'
}
