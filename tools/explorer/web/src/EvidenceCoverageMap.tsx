import { useMemo, useState } from 'react'
import { buildCoverageMap } from './evidenceCoverage'
import type { CoverageState, CoverageRow } from './evidenceCoverage'
import type { SearchPayload, SearchResponse } from './types'

interface Props {
  request: SearchPayload
  response: SearchResponse
  repository: string
  revision: string | null
  onInspect: (id: string) => void
}
type Filter = 'all' | 'required' | 'observed' | 'missing' | 'unknown'
const labels: Record<CoverageState, string> = {
  observed: 'Match observado',
  'not-observed': 'Sem match observado',
  'not-evaluated': 'Não avaliado/informado',
  unsupported: 'Tradução não suportada',
  truncated: 'Janela limitada',
}
function stateText(state: CoverageState) { return labels[state] }
function stateClass(state: CoverageState): string {
  if (state === 'observed') return 'border-emerald-600/50 bg-emerald-950/30 text-emerald-200'
  if (state === 'not-observed') return 'border-amber-700/50 bg-amber-950/30 text-amber-100'
  return 'border-line bg-background/60 text-muted'
}
function rowGroup(row: CoverageRow) {
  if (row.role === 'anchor' || row.role === 'expression-anchor' || row.role === 'signal-required' || row.role === 'expression-term' || row.role === 'exact-term') return 'Requisito declarado'
  if (row.role === 'hint' || row.role === 'expansion') return 'Opcional / expansão'
  if (row.role === 'canonical') return 'Query canônica (sem satisfação implícita)'
  return 'Sinal / termo'
}
export function EvidenceCoverageMap({ request, response, repository, revision, onInspect }: Props) {
  const coverage = useMemo(() => buildCoverageMap(request, response), [request, response])
  const [filter, setFilter] = useState<Filter>('all')
  const [selected, setSelected] = useState<{ row: string; col: string } | null>(null)
  const view = coverage.rows.filter(row =>
    filter === 'all' || filter === 'required' && row.required
    || filter === 'observed' && row.summary === 'observed'
    || filter === 'missing' && (row.summary === 'not-observed' || row.summary === 'truncated')
    || filter === 'unknown' && (row.summary === 'not-evaluated' || row.summary === 'unsupported'))
  const row = coverage.rows.find(item => item.id === selected?.row)
  const col = coverage.columns.find(item => item.key === selected?.col)
  const cell = row && col ? row.cells[coverage.columns.indexOf(col)] : null
  return <section className="panel mt-6 p-5" aria-label="Evidence Coverage Map">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold text-white">Evidence Coverage Map</h2>
        <p className="mt-1 max-w-3xl text-xs leading-5 text-muted">
          SearchIntent declarado × candidatos da última execução. Match observado significa apenas
          evidência tipada reportada pelo backend; não significa satisfação, relevância verificada
          ou ausência de evidências no repositório.
        </p>
      </div>
      <span className="chip">#127 · read-only</span>
    </div>
    <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      <div className="rounded-lg border border-line p-3 text-xs"><span className="text-muted">Elementos mapeados</span>
        <strong className="mt-1 block text-base text-white">{coverage.diagnostics.visibleSignals}/{coverage.diagnostics.totalSignals}</strong></div>
      <div className="rounded-lg border border-line p-3 text-xs"><span className="text-muted">Sources distintas exibidas</span>
        <strong className="mt-1 block text-base text-white">{coverage.diagnostics.visibleCandidates}</strong></div>
      <div className="rounded-lg border border-line p-3 text-xs"><span className="text-muted">Resultados com identidade duplicada</span>
        <strong className="mt-1 block text-base text-white">{coverage.diagnostics.duplicateCandidates}</strong></div>
      <div className="rounded-lg border border-line p-3 text-xs"><span className="text-muted">Canal efetivo</span>
        <strong className="mt-1 block text-base text-white">{coverage.diagnostics.effectiveMode ?? 'não informado'}</strong></div>
    </div>
    {(coverage.diagnostics.resultWindowBounded || coverage.diagnostics.matrixTruncated) &&
      <p className="mt-3 rounded-lg border border-amber-700/50 p-3 text-xs text-amber-200" role="status">
        Janela limitada: a busca atingiu top-K ou o mapa restringiu a visualização a 48 sinais × 24
        identidades. Ausência de match na janela não é ausência no corpus.
      </p>}
    {coverage.diagnostics.noQueryMatchDiagnostics &&
      <p className="mt-3 rounded-lg border border-line p-3 text-xs text-muted">
        O backend não informou QueryMatchEvidence nesta resposta. As células ficam
        como não avaliadas; nenhuma correspondência é inferida de scores, RRF ou conteúdo.
      </p>}
    <p className="mt-3 break-all text-xs text-muted">
      Repositório autorizado: {repository} · Revision binding: {revision ?? 'não vinculada'}
      · modo observado: {coverage.diagnostics.effectiveMode ?? 'não informado'}
      · fingerprints: {Object.keys(coverage.diagnostics.fingerprints).length ?
        JSON.stringify(coverage.diagnostics.fingerprints) : 'não informados'}
    </p>
    {!!coverage.branches.length && <section className="mt-4 rounded-lg border border-line p-3">
      <h3 className="text-sm font-semibold text-white">Branches explicitamente declarados</h3>
      <p className="mt-1 text-xs text-muted">Contagem de folhas com matches reportados,
        não avaliação da conjunção nem da disjunção sobre um candidato.</p>
      <div className="mt-3 flex flex-wrap gap-2">{coverage.branches.map(branch =>
        <span key={branch.path} className="chip">{branch.path}: {branch.operator === 'all_of' ? 'AND' : 'OR'}
          · {branch.observedChildren}/{branch.totalChildren} folhas observadas</span>)}</div>
    </section>}
    <div className="mt-4 flex flex-wrap gap-2" aria-label="Filtros de cobertura">
      {([
        ['all', 'Todos'], ['required', 'Obrigatórios'], ['observed', 'Matches'],
        ['missing', 'Sem match / limitados'], ['unknown', 'Não avaliados'],
      ] as const).map(([key, label]) => <button key={key} type="button" aria-pressed={filter === key}
        onClick={() => setFilter(key)} className={'switch-button border border-line ' + (filter === key ? 'switch-active' : '')}>
        {label}
      </button>)}
    </div>
    <div className="mt-3 max-h-[540px] overflow-auto rounded-lg border border-line" aria-label="Matriz de cobertura tipada">
      <table className="min-w-[750px] w-full border-collapse text-left text-xs">
        <thead className="sticky top-0 z-10 bg-surface">
          <tr><th scope="col" className="sticky left-0 z-20 min-w-[225px] max-w-[300px] border-b border-line bg-surface px-3 py-3">
            Intenção / sinal</th>
            {coverage.columns.map((column, index) =>
              <th scope="col" key={column.key} className="min-w-[105px] max-w-[135px] border-b border-line px-2 py-3">
                <span className="block">#{index + 1}</span>
                <span className="block break-all text-[10px] text-muted" title={column.source_ref}>
                  {column.source_ref.split('/').slice(-1)[0]}</span>
                {column.duplicateCount > 0 && <span className="block text-[10px] text-amber-200">
                  +{column.duplicateCount} duplicatas</span>}
              </th>)}
          </tr>
        </thead>
        <tbody>{view.map(item => <tr key={item.id} className="border-t border-line">
          <th scope="row" className="sticky left-0 z-10 max-w-[300px] break-words bg-surface px-3 py-3 align-top font-normal">
            <strong className="block text-white">{item.value}</strong>
            <span className="mt-1 block text-[11px] text-muted">{rowGroup(item)} · {item.path}
              {item.kind ? ' · ' + item.kind : ''}</span>
            <span className={'mt-2 inline-block rounded border px-2 py-1 text-[10px] ' + stateClass(item.summary)}>
              {stateText(item.summary)}</span>
            <span className="mt-1 block text-[10px] text-muted">Candidatos com match: {item.observedCandidates}</span>
          </th>
          {item.cells.map((data, i) => {
            const column = coverage.columns[i]
            return <td key={column.key} className="px-2 py-3 align-top">
              <button type="button" aria-label={item.path + ' × candidato ' + (i + 1)}
                aria-pressed={selected?.row === item.id && selected?.col === column.key}
                className={'min-h-9 w-full rounded border p-2 text-[11px] ' + stateClass(data.state)}
                onClick={() => setSelected({ row: item.id, col: column.key })}>
                {data.state === 'observed' ? 'Match' : data.state === 'not-observed' ? 'Sem match' :
                  data.state === 'unsupported' ? 'Unsupported' : 'N/D'}
              </button>
            </td>
          })}
        </tr>)}</tbody>
      </table>
      {!view.length && <p className="p-4 text-xs text-muted">Nenhuma linha para o filtro selecionado.</p>}
      {!coverage.columns.length && <p className="p-4 text-xs text-muted">Nenhum candidato retornado; cobertura não avaliada.</p>}
    </div>
    {row && col && cell && <div className="mt-4 rounded-lg border border-line p-4" aria-label="Detalhe da célula de cobertura">
      <div className="flex flex-wrap justify-between gap-2">
        <h3 className="text-sm font-semibold text-white">Diagnóstico: {row.value}</h3>
        <span className={'rounded border px-2 py-1 text-xs ' + stateClass(cell.state)}>{stateText(cell.state)}</span>
      </div>
      <p className="mt-2 break-all text-xs text-muted">{col.source_ref} · revisão: {col.revision ?? 'não informada'}
        · checksum: {col.checksum ?? 'não informado'} · {col.knowledge_type}
        · linhas: {col.line_start ?? '?'}–{col.line_end ?? '?'}
        · {col.hitIds.length} hit(s)</p>
      <p className="mt-2 text-xs text-muted">Canais reportados: {cell.channels.join(', ') || 'não reportados'}
        · EvidenceObservation neste candidato: {cell.observations} (não usados como prova de match)</p>
      {cell.matches.length > 0 && <details className="mt-3">
        <summary className="cursor-pointer text-xs text-accent">QueryMatchEvidence tipada ({cell.matches.length})</summary>
        <pre className="json-output mt-2 max-h-56 overflow-auto">{JSON.stringify(cell.matches, null, 2)}</pre>
      </details>}
      <button type="button" className="switch-button mt-3 border border-line"
        onClick={() => onInspect(col.primaryId)}>Inspecionar candidato e fonte autorizada</button>
      <p className="mt-2 text-xs text-muted">
        A leitura de source segue as regras já existentes do Search Lab:
        revisão indexada diferente do workspace é sinalizada, não considerada equivalente.
      </p>
    </div>}
    <p className="mt-4 text-xs text-muted">
      A matriz mostra até 48 itens declarados × 24 identidades. Não usa scores como
      autoridade, nem converte presença em aprovação. Dense/hybrid só têm atribuição
      quando canais constam realmente no QueryMatchEvidence.
    </p>
  </section>
}
