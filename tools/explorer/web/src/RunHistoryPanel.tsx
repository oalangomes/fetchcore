import { useState } from 'react'
import { exportHistory, importHistory } from './runHistory'
import type { RunManifest } from './runHistory'
import type { ExplorerTarget } from './types'

export interface RunHistoryProps {
  runs: RunManifest[]
  targets: ExplorerTarget[]
  busy: boolean
  onChange: (runs: RunManifest[]) => void
  onRestore: (run: RunManifest) => void
  onReplay: (run: RunManifest) => Promise<void>
}

export function RunHistoryPanel({ runs, targets, busy, onChange, onRestore, onReplay }: RunHistoryProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showImport, setShowImport] = useState(false)
  const [importDraft, setImportDraft] = useState('')
  const [warning, setWarning] = useState('')
  const [confirmClear, setConfirmClear] = useState(false)
  const [replaying, setReplaying] = useState(false)
  const selected = runs.find(run => run.id === selectedId) ?? runs[0] ?? null

  function importRuns() {
    try {
      const imported = importHistory(importDraft)
      onChange(imported)
      setSelectedId(imported[0]?.id ?? null)
      setImportDraft('')
      setShowImport(false)
      setWarning('Histórico importado somente em memória; nenhum replay foi executado.')
    } catch (error) {
      setWarning(error instanceof Error ? error.message : 'Bundle inválido')
    }
  }

  function download() {
    try {
      const data = exportHistory(runs)
      const url = URL.createObjectURL(new Blob([data], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url
      link.download = 'fetchcore-run-history-v1.json'
      link.click()
      URL.revokeObjectURL(url)
      setWarning('Exportado manualmente. Atenção: IDs, queries e source refs podem ser privados.')
    } catch (error) {
      setWarning(error instanceof Error ? error.message : 'Falha na exportação')
    }
  }

  async function replay(run: RunManifest) {
    setWarning('')
    setReplaying(true)
    try {
      await onReplay(run)
      setWarning('Replay autorizado e solicitado. O resultado será uma nova execução, não a verdade da execução anterior.')
    } catch (error) {
      setWarning(error instanceof Error ? error.message : 'Replay bloqueado')
    } finally {
      setReplaying(false)
    }
  }

  const available = selected && targets.some(target => target.id === selected.target.id
    && target.repository === selected.target.repository
    && target.backend === selected.target.backend
    && target.collection === selected.target.collection)

  return <section aria-label="Histórico de execuções" className="mt-4 border-t border-line p-5">
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-sm font-semibold text-white">Histórico de execuções</h3>
      <span className="chip">{runs.length}/20</span>
    </div>
    <p className="mt-2 text-xs leading-5 text-muted">
      Apenas nesta aba, em memória; recarregar limpa o histórico. Não armazenamos
      código-fonte, credenciais ou URLs privadas. Exportação e replay são explícitos.
    </p>
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" className="switch-button border border-line"
        onClick={download} disabled={!runs.length}>Exportar histórico</button>
      <button type="button" className="switch-button border border-line"
        onClick={() => { setShowImport(open => !open); setWarning('') }}>Importar histórico</button>
      <button type="button" className="switch-button border border-line"
        onClick={() => setConfirmClear(true)} disabled={!runs.length}>Limpar</button>
    </div>
    {showImport && <div className="mt-3 space-y-2">
      <label className="field-label" htmlFor="history-import">Bundle JSON v1 (máx. 512 KB / 20 execuções)</label>
      <textarea id="history-import" className="field-input min-h-[100px] font-mono text-xs"
        value={importDraft} onChange={event => setImportDraft(event.target.value)}/>
      <button type="button" className="switch-button border border-line" onClick={importRuns}>Confirmar importação</button>
      <p className="text-xs text-muted">A importação não efetua busca nem autoriza novos destinos.</p>
    </div>}
    {confirmClear && <div className="mt-3 rounded-lg border border-line p-3">
      <p className="text-xs text-muted">Apagar as execuções desta aba? Esta ação não afeta índices.</p>
      <div className="mt-2 flex gap-2">
        <button type="button" className="switch-button border border-line"
          onClick={() => { onChange([]); setSelectedId(null); setConfirmClear(false); setWarning('Histórico limpo.') }}>
          Confirmar limpeza
        </button>
        <button type="button" className="switch-button border border-line"
          onClick={() => setConfirmClear(false)}>Cancelar</button>
      </div>
    </div>}
    {warning && <p className="mt-3 rounded-lg border border-line p-2 text-xs text-amber-100" role="status">{warning}</p>}
    {!runs.length && <p className="mt-4 text-xs text-muted">Nenhuma execução registrada nesta aba.</p>}
    {!!runs.length && <div className="mt-4 max-h-64 space-y-2 overflow-auto">
      {runs.map(run => <button type="button" key={run.id}
        aria-label={'Selecionar execução ' + run.id} aria-pressed={selected?.id === run.id}
        className={'result-card w-full text-left ' + (selected?.id === run.id ? 'result-active' : '')}
        onClick={() => setSelectedId(run.id)}>
        <span className="block text-xs text-white">{run.request.intent.canonical_query || '(sem consulta canônica)'}</span>
        <span className="mt-1 block text-xs text-muted">{run.recorded_at} · {run.target.id} · {run.outcome.state}
          {run.outcome.truncated && ' · truncado'}</span>
      </button>)}
    </div>}
    {selected && <div className="mt-3 space-y-3 rounded-lg border border-line p-3">
      <p className="text-xs font-semibold text-white">Manifest selecionado · v{selected.schema_version}</p>
      <p className="break-all text-xs text-muted">{selected.target.repository} · {selected.target.backend} ·
        {selected.target.collection ?? 'sem índice'} · revisão {selected.target.revision ?? 'não vinculada'}</p>
      <p className="text-xs text-muted">Candidatos observados: {selected.outcome.results.length}
        · telemetry {selected.outcome.telemetry ? 'informada' : 'não informada'}
        · fingerprints {Object.keys(selected.outcome.fingerprints).length}
        {selected.outcome.truncated && ' · snapshot truncado'}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="switch-button border border-line"
          onClick={() => { onRestore(selected); setWarning('Query restaurada como rascunho. Nenhuma busca foi executada.') }}>
          Restaurar query
        </button>
        <button type="button" className="switch-button border border-line"
          disabled={busy || replaying || !available}
          onClick={() => void replay(selected)}>Replay verificado</button>
      </div>
      {!available && <p className="text-xs text-amber-200">Destino não disponível/autorizado nesta sessão; replay bloqueado.</p>}
      <details>
        <summary className="cursor-pointer text-xs text-accent">Inspecionar manifest e evidências observadas</summary>
        <pre className="json-output mt-2 max-h-72 overflow-auto">{JSON.stringify(selected, null, 2)}</pre>
      </details>
      <p className="text-xs text-muted">
        Replay estrito exige target, revisão, fingerprints e readiness compatíveis.
        Em ausência de prova, use Restaurar query e execute como nova busca não comparável.
        A comparação A/B controlada pertence à #126; qualidade/ground truth ao retrievalgate.
      </p>
    </div>}
  </section>
}
