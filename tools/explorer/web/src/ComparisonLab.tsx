import { useState } from 'react'
import { compareRuns, exportComparison } from './compareRuns'
import type { CandidateDiff, ComparisonDiff } from './compareRuns'
import type { RunManifest, RecordedHit } from './runHistory'

type Filter = 'all' | 'shared' | 'only-a' | 'only-b'
interface Props {
  runs: RunManifest[]
  onInspect: (run: RunManifest, hit: RecordedHit) => boolean
}
function display(value: number | null | undefined, suffix = ''): string {
  return value == null ? 'não medido' : String(value) + suffix
}
function armInfo(run: RunManifest) {
  return run.target.id + ' · ' + run.target.backend + ' · ' +
    (run.target.collection ?? 'índice desconhecido') + ' · ' + (run.outcome.telemetry?.mode ?? 'modo não medido')
}
function MetricRow({ label, a, b }: { label: string; a: string; b: string }) {
  return <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 border-t border-line py-2 text-xs">
    <span className="text-muted">{label}</span>
    <span className="break-words font-mono text-white">{a}</span>
    <span className="break-words font-mono text-white">{b}</span>
  </div>
}
function DiffView({ diff, a, b, onInspect }: {
  diff: ComparisonDiff
  a: RunManifest
  b: RunManifest
  onInspect: Props['onInspect']
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const [warning, setWarning] = useState('')
  const status = diff.assessment.status
  const candidates = diff.candidates.filter(candidate => filter === 'all' || candidate.presence === filter)
  function downloadDiff() {
    const url = URL.createObjectURL(new Blob([exportComparison(diff)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'fetchcore-retrieval-diff-v1.json'
    link.click()
    URL.revokeObjectURL(url)
    setWarning('Diff exportado. Contém consultas técnicas indiretamente identificáveis por IDs e source refs; revise antes de compartilhar.')
  }
  function inspect(run: RunManifest, hit: RecordedHit) {
    if (!onInspect(run, hit)) {
      setWarning('Somente candidatos da resposta atual com identidade, target e checksum compatíveis podem ser abertos. Um manifest histórico não autoriza leitura de source.')
    } else {
      setWarning('Candidato atual selecionado na área Explorar evidências.')
    }
  }
  function candidateRow(candidate: CandidateDiff) {
    const allowRank = diff.assessment.status === 'snapshot-aligned'
    return <tr key={candidate.key} className="border-t border-line">
      <td className="min-w-[180px] max-w-[340px] break-all px-3 py-3 align-top">
        <code className="text-xs text-white">{candidate.identity}</code>
        <div className="mt-1 text-[11px] text-muted">{candidate.identity_confidence === 'run-local-id' ?
          'ID local do backend — identidade fraca' : candidate.identity_confidence === 'partial-source-ref' ? 'source_ref sem revisão/checksum — correspondência não verificada' : 'source_ref com revisão/checksum'}</div>
      </td>
      <td className="px-3 py-3 align-top font-mono text-xs">{candidate.a?.rank ?? '—'}</td>
      <td className="px-3 py-3 align-top font-mono text-xs">{candidate.b?.rank ?? '—'}</td>
      <td className="px-3 py-3 align-top text-xs">
        {candidate.presence === 'shared' ? 'Compartilhado' : candidate.presence === 'only-a' ? 'Somente A' : 'Somente B'}
        {allowRank && candidate.rank_delta !== null &&
          <span className="block text-[11px] text-muted">Δ ranking (B−A): {candidate.rank_delta > 0 ? '+' : ''}{candidate.rank_delta}</span>}
      </td>
      <td className="max-w-[180px] break-words px-3 py-3 align-top text-[11px] text-muted">
        A: {candidate.channels_a.length ? candidate.channels_a.join(', ') : 'não informado'}
        <div>B: {candidate.channels_b.length ? candidate.channels_b.join(', ') : 'não informado'}</div>
      </td>
      <td className="px-3 py-3 align-top text-xs">
        {candidate.a && <button type="button" className="switch-button border border-line"
          onClick={() => inspect(a, candidate.a!)}>A</button>}
        {candidate.b && <button type="button" className="switch-button ml-1 border border-line"
          onClick={() => inspect(b, candidate.b!)}>B</button>}
      </td>
    </tr>
  }
  return <div className="space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <p className="text-sm font-semibold text-white">A × B — {status === 'snapshot-aligned'
          ? 'fingerprints alinhados' : status === 'unverified' ? 'não comprovada' : 'incompatível como experimento controlado'}</p>
        <p className="text-xs text-muted">{armInfo(a)}</p>
        <p className="text-xs text-muted">{armInfo(b)}</p>
      </div>
      <button type="button" className="switch-button border border-line" onClick={downloadDiff}>
        Exportar diff JSON v1
      </button>
    </div>
    <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {[
        ['Compartilhados', diff.counts.shared],
        ['Somente A', diff.counts.only_a],
        ['Somente B', diff.counts.only_b],
        ['Retidos A', diff.counts.a],
        ['Retidos B', diff.counts.b],
      ].map(([name, value]) => <div className="rounded-lg border border-line p-3" key={String(name)}>
        <div className="text-[11px] text-muted">{name}</div>
        <strong className="text-lg text-white">{value}</strong>
      </div>)}
    </div>
    {diff.assessment.reasons.length > 0 && <div className="rounded-lg border border-amber-800/50 bg-amber-950/15 p-3" role="status">
      <strong className="text-xs text-amber-200">Não apresentar como benchmark controlado</strong>
      <ul className="mt-2 list-inside list-disc space-y-1 text-xs text-amber-100">
        {diff.assessment.reasons.map(reason => <li key={reason}>{reason}</li>)}
      </ul>
    </div>}
    {diff.assessment.observations.length > 0 && <div className="text-xs text-muted">
      {diff.assessment.observations.map(value => <p key={value}>• {value}</p>)}
    </div>}
    <section className="rounded-lg border border-line px-3 py-2">
      <h3 className="py-2 text-sm font-semibold text-white">Custos observados</h3>
      <MetricRow label="Execução" a="A" b="B"/>
      <MetricRow label="Tempo total" a={display(diff.costs.a?.duration_ms, ' ms')} b={display(diff.costs.b?.duration_ms, ' ms')}/>
      <MetricRow label="Time-to-first-candidate" a={display(diff.costs.a?.time_to_first_candidate_ms, ' ms')} b={display(diff.costs.b?.time_to_first_candidate_ms, ' ms')}/>
      <MetricRow label="Candidatos examinados" a={display(diff.costs.a?.candidates_examined)} b={display(diff.costs.b?.candidates_examined)}/>
      <MetricRow label="Candidatos retornados" a={display(diff.costs.a?.candidates_returned)} b={display(diff.costs.b?.candidates_returned)}/>
      <MetricRow label="Caracteres retornados" a={display(diff.costs.a?.returned_chars)} b={display(diff.costs.b?.returned_chars)}/>
      <MetricRow label="Budget top-K" a={String(a.request.limit)} b={String(b.request.limit)}/>
      <details className="py-2">
        <summary className="cursor-pointer text-xs text-accent">Latência dos estágios relatados pelo backend</summary>
        <pre className="json-output mt-2">{JSON.stringify({
          A: diff.costs.a?.stages ?? null, B: diff.costs.b?.stages ?? null,
        }, null, 2)}</pre>
      </details>
    </section>
    <div className="flex flex-wrap gap-2" aria-label="Filtrar diferenças">
      {[
        ['all', 'Todos'], ['shared', 'Compartilhados'], ['only-a', 'Somente A'], ['only-b', 'Somente B'],
      ].map(([value, label]) =>
        <button key={value} type="button" aria-pressed={filter === value} className={'switch-button border border-line ' +
          (filter === value ? 'switch-active' : '')}
          onClick={() => setFilter(value as Filter)}>{label}</button>)}
    </div>
    <div className="max-h-[520px] overflow-auto rounded-lg border border-line" aria-label="Diferenças dos candidatos">
      <table className="min-w-[770px] w-full text-left">
        <thead className="sticky top-0 bg-surface text-xs text-muted">
          <tr><th className="px-3 py-2">Identidade</th><th className="px-3 py-2">Rank A</th>
            <th className="px-3 py-2">Rank B</th><th className="px-3 py-2">Observação</th>
            <th className="px-3 py-2">Canais declarados</th><th className="px-3 py-2">Inspecionar atual</th></tr>
        </thead>
        <tbody>{candidates.map(candidateRow)}</tbody>
      </table>
      {!candidates.length && <p className="p-4 text-xs text-muted">Nenhuma identidade nesta seleção.</p>}
    </div>
    {status !== 'snapshot-aligned' && <p className="text-xs text-amber-100">
      Δ rank indisponível: índices/snapshots não estão comprovadamente alinhados.
      A presença na tabela representa apenas candidatos armazenados nos manifests.
    </p>}
    {warning && <p className="text-xs text-amber-100" role="status">{warning}</p>}
  </div>
}

export function ComparisonLab({ runs, onInspect }: Props) {
  const [aId, setAId] = useState('')
  const [bId, setBId] = useState('')
  const [cId, setCId] = useState('')
  const a = runs.find(run => run.id === aId) ?? runs[0] ?? null
  const b = runs.find(run => run.id === bId && run.id !== a?.id)
    ?? runs.find(run => run.id !== a?.id) ?? null
  const c = runs.find(run => run.id === cId && run.id !== a?.id && run.id !== b?.id) ?? null
  return <section className="panel mt-6 p-5" aria-label="Retrieval Comparison Lab">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold text-white">Retrieval Comparison Lab</h2>
        <p className="mt-1 text-xs text-muted">
          Compare 2 execuções históricas; uma terceira é opcional. Observações e custos reais,
          sem estimar relevância, recall, Ground Truth ou satisfação.
        </p>
      </div>
      <span className="chip">#126 · A/B/C</span>
    </div>
    {runs.length < 2 ? <p className="mt-4 text-sm text-muted">
      Execute pelo menos duas queries ou importe manifests válidos pelo Histórico de execuções.
      Comparar não dispara novas buscas.
    </p> : <>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        {[
          ['A · referência', a?.id ?? '', (value: string) => { setAId(value); setCId('') }],
          ['B · alternativa', b?.id ?? '', setBId],
          ['C · opcional', c?.id ?? '', setCId],
        ].map(([name, value, action], i) =>
          <div key={String(name)}>
            <label className="field-label" htmlFor={'comparison-' + i}>{String(name)}</label>
            <select id={'comparison-' + i} className="field-input" value={String(value)}
              onChange={event => (action as (value: string) => void)(event.target.value)}>
              {i === 2 && <option value="">Sem terceira execução</option>}
              {runs.filter(run => i === 0 || i === 1 ? (i === 0 ? run.id !== b?.id : run.id !== a?.id)
                : run.id !== a?.id && run.id !== b?.id).map(run =>
                <option key={run.id} value={run.id}>{run.id.slice(0, 36)} · {run.target.id} · {run.outcome.telemetry?.mode ?? 'sem modo'}</option>)}
            </select>
          </div>)}
      </div>
      {a && b && <div className="mt-5 space-y-6">
        <DiffView key={a.id + b.id} a={a} b={b} diff={compareRuns(a, b)} onInspect={onInspect}/>
        {c && <div className="border-t border-line pt-5">
          <p className="mb-3 text-xs text-muted">Terceiro braço: comparação A × C (sem fusão dos três resultados).</p>
          <DiffView key={a.id + c.id} a={a} b={c} diff={compareRuns(a, c)} onInspect={onInspect}/>
        </div>}
      </div>}
    </>}
  </section>
}
