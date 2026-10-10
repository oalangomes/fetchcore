export interface ExplorerTarget {
  id: string
  repository: string
  backend: string
  collection: string | null
  revision: string | null
  capabilities: Record<string, boolean>
  retrieval: Record<string, unknown> | null
}

export interface ExplorerConfig {
  repository: string
  backend: string
  revision: string | null
  capabilities: Record<string, boolean>
  retrieval: Record<string, unknown> | null
  search_modes: string[]
  max_results: number
  default_target?: string
  targets?: ExplorerTarget[]
}

export interface SearchPayload {
  target_id?: string
  repository: string
  limit: number
  mode: 'discovery' | 'targeted' | 'repository_source'
  knowledge_types: string[]
  intent: Record<string, unknown>
}

export interface QueryMatch {
  signal: Record<string, unknown>
  match_type: string
  channels: string[]
  matched_values: string[]
  source_ref: string
  strength?: number
  normalized_contribution?: number
  provenance?: string
  derivation?: string[]
}

export interface SearchHit {
  id: string
  source: string
  source_ref: string
  knowledge_type: string
  content: string
  score: number
  query_alignment_score?: number
  line_start?: number
  line_end?: number
  revision?: string
  checksum?: string
  source_type?: string
  trust_level?: string
  matched_terms?: string[]
  reasons?: string[]
  query_matches?: QueryMatch[]
  observations?: Array<Record<string, unknown>>
  relationships?: Array<Record<string, unknown>>
  capabilities?: string[]
  metadata?: Record<string, unknown>
  [key: string]: unknown
}

export interface StageTelemetry {
  name: string
  duration_ms: number
  candidates_in?: number
  candidates_out?: number
  metadata?: Record<string, unknown>
}

export interface RetrievalTelemetry {
  mode: string
  duration_ms: number
  candidates_examined: number
  candidates_returned: number
  returned_chars: number
  time_to_first_candidate_ms?: number
  stages: StageTelemetry[]
  fingerprints?: Record<string, string>
  cache?: Record<string, unknown>
  budget?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface SearchResponse {
  backend: string
  status: string
  results: SearchHit[]
  duration_ms: number
  metadata: Record<string, unknown>
  telemetry?: RetrievalTelemetry
}

export interface SourceResult {
  repository: string
  source_ref: string
  path: string
  revision: string | null
  content: string
  line_start: number
  line_end: number
  total_lines: number
  truncated: boolean
}

export interface ReadinessReport {
  schema_version: number
  status: string
  ready: boolean
  backend: string
  repository: string | null
  requested_revision: string | null
  indexed_revision: string | null
  mode: string
  checks: Record<string, { state: string; category?: string }>
  fingerprints: Record<string, string | null>
}
