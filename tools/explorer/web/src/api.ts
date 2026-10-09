import type { ExplorerConfig, SearchHit, SearchPayload, SearchResponse, SourceResult } from './types'

export class ApiError extends Error {
  constructor(message: string, readonly category: string, readonly status: number) {
    super(message)
  }
}

async function call<T>(path: string, payload?: unknown): Promise<T> {
  const response = await fetch(path, payload === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const error = data?.error ?? data?.detail
    throw new ApiError(
      typeof error?.message === 'string' ? error.message : 'Falha na comunicação com FetchCore',
      typeof error?.category === 'string' ? error.category : 'http_error',
      response.status,
    )
  }
  return response.json() as Promise<T>
}

export const getConfig = () => call<ExplorerConfig>('/api/config')
export const search = (payload: SearchPayload) => call<SearchResponse>('/api/search', payload)
export const readSource = (payload: {
  source_ref: string
  repository: string
  revision?: string
  line_start?: number
  line_end?: number
  context_lines: number
}) => call<SourceResult>('/api/source', payload)

export function parseIntent(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('SearchIntent deve ser um objeto JSON.')
  }
  return value as Record<string, unknown>
}

/** Build a source request without pretending result provenance authorizes a workspace revision. */
export function sourcePayloadForHit(hit: SearchHit, repository: string, boundRevision: string | null) {
  return {
    source_ref: hit.source_ref,
    repository,
    ...(hit.revision && hit.revision === boundRevision ? { revision: hit.revision } : {}),
    ...(hit.line_start ? { line_start: hit.line_start } : {}),
    ...(hit.line_end ? { line_end: hit.line_end } : {}),
    context_lines: 5,
  }
}
