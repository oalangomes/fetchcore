/** Read-only typed coverage diagnostics for the bounded returned candidate window.
 * No lexical similarity, score threshold, or inferred semantic satisfaction.
 */
import type { QueryMatch, SearchHit, SearchPayload, SearchResponse } from './types'

export type CoverageState = 'observed' | 'not-observed' | 'not-evaluated' | 'unsupported' | 'truncated'
export type CoverageRole = 'canonical' | 'anchor' | 'expression-term' | 'expression-anchor' |
  'exact-term' | 'semantic-term' | 'signal-required' | 'signal-optional' | 'hint' | 'expansion'
export interface CoverageItem {
  id: string
  label: string
  role: CoverageRole
  value: string
  required: boolean
  path: string
  origin?: string
  kind?: string
  classification?: string
  signalType?: string
}
export interface CoverageColumn {
  key: string
  source_ref: string
  revision: string | null
  checksum: string | null
  knowledge_type: string
  hitIds: string[]
  primaryId: string
  duplicateCount: number
  line_start: number | null
  line_end: number | null
}
export interface CoverageCell {
  state: Exclude<CoverageState, 'truncated'>
  matches: QueryMatch[]
  channels: string[]
  observations: number
}
export interface CoverageRow extends CoverageItem {
  cells: CoverageCell[]
  summary: CoverageState
  observedCandidates: number
}
export interface CoverageBranch {
  path: string
  operator: 'all_of' | 'any_of'
  descendants: string[]
  observedChildren: number
  totalChildren: number
}
export interface CoverageMap {
  schema_version: 1
  rows: CoverageRow[]
  columns: CoverageColumn[]
  branches: CoverageBranch[]
  diagnostics: {
    totalReturned: number
    visibleCandidates: number
    totalSignals: number
    visibleSignals: number
    duplicateCandidates: number
    resultWindowBounded: boolean
    matrixTruncated: boolean
    noQueryMatchDiagnostics: boolean
    unsupportedTranslationReported: boolean
    effectiveMode: string | null
    fingerprints: Record<string, string>
  }
}
const MAX_COLUMNS = 24
const MAX_ROWS = 48
const MAX_MATCHES = 12
const MAX_NODES = 256
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function value(v: unknown): string { return typeof v === 'string' ? v : '' }
function nonblank(v: unknown): boolean { return typeof v === 'string' && v.trim().length > 0 }
function codeId(...parts: string[]): string { return parts.join(':') }
function equality(a: string, b: string): boolean { return a === b && nonblank(a) }
function isMatchingSignal(row: CoverageItem, match: QueryMatch): boolean {
  const signal = match.signal
  if (!object(signal) || !equality(row.value, value(signal.value)) || !nonblank(match.source_ref)) return false
  // For typed anchors, path/identifier/reference classes must explicitly agree.
  // Never treat a generic lexical match as proof of a required structural anchor.
  if (row.role === 'anchor' || row.role === 'expression-anchor') {
    const allowed: Record<string, string[]> = {
      path: ['path'], identifier: ['exact_identifier'],
      qualified_symbol: ['exact_identifier'],
      route: ['direct_reference'],
      source_ref: ['direct_reference'],
    }
    return (allowed[row.kind ?? ''] ?? []).includes(value(signal.classification)) &&
      ['exact', 'reference'].includes(value(signal.type))
  }
  if (row.role === 'signal-required' || row.role === 'signal-optional') {
    return value(signal.classification) === row.classification &&
      value(signal.type) === row.signalType
  }
  if (row.role === 'hint' || row.role === 'expansion') return false
  if (row.role === 'canonical') return false // canonical_query does not promise a concrete query signal
  return ['exact', 'semantic', 'reference'].includes(value(signal.type))
}
function sourceKey(hit: SearchHit): string {
  // Revision/checksum are part of the canonical identity. A ref with different
  // revisions/checksums represents distinct observations, not duplicates.
  return JSON.stringify([hit.source_ref || hit.id, hit.revision ?? null, hit.checksum ?? null])
}
function collectedColumns(results: SearchHit[]): CoverageColumn[] {
  const byKey = new Map<string, CoverageColumn>()
  for (const hit of results) {
    const key = sourceKey(hit)
    const existing = byKey.get(key)
    if (existing) {
      existing.hitIds.push(hit.id)
      existing.duplicateCount++
    } else {
      byKey.set(key, {
        key, source_ref: hit.source_ref || hit.id,
        revision: hit.revision ?? null, checksum: hit.checksum ?? null,
        knowledge_type: hit.knowledge_type, hitIds: [hit.id],
        primaryId: hit.id, duplicateCount: 0,
        line_start: hit.line_start ?? null, line_end: hit.line_end ?? null,
      })
    }
  }
  return [...byKey.values()]
}
function declaredIntent(intent: Record<string, unknown>) {
  const items: CoverageItem[] = []
  const branches: CoverageBranch[] = []
  const add = (role: CoverageRole, v: unknown, path: string, required: boolean,
    more: Partial<CoverageItem> = {}) => {
    if (!nonblank(v)) return
    items.push({ id: codeId(role, path), role, path, label: role, value: v as string, required, ...more })
  }
  add('canonical', intent.canonical_query, 'canonical_query', false)
  const anchors = Array.isArray(intent.anchors) ? intent.anchors : []
  anchors.slice(0, MAX_NODES).forEach((anchor, i) => {
    if (object(anchor)) add('anchor', anchor.value, 'anchors.' + i, true, {
      kind: value(anchor.kind), origin: value(anchor.origin),
    })
  })
  const traverse = (node: unknown, path: string, depth: number): string[] => {
    if (!object(node) || depth > 16 || items.length >= MAX_NODES) return []
    if (node.type === 'term' || node.type === 'anchor') {
      const role = node.type === 'term' ? 'expression-term' : 'expression-anchor'
      add(role, node.value, path, true, {
        origin: value(node.origin), kind: value(node.kind),
      })
      return [codeId(role, path)]
    }
    if (node.type !== 'all_of' && node.type !== 'any_of') return []
    const descendants = (Array.isArray(node.children) ? node.children.slice(0, MAX_NODES) : [])
      .flatMap((child, i) => traverse(child, path + '.' + i, depth + 1))
    branches.push({ path, operator: node.type, descendants, observedChildren: 0, totalChildren: descendants.length })
    return descendants
  }
  if (intent.expression != null) traverse(intent.expression, 'expression', 0)
  for (const [key, role, required] of [
    ['exact_terms', 'exact-term', true],
    ['semantic_terms', 'semantic-term', false],
    ['hints', 'hint', false],
  ] as const) {
    const values = Array.isArray(intent[key]) ? intent[key] as unknown[] : []
    values.slice(0, MAX_NODES).forEach((entry, i) => add(role, entry, key + '.' + i, required))
  }
  const expansions = Array.isArray(intent.expansions) ? intent.expansions : []
  expansions.slice(0, MAX_NODES).forEach((exp, i) => {
    if (object(exp)) add('expansion', exp.value, 'expansions.' + i, false, { origin: value(exp.origin) })
  })
  const signals = Array.isArray(intent.signals) ? intent.signals : []
  signals.slice(0, MAX_NODES).forEach((s, i) => {
    if (!object(s)) return
    const required = s.required === true
    add(required ? 'signal-required' : 'signal-optional', s.value, 'signals.' + i, required, {
      classification: value(s.classification), signalType: value(s.type),
      origin: value(s.origin),
    })
  })
  return { items, branches }
}
function explicitlyUnsupported(response: SearchResponse, row: CoverageItem): boolean {
  // Only a backend-declared, structured diagnostic can report unsupported.
  // No heuristic derived from no matches or a low relevance score is allowed.
  const info = response.metadata?.query_translation
  if (!object(info) || !Array.isArray(info.unsupported)) return false
  return info.unsupported.some(item => object(item) &&
    item.path === row.path && item.reason === 'unsupported')
}
function channelList(matches: QueryMatch[]): string[] {
  return Array.from(new Set(matches.flatMap(item => Array.isArray(item.channels) ? item.channels : []))).sort()
}

export function buildCoverageMap(request: SearchPayload, response: SearchResponse): CoverageMap {
  const declared = declaredIntent(request.intent)
  const allColumns = collectedColumns(response.results)
  const columns = allColumns.slice(0, MAX_COLUMNS)
  const visibleResults = new Map(response.results.map(hit => [hit.id, hit]))
  const atLimit = response.results.length >= request.limit
  const matrixTruncated = allColumns.length > MAX_COLUMNS || declared.items.length > MAX_ROWS
  const noQueryMatchDiagnostics = response.results.every(hit => !hit.query_matches?.length)
  const rows: CoverageRow[] = declared.items.slice(0, MAX_ROWS).map(item => {
    const unsupported = explicitlyUnsupported(response, item)
    const cells: CoverageCell[] = columns.map(column => {
      const hits = column.hitIds.map(id => visibleResults.get(id)).filter((hit): hit is SearchHit => !!hit)
      const all = hits.flatMap(hit => hit.query_matches ?? []).slice(0, MAX_MATCHES * 2)
      const found = all.filter(match => isMatchingSignal(item, match)).slice(0, MAX_MATCHES)
      const observations = hits.reduce((sum, hit) => sum + (hit.observations?.length ?? 0), 0)
      return {
        state: unsupported ? 'unsupported' : found.length ? 'observed'
          : item.role === 'canonical' || item.role === 'hint' || item.role === 'expansion' || !all.length
            ? 'not-evaluated' : 'not-observed',
        matches: found, channels: channelList(found), observations,
      }
    })
    const observedCandidates = cells.filter(c => c.state === 'observed').length
    const summary: CoverageState = unsupported ? 'unsupported' : observedCandidates
      ? 'observed' : matrixTruncated || atLimit ? 'truncated'
        : item.role === 'canonical' || item.role === 'hint' || item.role === 'expansion' || noQueryMatchDiagnostics
          ? 'not-evaluated' : 'not-observed'
    return { ...item, cells, observedCandidates, summary }
  })
  const lookup = new Map(rows.map(row => [row.id, row]))
  const branches = declared.branches.map(branch => ({
    ...branch, observedChildren: branch.descendants.filter(id => lookup.get(id)?.summary === 'observed').length,
  }))
  return {
    schema_version: 1, rows, columns, branches,
    diagnostics: {
      totalReturned: response.results.length, visibleCandidates: columns.length,
      totalSignals: declared.items.length, visibleSignals: rows.length,
      duplicateCandidates: allColumns.reduce((count, column) => count + column.duplicateCount, 0),
      resultWindowBounded: atLimit, matrixTruncated, noQueryMatchDiagnostics,
      unsupportedTranslationReported: rows.some(row => row.summary === 'unsupported'),
      effectiveMode: response.telemetry?.mode ?? null,
      fingerprints: response.telemetry?.fingerprints ?? {},
    },
  }
}
