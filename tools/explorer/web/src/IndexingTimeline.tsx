import { useRef, useState } from 'react'
import { exportArtifact, observed, validateArtifacts, type ArtifactResponse, type IndexArtifact } from './indexArtifacts'

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function Facts({ data, fields }: { data: Record<string, unknown>; fields: string[] }) {
  return <dl className="grid gap-2 text-xs md:grid-cols-2">{fields.map(key => <div key={key} className="min-w-0 rounded border border-line p-2">
    <dt className="text-muted">{key}</dt><dd className="break-all font-mono">{observed(data[key])}</dd>
  </div>)}</dl>
}
function Run({ artifact }: { artifact: IndexArtifact }) {
  const result = object(artifact.command.result)
  const execution = object(result.execution)
  const snapshot = object(execution.snapshot)
  const discovery = object(result.discovery ?? execution.discovery)
  const telemetry = object(execution.telemetry)
  const canonical = object(result.indexing_telemetry)
  function download() {
    const url = URL.createObjectURL(new Blob([exportArtifact(artifact)], { type: 'application/json' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'index-run.json'; anchor.click()
    URL.revokeObjectURL(url)
  }
  return <article className="min-w-0 rounded-lg border border-line p-4" aria-label={`Index run ${artifact.run_id}`}>
    <h3 className="font-semibold">{artifact.run_id} · {artifact.command.operation} · {observed(result.mode ?? artifact.command.status)}</h3>
    <p className="my-2 text-xs text-muted">Observado em {artifact.observed_at} · {artifact.repository} · {artifact.backend} · {artifact.target}</p>
    <button className="button-secondary mb-3" onClick={download}>Exportar observação</button>
    {artifact.command.error && <p role="status">Falha: {artifact.command.error.category} — {artifact.command.error.message}. Persistência e rollback: desconhecidos.</p>}
    <p className="break-all text-xs">Configuração SHA-256: {artifact.configuration_fingerprint}</p>
    <h4 className="my-3 text-sm">Revisão e motivo do trabalho</h4>
    <Facts data={result} fields={['target_revision', 'indexed_revision', 'previous_indexed_revision', 'currentness', 'previous_currentness', 'reason', 'schema_compatible', 'projection_compatible', 'indexing_fingerprint', 'projection_fingerprint']}/>
    <h4 className="my-3 text-sm">Scan / descoberta canônica</h4>
    <Facts data={discovery} fields={['mode', 'reason', 'changed_paths', 'removed_paths', 'discovery_duration_ms', 'git_diff_duration_ms', 'changed_path_values', 'removed_path_values']}/>
    <p className="my-2 text-xs text-muted">Full scan não implica substituição completa da projeção persistida.</p>
    <Facts data={canonical} fields={['files_scanned', 'files_changed', 'chunks_total', 'chunks_changed', 'duration_ms', 'indexing_throughput_chunks_per_second']}/>
    <h4 className="my-3 text-sm">{artifact.command.operation === 'plan' ? 'Mutações planejadas (sem persistência)' : 'Mutações persistidas observadas'}</h4>
    <Facts data={artifact.command.operation === 'plan' ? object(result.planned_mutations) : execution} fields={['mode', 'replace_repository', 'added', 'updated', 'deleted', 'unchanged', 'duration_ms']}/>
    <h4 className="my-3 text-sm">Embeddings / persistência medida</h4>
    <Facts data={telemetry} fields={['backend_writes', 'backend_deletes', 'duration_ms', 'embedding_requests', 'embedding_vectors', 'embedding_duration_ms', 'embedding_throughput_vectors_per_second', 'embedding_cache_hits']}/>
    <h4 className="my-3 text-sm">Snapshot persistido</h4>
    <Facts data={snapshot} fields={['revision', 'document_count', 'corpus_checksum', 'indexed_at', 'status', 'projection_fingerprint', 'indexing_fingerprint', 'serializer_fingerprint', 'serializer_mode', 'serializer_version', 'embedding_provider', 'embedding_model', 'vector_dimensions']}/>
    <details className="mt-3"><summary>Artefato original</summary><pre className="max-h-96 overflow-auto text-xs">{exportArtifact(artifact)}</pre></details>
  </article>
}
export function IndexingTimeline() {
  const [report, setReport] = useState<ArtifactResponse | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const request = useRef(0)
  async function importFiles(files: FileList | null) {
    const id = ++request.current
    setReport(null); setError(''); setBusy(false)
    if (!files?.length) return
    if (files.length > 2 || [...files].some(f => f.size > 2_000_000)) {
      setError('Selecione um ou dois artefatos de até 2 MB cada.'); return
    }
    setBusy(true)
    try {
      const raw = await Promise.all([...files].map(f => f.text()))
      const response = await validateArtifacts(raw)
      if (id === request.current) setReport(response)
    } catch (e) {
      if (id === request.current) setError(e instanceof Error ? e.message : 'Falha na importação')
    } finally { if (id === request.current) setBusy(false) }
  }
  return <section className="panel mt-6 p-5" aria-label="Indexing Timeline">
    <h2 className="text-lg font-semibold">Indexing Timeline & Projection Diff</h2>
    <p className="my-2 text-sm text-muted">Importe uma observação ou duas para antes/depois. Somente leitura; a ordem de seleção define antes → depois. Nenhum histórico ou duração é inferido.</p>
    <label className="block text-sm">Importar artefatos de indexação
      <input aria-label="Importar artefatos de indexação" className="my-3 block" type="file" accept=".json,application/json" multiple onChange={e => void importFiles(e.target.files)}/>
    </label>
    <button className="button-secondary mb-3" onClick={() => { request.current++; setBusy(false); setReport(null); setError('') }}>Limpar observações</button>
    {busy && <p role="status">Validando contratos…</p>}
    {error && <p role="alert">{error}</p>}
    {report?.comparison && <div className="my-3 rounded border border-line p-3" role="status">
      <strong>{report.comparison.comparable ? 'Comparação compatível' : 'Comparação bloqueada'}</strong>
      <p>Revisão de origem mudou: {observed(report.comparison.source_revision_changed)}</p>
      <p>{report.comparison.reasons.join(', ')}</p>
      <p className="text-xs text-muted">Contagens pertencem a cada observação. Diferenças de contagens não provam alterações em documentos individuais.</p>
    </div>}
    <div className="grid gap-4 xl:grid-cols-2">{report?.artifacts.map((a, i) => <div key={i}><p className="mb-2 text-xs text-muted">{report.artifacts.length === 2 ? i === 0 ? 'Antes' : 'Depois' : 'Observação'}</p><Run artifact={a}/></div>)}</div>
  </section>
}
