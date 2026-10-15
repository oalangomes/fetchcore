/** Transport mirror of the application-owned v1 indexing observation envelope. */
export interface IndexArtifact {
  schema_version: 1
  kind: 'fetchcore.index-run'
  run_id: string
  observed_at: string
  repository: string
  backend: string
  target: string
  configuration_fingerprint: string
  command: {
    operation: 'status' | 'plan' | 'sync' | 'rebuild'
    status: 'succeeded' | 'failed'
    result?: Record<string, unknown>
    error?: { category: string; message: string }
  }
}
export interface ArtifactResponse {
  artifacts: IndexArtifact[]
  comparison: { comparable: boolean; reasons: string[]; source_revision_changed: boolean | null } | null
}

export function observed(value: unknown): string {
  return value === undefined || value === null ? 'desconhecido' : typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export async function validateArtifacts(raw: string[]): Promise<ArtifactResponse> {
  const response = await fetch('/api/indexing/artifacts', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artifacts: raw }),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data?.error?.message ?? data?.detail?.message ?? 'Artefato de indexação inválido')
  return data as ArtifactResponse
}

export function exportArtifact(artifact: IndexArtifact): string {
  return JSON.stringify(artifact, null, 2)
}
