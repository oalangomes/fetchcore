import { useRef, useState, type ReactNode, type FormEvent } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import CodeMirror from '@uiw/react-codemirror'
import { json } from '@codemirror/lang-json'
import { EditorView } from '@codemirror/view'
import * as Tabs from '@radix-ui/react-tabs'
import {
  Activity, ArrowDownRight, ArrowRight, Braces, Check, ChevronRight, CircleAlert,
  Clock3, Code2, Copy, Database, FileCode2, Fingerprint, Gauge, GitBranch,
  Layers3, ListFilter, LoaderCircle, Network, Play, Search, ShieldCheck, SlidersHorizontal,
  Sparkles, TerminalSquare, Workflow,
} from 'lucide-react'
import { Button } from './components/ui/button'
import { ApiError, getConfig, getReadiness, parseIntent, readSource, search, sourcePayloadForHit } from './api'
import { SearchIntentForm } from './SearchIntentForm'
import { RunHistoryPanel } from './RunHistoryPanel'
import { ComparisonLab } from './ComparisonLab'
import { captureRun, prependRun, replayGate } from './runHistory'
import type { RunManifest, RecordedHit } from './runHistory'
import { applyPreset, intentPresets, parseValidatedIntent, validateVisualIntent } from './intentBuilder'
import type { ExplorerTarget, SearchHit, SearchPayload, SearchResponse, RetrievalTelemetry } from './types'

const initialQuery = 'where is authentication validated?'
const initialIntent = JSON.stringify({
  schema_version: 2,
  canonical_query: initialQuery,
  anchors: [],
  expression: null,
  expansions: [],
  hints: [],
  signals: [],
}, null, 2)
const editorExtensions = [json(), EditorView.lineWrapping]

function text(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
function shorten(value: string | undefined, count = 74): string {
  if (!value) return '—'
  return value.length > count ? value.slice(0, count - 1) + '…' : value
}
function Metric({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <div className="metric">
    <span className="metric-label">{label}</span>
    <strong className="metric-value">{value}</strong>
    {hint && <span className="text-xs text-muted">{hint}</span>}
  </div>
}
function SectionLabel({ children }: { children: ReactNode }) {
  return <h3 className="section-label">{children}</h3>
}
function JsonDump({ data }: { data: unknown }) {
  return <pre className="json-output">{JSON.stringify(data, null, 2)}</pre>
}
function EmptyPanel({ icon: Icon, title, message }: {
  icon: typeof Search; title: string; message: string
}) {
  return <div className="empty-panel">
    <Icon size={24} className="mb-3 text-muted" strokeWidth={1.6}/>
    <strong className="text-white">{title}</strong>
    <p className="max-w-sm text-center text-sm leading-6 text-muted">{message}</p>
  </div>
}

function Evidence({ hit }: { hit: SearchHit }) {
  return <div className="space-y-5">
    <div className="rounded-xl border border-line bg-background/50 p-4">
      <SectionLabel>Trecho recuperado</SectionLabel>
      <pre className="code-output">{hit.content}</pre>
    </div>
    <section className="space-y-2">
      <SectionLabel>Provenance e identidade</SectionLabel>
      <div className="key-value"><span>Source ref</span><code className="break-all text-accent">{hit.source_ref}</code></div>
      <div className="key-value"><span>Revision</span><code>{hit.revision ?? 'não informada'}</code></div>
      <div className="key-value"><span>Checksum</span><code className="break-all">{hit.checksum ?? 'não informado'}</code></div>
      <div className="key-value"><span>Linhas</span><code>{hit.line_start ?? '?'}–{hit.line_end ?? '?'}</code></div>
      <div className="key-value"><span>Tipo / trust</span><code>{hit.source_type ?? '—'} / {hit.trust_level ?? '—'}</code></div>
      <div className="key-value"><span>Ranking score</span><code>{String(hit.score)} (não é autoridade)</code></div>
      {hit.query_alignment_score !== undefined && <div className="key-value"><span>Alignment</span><code>{hit.query_alignment_score}</code></div>}
    </section>
    <section className="space-y-3">
      <SectionLabel>QueryMatchEvidence <span className="count">{hit.query_matches?.length ?? 0}</span></SectionLabel>
      {hit.query_matches?.length ? hit.query_matches.map((match, index) =>
        <div key={index} className="rounded-xl border border-line p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="chip chip-teal">{match.match_type}</span>
            {match.channels.map(channel => <span className="chip" key={channel}>{channel}</span>)}
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {match.matched_values.map((value, valueIndex) => <code key={valueIndex} className="rounded bg-white/5 px-2 py-1 text-xs">{value}</code>)}
          </div>
          <div className="mt-3 text-xs text-muted">Origem: {match.provenance ?? 'não informada'} · Signal: {shorten(text(match.signal), 140)}</div>
          {match.strength !== undefined && <div className="mt-1 text-xs text-muted">Strength observada: {match.strength}</div>}
        </div>,
      ) : <p className="text-sm text-muted">O backend não retornou query-match evidence para este candidato.</p>}
    </section>
    <section className="space-y-2">
      <SectionLabel>Observações de evidência <span className="count">{hit.observations?.length ?? 0}</span></SectionLabel>
      {hit.observations?.length ? hit.observations.map((observation, index) =>
        <div key={index} className="key-value"><span>{text(observation.category)} · {text(observation.subtype)}</span><code className="break-all text-white">{text(observation.value)}</code></div>,
      ) : <p className="text-sm text-muted">Nenhuma observação tipada informada.</p>}
    </section>
    <section className="space-y-2">
      <SectionLabel>Relacionamentos <span className="count">{hit.relationships?.length ?? 0}</span></SectionLabel>
      {hit.relationships?.length ? hit.relationships.map((relation, index) =>
        <div key={index} className="key-value"><span>{text(relation.type)}</span><code className="break-all">{text(relation.target)}</code></div>,
      ) : <p className="text-sm text-muted">Nenhuma relação informada; não inferimos arestas.</p>}
    </section>
    {!!hit.reasons?.length && <section><SectionLabel>Razões observadas</SectionLabel><div className="space-y-1">{hit.reasons.map((reason, index) => <p key={index} className="text-xs text-muted">{reason}</p>)}</div></section>}
  </div>
}

function TelemetryPanel({ data }: { data?: RetrievalTelemetry }) {
  if (!data) return <EmptyPanel icon={Activity} title="Telemetry indisponível" message="A resposta não possui telemetry. Nenhuma métrica foi estimada."/>
  return <div className="space-y-5">
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Metric label="Modo efetivo" value={data.mode}/>
      <Metric label="Examinados" value={data.candidates_examined}/>
      <Metric label="Retornados" value={data.candidates_returned}/>
      <Metric label="Caracteres" value={data.returned_chars.toLocaleString('pt-BR')}/>
    </div>
    <div className="rounded-xl border border-line bg-background/40 p-4">
      <SectionLabel>Estágios de aquisição</SectionLabel>
      {data.stages?.length ? <div className="mt-3 space-y-3">{data.stages.map((stage, index) => {
        const total = Math.max(1, data.stages.reduce((sum, item) => sum + item.duration_ms, 0))
        return <div key={index}>
          <div className="mb-1 flex items-center justify-between text-sm"><span>{stage.name}</span><code className="text-muted">{stage.duration_ms} ms</code></div>
          <div className="h-1.5 rounded-full bg-line"><div className="h-1.5 rounded-full bg-accent/80" style={{ width: String(100 * stage.duration_ms / total) + '%' }}/></div>
          <p className="mt-1 text-xs text-muted">{stage.candidates_in ?? '—'} in · {stage.candidates_out ?? '—'} out</p>
        </div>
      })}</div> : <p className="mt-3 text-sm text-muted">Sem estágios publicados.</p>}
      {data.time_to_first_candidate_ms !== undefined && <p className="mt-3 text-xs text-muted">Time to first candidate: {data.time_to_first_candidate_ms} ms</p>}
    </div>
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="rounded-xl border border-line p-4"><SectionLabel>Fingerprints</SectionLabel><JsonDump data={data.fingerprints ?? {}}/></div>
      <div className="rounded-xl border border-line p-4"><SectionLabel>Budget / Cache</SectionLabel><JsonDump data={{ budget: data.budget ?? {}, cache: data.cache ?? {} }}/></div>
    </div>
    <div><SectionLabel>Metadata de telemetry</SectionLabel><JsonDump data={data.metadata ?? {}}/></div>
  </div>
}

function ResultDetails({ hit, repository, boundRevision, targetId }: { hit: SearchHit; repository: string; boundRevision: string | null; targetId: string }) {
  // A result revision is observed provenance, not authority over materialized workspace bytes.
  // SourceReadRequest.revision is an exact precondition on the authorized binding only.
  const revisionPrecondition = hit.revision && hit.revision === boundRevision ? hit.revision : undefined
  const [tab, setTab] = useState('evidence')
  const source = useQuery({
    queryKey: ['source', targetId, repository, hit.source_ref, revisionPrecondition, hit.line_start, hit.line_end],
    queryFn: () => readSource(sourcePayloadForHit(hit, repository, boundRevision, targetId)),
    enabled: tab === 'source',
    retry: false,
  })
  return <div className="h-full">
    <div className="mb-4 flex min-w-0 flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="mb-2 flex items-center gap-2"><span className="chip chip-teal">{hit.knowledge_type}</span><span className="text-xs text-muted">Candidato selecionado</span></div>
        <h3 className="break-all text-base font-semibold text-white">{hit.source || hit.source_ref}</h3>
        <p className="mt-1 break-all font-mono text-xs text-muted">{hit.source_ref}</p>
      </div>
      <Button variant="secondary" size="sm" onClick={() => void navigator.clipboard?.writeText(JSON.stringify(hit, null, 2))}><Copy size={14}/> Copiar JSON</Button>
    </div>
    <Tabs.Root value={tab} onValueChange={setTab}>
      <Tabs.List className="tab-list" aria-label="Detalhes do candidato">
        <Tabs.Trigger className="tab" value="evidence">Evidência</Tabs.Trigger>
        <Tabs.Trigger className="tab" value="source">Código-fonte</Tabs.Trigger>
        <Tabs.Trigger className="tab" value="raw">JSON bruto</Tabs.Trigger>
      </Tabs.List>
      <Tabs.Content value="evidence" className="py-5"><Evidence hit={hit}/></Tabs.Content>
      <Tabs.Content value="source" className="py-5">
        {hit.revision && !revisionPrecondition && <p className="mb-3 rounded-lg border border-amber-700/40 bg-amber-900/20 p-3 text-xs text-amber-100">
          Fonte indexada na revisão {shorten(hit.revision, 18)}. A revisão do workspace não está vinculada ou não coincide: esta leitura representa os bytes atuais autorizados, não prova equivalência ao trecho indexado.
        </p>}
        {source.isLoading ? <p className="text-sm text-muted">Lendo arquivo autorizado...</p> :
          source.isError ? <div className="error-panel">{source.error.message}</div> : source.data ?
          <div className="rounded-xl border border-line bg-background/50 p-4">
            <div className="mb-3 flex flex-wrap justify-between gap-2 text-xs text-muted">
              <span>{source.data.path}</span>
              <span>Linhas {source.data.line_start}–{source.data.line_end} / {source.data.total_lines}{source.data.truncated && ' · trecho limitado'}</span>
            </div>
            <pre className="code-output">{source.data.content}</pre>
          </div> : null}
      </Tabs.Content>
      <Tabs.Content value="raw" className="py-5"><JsonDump data={hit}/></Tabs.Content>
    </Tabs.Root>
  </div>
}

function ResultList({ response, selected, onSelect }: {
  response: SearchResponse; selected: string | null; onSelect: (id: string) => void
}) {
  if (!response.results.length) {
    return <EmptyPanel icon={Search} title="Nenhum candidato" message="A consulta foi executada, mas não retornou evidências neste escopo. Ajuste sua intenção e execute novamente."/>
  }
  return <div className="space-y-2" aria-label="Resultados de busca">
    {response.results.map((hit, index) => <button
      type="button" key={hit.id} aria-pressed={selected === hit.id}
      onClick={() => onSelect(hit.id)}
      className={'result-card w-full text-left ' + (selected === hit.id ? 'result-active' : '')}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-xs text-muted">#{String(index + 1).padStart(2, '0')} · {hit.knowledge_type}</span>
        <ChevronRight size={15} className="shrink-0 text-muted"/>
      </div>
      <div className="mt-2 break-all text-sm font-semibold text-white">{shorten(hit.source || hit.source_ref, 62)}</div>
      <p className="mt-1 line-clamp-2 break-words text-xs leading-5 text-muted">{shorten(hit.content.replace(/\s+/g, ' '), 140)}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
        <span className="chip">score {hit.score}</span>
        {hit.line_start !== undefined && <span className="chip">L{hit.line_start}–{hit.line_end ?? '?'}</span>}
        {!!hit.query_matches?.length && <span className="chip chip-teal">{hit.query_matches.length} matches</span>}
      </div>
    </button>)}
  </div>
}

export function App() {
  const config = useQuery({ queryKey: ['config'], queryFn: getConfig, retry: false })
  const [query, setQuery] = useState(initialQuery)
  const [intentText, setIntentText] = useState(initialIntent)
  const [limit, setLimit] = useState(10)
  const [mode, setMode] = useState<SearchPayload['mode']>('discovery')
  const [knowledgeTypes, setKnowledgeTypes] = useState('')
  const [parseError, setParseError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activePanel, setActivePanel] = useState<'results' | 'telemetry'>('results')
  const [intentView, setIntentView] = useState<'form' | 'json'>('form')
  const [editorEpoch, setEditorEpoch] = useState(0)
  const [presetId, setPresetId] = useState(intentPresets[0].id)
  const [presetAction, setPresetAction] = useState<'replace' | 'merge'>('replace')
  const [importOpen, setImportOpen] = useState(false)
  const [importDraft, setImportDraft] = useState('')
  const [previewOpen, setPreviewOpen] = useState(false)
  const [selectedRepository, setSelectedRepository] = useState('')
  const [selectedTargetId, setSelectedTargetId] = useState('default')
  const [runs, setRuns] = useState<RunManifest[]>([])
  const sequence = useRef(0)
  const run = useMutation({ mutationFn: search, onSuccess: (response) => {
    setSelectedId(response.results[0]?.id ?? null)
    setActivePanel('results')
  } })
  const targets: ExplorerTarget[] = config.data?.targets?.length ? config.data.targets : config.data ? [{
    id: 'default', repository: config.data.repository, backend: config.data.backend,
    collection: null, revision: config.data.revision, capabilities: config.data.capabilities,
    retrieval: config.data.retrieval,
  }] : []
  const repositories = Array.from(new Set(targets.map(target => target.repository)))
  const activeRepository = repositories.includes(selectedRepository) ? selectedRepository : (config.data?.repository ?? '')
  const repositoryTargets = targets.filter(target => target.repository === activeRepository)
  const activeTarget = repositoryTargets.find(target => target.id === selectedTargetId) ?? repositoryTargets[0]
  const resultTarget = targets.find(target => target.id === run.variables?.target_id) ?? activeTarget
  const [readinessTargetId, setReadinessTargetId] = useState<string | null>(null)
  const readiness = useQuery({
    queryKey: ['readiness', readinessTargetId],
    queryFn: () => getReadiness(readinessTargetId ?? 'default'),
    enabled: readinessTargetId !== null,
    retry: false,
    staleTime: 0,
  })
  const response = run.data
  const selectedHit = response?.results.find(hit => hit.id === selectedId) ?? response?.results[0]
  function changeRepository(value: string) {
    setSelectedRepository(value)
    setSelectedTargetId(targets.find(target => target.repository === value)?.id ?? 'default')
    setSelectedId(null)
    run.reset()
    setReadinessTargetId(null)
  }
  function changeTarget(value: string) {
    setSelectedTargetId(value)
    setSelectedId(null)
    run.reset()
    setReadinessTargetId(null)
  }
  function switchIntentView(next: 'form' | 'json') {
    if (next === 'form') {
      try {
        validateVisualIntent(intentText)
      } catch {
        setParseError('Corrija o JSON antes de abrir o formulário; suas edições foram preservadas.')
        return
      }
    }
    setParseError('')
    setIntentView(next)
  }

  function updateQuery(value: string) {
    setQuery(value)
    try {
      const parsed = parseIntent(intentText)
      setIntentText(JSON.stringify({ ...parsed, canonical_query: value }, null, 2))
      setParseError('')
    } catch {
      // Preserve the invalid JSON instead of silently overwriting user edits.
    }
  }
  function updateIntent(value: string) {
    setIntentText(value)
    setParseError('')
    try {
      const parsed = parseIntent(value)
      if (typeof parsed.canonical_query === 'string') setQuery(parsed.canonical_query)
    } catch {
      // Editing incomplete JSON is expected.
    }
  }
  function executeSearch(request: SearchPayload) {
    const target = targets.find(candidate => candidate.id === request.target_id
      && candidate.repository === request.repository)
    if (!target) throw new Error('Destino não autorizado no catálogo atual')
    const id = 'run-' + new Date().toISOString() + '-' + (++sequence.current)
    run.mutate(request, {
      onSuccess: response => {
        try {
          const captured = captureRun(id, request, target, response, null)
          setRuns(previous => prependRun(previous, captured))
        } catch (error) {
          setParseError(error instanceof Error ? error.message : 'Falha ao registrar histórico')
        }
      },
      onError: error => {
        try {
          const category = error instanceof ApiError ? error.category : 'retrieval_failed'
          setRuns(previous => prependRun(previous, captureRun(id, request, target, null, category)))
        } catch {
          // A bad envelope must not change retrieval error handling.
        }
      },
    })
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    try {
      const intent = parseValidatedIntent(intentText)
      if (previewError) throw new Error(previewError)
      setParseError('')
      executeSearch({
        repository: activeTarget?.repository ?? '',
        target_id: activeTarget?.id,
        limit, mode,
        knowledge_types: knowledgeTypes.split(',').map(item => item.trim()).filter(Boolean),
        intent,
      })
    } catch (error) {
      setParseError(error instanceof Error ? error.message : 'SearchIntent JSON inválido')
    }
  }
  function inspectRecordedCandidate(record: RunManifest, recorded: RecordedHit): boolean {
    // A recorded historical source never authorizes a read. Only navigate a
    // verified current response on the same operator-bound target/provenance.
    if (!run.data || !run.variables || run.variables.target_id !== record.target.id
      || run.variables.repository !== record.target.repository
      || !recorded.source_ref || !recorded.checksum) return false
    const found = run.data.results.find(hit => hit.source_ref === recorded.source_ref
      && hit.checksum === recorded.checksum && hit.revision === recorded.revision)
    if (!found) return false
    setSelectedId(found.id)
    setActivePanel('results')
    return true
  }
  function restoreRecordedQuery(record: RunManifest) {
    const target = targets.find(candidate => candidate.id === record.target.id
      && candidate.repository === record.target.repository)
    // A historical target never becomes a new authorized target.
    if (target) {
      setSelectedRepository(target.repository)
      setSelectedTargetId(target.id)
    }
    replaceIntent(record.request.intent)
    setMode(record.request.mode)
    setLimit(record.request.limit)
    setKnowledgeTypes(record.request.knowledge_types.join(', '))
    run.reset()
    setReadinessTargetId(null)
  }
  async function verifyAndReplay(record: RunManifest): Promise<void> {
    const target = targets.find(candidate => candidate.id === record.target.id)
    if (!target) throw new Error('Target ausente no catálogo autorizado')
    const report = await getReadiness(target.id)
    const gate = replayGate(record, targets, report)
    if (!gate.allowed) throw new Error(gate.reasons.join('; '))
    executeSearch(record.request)
  }

  function replaceIntent(next: Record<string, unknown>) {
    setIntentText(JSON.stringify(next, null, 2))
    setQuery(typeof next.canonical_query === 'string' ? next.canonical_query : '')
    setEditorEpoch(epoch => epoch + 1)
    setParseError('')
  }
  const selectedPreset = intentPresets.find(item => item.id === presetId) ?? intentPresets[0]
  function usePreset() {
    try {
      const next = presetAction === 'replace'
        ? applyPreset({ schema_version: 2 }, selectedPreset, 'replace')
        : applyPreset(parseValidatedIntent(intentText), selectedPreset, 'merge')
      replaceIntent(next)
    } catch (error) {
      setParseError(error instanceof Error ? error.message : 'Preset incompatível')
    }
  }
  function importIntent() {
    try {
      const next = parseValidatedIntent(importDraft)
      replaceIntent(next)
      if (next.schema_version !== 2) setIntentView('json')
      setImportOpen(false)
      setImportDraft('')
    } catch (error) {
      setParseError(error instanceof Error ? error.message : 'Importação inválida')
    }
  }
  function exportIntent() {
    try {
      const next = parseValidatedIntent(intentText)
      const blob = new Blob([JSON.stringify(next, null, 2) + '\n'], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'fetchcore-search-intent-v2.json'
      anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 0)
      setParseError('')
    } catch (error) {
      setParseError(error instanceof Error ? error.message : 'Exportação inválida')
    }
  }
  let finalPayload: SearchPayload | null = null
  let previewError: string | null = null
  try {
    finalPayload = {
      target_id: activeTarget?.id,
      repository: activeTarget?.repository ?? '',
      mode,
      limit,
      knowledge_types: knowledgeTypes.split(',').map(value => value.trim()).filter(Boolean),
      intent: parseValidatedIntent(intentText),
    }
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) previewError = 'Max results deve estar entre 1 e 100'
    if (!activeTarget) previewError = 'Nenhum destino autorizado foi carregado'
  } catch (error) {
    previewError = error instanceof Error ? error.message : 'SearchIntent inválido'
  }
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="mb-10 flex items-center gap-3">
        <div className="logo-mark"><Layers3 size={22}/></div>
        <div><div className="text-lg font-extrabold tracking-tight text-white">Fetch<span className="text-accent">Core</span></div><div className="font-mono text-[10px] tracking-[.22em] text-muted">EXPLORER / 0.1</div></div>
      </div>
      <div className="nav-caption">WORKSPACE</div>
      <div className="nav-selected"><Search size={17}/> Search Lab <span className="ml-auto rounded bg-accent/15 px-2 py-0.5 font-mono text-[10px] text-accent">MVP</span></div>
      <div className="nav-inert"><Network size={17}/> Graph Explorer <span className="ml-auto text-[10px]">NEXT</span></div>
      <div className="nav-caption mt-10">AMBIENTE</div>
      <div className="space-y-3 rounded-xl border border-line bg-surface/40 p-4">
        <div className="flex items-center gap-2 text-xs"><span className={'status-dot ' + (config.isSuccess ? 'bg-accent' : 'bg-amber-400')}/>{config.isSuccess ? 'API conectada' : config.isLoading ? 'Conectando...' : 'Indisponível'}</div>
        <p className="break-all font-mono text-xs text-white">{activeTarget?.repository ?? 'repository indisponível'}</p>
        <div className="flex items-center gap-2 text-xs text-muted"><Database size={13}/>{activeTarget?.backend ?? '—'} · {activeTarget?.collection ?? 'sem coleção declarada'}</div>
        <div className="flex items-center gap-2 text-xs text-muted"><GitBranch size={13}/>{shorten(activeTarget?.revision ?? 'working tree', 20)}</div>
      </div>
      <div className="mt-auto rounded-xl border border-line p-4">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-white"><ShieldCheck size={15} className="text-accent"/> Retrieval ≠ Authority</div>
        <p className="text-xs leading-5 text-muted">O Explorer inspeciona candidatos. Scores e matches não são prova de satisfação semântica.</p>
      </div>
    </aside>

    <main className="main-content">
      <div className="page-topbar">
        <div className="flex items-center gap-2 text-xs text-muted"><span>Ferramentas</span><ChevronRight size={13}/><span className="text-white">Search Lab</span></div>
        <div className="flex items-center gap-2 rounded-full border border-line px-3 py-1.5 font-mono text-[10px] text-muted"><span className="status-dot bg-accent"/> LOCAL DEVELOPMENT</div>
      </div>
      <div className="mx-auto w-full max-w-[1680px] px-5 py-8 lg:px-9">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="eyebrow mb-2 flex items-center gap-2"><Sparkles size={13}/> RETRIEVAL WORKBENCH <span className="h-px w-9 bg-line"/></div>
            <h1 className="text-3xl font-bold tracking-tight text-white lg:text-4xl">Search <span className="text-accent">Lab</span><span className="ml-3 align-middle text-base font-normal text-muted">/ Playground</span></h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">Investigue buscas reais, inspecione evidências tipadas e entenda o comportamento do backend — sem inferir autoridade.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {Object.entries(activeTarget?.capabilities ?? {}).filter(([,enabled]) => enabled).map(([capability]) => <span key={capability} className="chip chip-teal"><Check size={11}/>{capability}</span>)}
          </div>
        </div>
        {config.isError && <div role="alert" className="error-panel mb-5 flex items-start gap-3"><CircleAlert size={18}/><div><strong>API indisponível</strong><p className="mt-1 text-sm">{config.error.message}. Confirme que o FastAPI está rodando em 127.0.0.1:8765.</p></div></div>}

        <div className="grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
          <div className="min-w-0 self-start">
          <section className="panel" aria-label="Editor de busca">
            <div className="panel-heading"><div className="flex items-center gap-2"><TerminalSquare size={17} className="text-accent"/><h2>Configurar busca</h2></div><span className="chip">SearchIntent v2</span></div>
            <form className="space-y-5 p-5" onSubmit={submit}>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="field-label" htmlFor="repository-select">Repositório autorizado</label>
                  <select id="repository-select" className="field-input" value={activeRepository}
                    onChange={event => changeRepository(event.target.value)} disabled={!repositories.length}>
                    {repositories.map(repository => <option key={repository} value={repository}>{repository}</option>)}
                  </select>
                </div>
                <div>
                  <label className="field-label" htmlFor="collection-select">Coleção / índice configurado</label>
                  <select id="collection-select" className="field-input" value={activeTarget?.id ?? ''}
                    onChange={event => changeTarget(event.target.value)} disabled={!repositoryTargets.length}>
                    {repositoryTargets.map(target => <option key={target.id} value={target.id}>
                      {target.backend} · {target.collection ?? 'default'} ({target.id})
                    </option>)}
                  </select>
                </div>
              </div>
              <p className="text-xs leading-5 text-muted">Opções autorizadas pelo operador. A seleção troca o destino, não realiza busca federada.</p>
              <div>
                <label className="field-label" htmlFor="canonical-query">Canonical query</label>
                <textarea
                  id="canonical-query" rows={3} value={query}
                  onChange={event => updateQuery(event.target.value)}
                  placeholder="Descreva a evidência técnica que deseja recuperar"
                  className="field-input min-h-[92px] resize-y"
                />
                <p className="mt-2 text-xs text-muted">Texto da intenção declarada. Anchors e boolean expressions são editáveis abaixo.</p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className="field-label" htmlFor="search-mode">Search scope mode</label>
                  <select id="search-mode" className="field-input" value={mode} onChange={event => setMode(event.target.value as SearchPayload['mode'])}>
                    <option value="discovery">discovery</option><option value="targeted">targeted</option><option value="repository_source">repository_source</option>
                  </select>
                </div>
                <div><label className="field-label" htmlFor="search-limit">Max results</label>
                  <input id="search-limit" className="field-input" type="number" min={1} max={100} value={limit} onChange={event => setLimit(Number(event.target.value))}/>
                </div>
              </div>
              <div><label className="field-label" htmlFor="knowledge-types">Knowledge types <span className="font-normal normal-case text-muted">(opcional)</span></label>
                <input id="knowledge-types" value={knowledgeTypes} onChange={event => setKnowledgeTypes(event.target.value)} className="field-input" placeholder="code, documentation (separados por vírgula)"/>
              </div>
              <section className="space-y-3 rounded-xl border border-line bg-background/20 p-3" aria-label="Presets versionados">
                <div>
                  <strong className="text-xs text-white">Presets de SearchIntent v2</strong>
                  <p className="mt-1 text-xs text-muted">Escolha explicitamente o resultado: substituir todo o JSON ou combinar sem inventar operadores booleanos. Presets v1.</p>
                </div>
                <select className="field-input" aria-label="Preset" value={presetId}
                  onChange={event => setPresetId(event.target.value)}>
                  {intentPresets.map(preset => <option key={preset.id} value={preset.id}>{preset.name} · v{preset.version}</option>)}
                </select>
                <p className="text-xs text-muted">{selectedPreset.detail}</p>
                <fieldset className="flex flex-wrap gap-4 text-xs text-muted">
                  <legend className="mb-2">Operação explícita</legend>
                  <label className="flex items-center gap-1.5">
                    <input type="radio" name="preset-action" checked={presetAction === 'replace'}
                      onChange={() => setPresetAction('replace')}/> Substituir intenção
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input type="radio" name="preset-action" checked={presetAction === 'merge'}
                      onChange={() => setPresetAction('merge')}/> Combinar listas (sem OR/AND automático)
                  </label>
                </fieldset>
                <details>
                  <summary className="cursor-pointer text-xs text-accent">Prévia do preset (JSON v2)</summary>
                  <pre className="json-output mt-2">{JSON.stringify(selectedPreset.intent, null, 2)}</pre>
                </details>
                <button type="button" className="switch-button border border-line"
                  onClick={usePreset}>Aplicar preset</button>
              </section>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="switch-button border border-line"
                  onClick={() => { setImportOpen(open => !open); setParseError('') }}>Importar JSON</button>
                <button type="button" className="switch-button border border-line"
                  onClick={exportIntent}>Exportar JSON</button>
              </div>
              {importOpen && <section className="space-y-2 rounded-lg border border-line p-3">
                <label htmlFor="import-intent" className="field-label">Cole JSON completo para importar (até 100 KB)</label>
                <textarea id="import-intent" className="field-input min-h-[130px] font-mono text-xs"
                  value={importDraft} onChange={event => setImportDraft(event.target.value)}
                  placeholder='{"schema_version":2,"canonical_query":"..."}'/>
                <p className="text-xs text-muted">Substituir o editor só ocorre ao clicar em Confirmar importação. Nenhuma query é executada.</p>
                <button type="button" className="switch-button border border-line"
                  onClick={importIntent}>Confirmar importação</button>
              </section>}
              <div className="rounded-xl border border-line bg-background/30">
                <div className="flex gap-1 border-b border-line p-2" role="group" aria-label="Modo de edição da query">
                  <button type="button" className={'switch-button ' + (intentView === 'form' ? 'switch-active' : '')}
                    aria-pressed={intentView === 'form'} onClick={() => switchIntentView('form')}><SlidersHorizontal size={15}/> Formulário</button>
                  <button type="button" className={'switch-button ' + (intentView === 'json' ? 'switch-active' : '')}
                    aria-pressed={intentView === 'json'} onClick={() => switchIntentView('json')}><Braces size={15}/> JSON completo</button>
                </div>
                <div className="p-3">
                  {intentView === 'form' ? <SearchIntentForm key={editorEpoch} intentText={intentText} onChange={updateIntent}/> :
                    <div>
                      <CodeMirror aria-label="SearchIntent JSON" value={intentText} height="330px" theme="dark" extensions={editorExtensions} onChange={updateIntent}
                        basicSetup={{ lineNumbers: true, foldGutter: true }}/>
                      <p className="mt-2 text-xs leading-5 text-muted">Edite ou cole um SearchIntent v2 válido. O formulário preserva as expressões avançadas.</p>
                    </div>}
                </div>
              </div>
              <div className="rounded-lg border border-line bg-surface/50 px-3 py-2.5 text-xs text-muted">
                <div className="flex items-center justify-between gap-2">
                  <strong className="text-white">Operational readiness</strong>
                  <button type="button" className="switch-button border border-line"
                    onClick={() => {
                      setReadinessTargetId(activeTarget?.id ?? 'default')
                      if (readinessTargetId === (activeTarget?.id ?? 'default')) void readiness.refetch()
                    }}>
                    Inspecionar índice
                  </button>
                </div>
                {readiness.isFetching && <p className="mt-2">Verificando serviço, schema, snapshot, revisão e vetores…</p>}
                {readiness.isError && <p role="alert" className="mt-2 text-rose-300">{readiness.error.message}</p>}
                {readiness.data && !readiness.isFetching && readinessTargetId === activeTarget?.id &&
                  <div className="mt-3 space-y-2" aria-label="Resultado de readiness">
                    <div className="flex flex-wrap gap-2">
                      <span className="chip">{readiness.data.ready ? 'Ready' : 'Not ready'}</span>
                      <span className="chip">{readiness.data.backend} · {readiness.data.mode}</span>
                    </div>
                    {Object.entries(readiness.data.checks).map(([name, fact]) =>
                      <div className="flex justify-between gap-3" key={name}>
                        <span>{name}</span>
                        <span className="font-mono" title={fact.category ?? ''}>{fact.state}{fact.category ? ' · ' + fact.category : ''}</span>
                      </div>)}
                    <p>Revisão indexada: {readiness.data.indexed_revision ?? 'não informada'} · solicitada: {readiness.data.requested_revision ?? 'não informada'}</p>
                    <p>Readiness significa condições operacionais verificadas, não qualidade ou satisfação da busca.</p>
                  </div>}
              </div>
              <div className="rounded-lg border border-line bg-surface/50 px-3 py-2.5 text-xs text-muted">
                <div className="mb-1 flex items-center gap-2 font-semibold text-white"><Workflow size={14}/> Retrieval policy</div>
                <code className="break-all">{activeTarget?.retrieval ? text(activeTarget.retrieval) : 'configuração padrão do backend'}</code>
                <p className="mt-2">Lexical / dense / hybrid vêm da configuração do destino autorizado. Selecionar outro índice não gera embeddings.</p>
              </div>
              <section className="rounded-lg border border-line bg-background/30 p-3" aria-label="Prévia de payload">
                <button type="button" className="switch-button border border-line"
                  onClick={() => setPreviewOpen(value => !value)}>
                  {previewOpen ? 'Ocultar payload final' : 'Inspecionar payload final'}
                </button>
                {previewOpen && (previewError
                  ? <p className="mt-2 text-xs text-rose-300" role="alert">Payload bloqueado: {previewError}</p>
                  : <pre className="json-output mt-2" aria-label="JSON final da busca">{JSON.stringify(finalPayload, null, 2)}</pre>)}
                <p className="mt-2 text-xs text-muted">Scope e budget não são SearchIntent. Payload exibido inclui o destino autorizado, modo, limite e knowledge types.</p>
              </section>
              {parseError && <div className="error-panel" role="alert">{parseError}</div>}
              {run.isError && <div className="error-panel" role="alert"><strong>Busca falhou:</strong> {run.error.message}</div>}
              <Button type="submit" className="w-full py-3" disabled={!config.data || run.isPending} data-testid="run-search">
                {run.isPending ? <LoaderCircle size={16} className="animate-spin"/> : <Play size={16}/>}
                {run.isPending ? 'Buscando evidências...' : 'Executar busca'} <ArrowRight size={16} className="ml-auto"/>
              </Button>
              <div className="flex items-center justify-between text-[11px] text-muted"><span>Destino: {activeTarget?.id ?? 'não configurado'}</span><span>limit 1–100</span></div>
            </form>
          </section>
          <div className="panel mt-5">
            <RunHistoryPanel runs={runs} targets={targets} busy={run.isPending}
              onChange={setRuns} onRestore={restoreRecordedQuery} onReplay={verifyAndReplay}/>
          </div>
          </div>

          <div className="min-w-0 space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Metric label="Candidatos" value={response?.results.length ?? '—'} hint="retornados"/>
              <Metric label="Duração" value={response ? String(response.duration_ms) + ' ms' : '—'} hint="response"/>
              <Metric label="Examinados" value={response?.telemetry?.candidates_examined ?? '—'} hint="telemetry"/>
              <Metric label="Backend" value={response?.backend ?? activeTarget?.backend ?? '—'} hint="destino autorizado"/>
            </div>
            <section className="panel min-h-[640px]">
              <div className="panel-heading flex-wrap gap-3">
                <div className="flex items-center gap-2"><ListFilter size={17} className="text-accent"/><h2>Explorar evidências</h2>{response && <span className="chip">{response.status}</span>}</div>
                <div className="flex items-center gap-1 rounded-lg border border-line bg-background/50 p-1">
                  <button type="button" className={'switch-button ' + (activePanel === 'results' ? 'switch-active' : '')} onClick={() => setActivePanel('results')}><FileCode2 size={14}/> Resultados</button>
                  <button type="button" className={'switch-button ' + (activePanel === 'telemetry' ? 'switch-active' : '')} onClick={() => setActivePanel('telemetry')}><Gauge size={14}/> Telemetry</button>
                </div>
              </div>
              {!response ? <div className="p-6"><EmptyPanel icon={Search} title="Seu laboratório está pronto" message="Declare uma intenção, execute a busca e explore os candidatos retornados pelo FetchCore. Nenhum resultado é simulado."/></div> :
                activePanel === 'telemetry' ? <div className="p-5"><TelemetryPanel data={response.telemetry}/></div> :
                <div className="grid min-h-[580px] min-w-0 lg:grid-cols-[290px_minmax(0,1fr)]">
                  <div className="min-w-0 border-b border-line p-3 lg:border-b-0 lg:border-r">
                    <div className="mb-3 flex items-center justify-between px-2 text-xs text-muted"><span>{response.results.length} candidatos</span><ArrowDownRight size={13}/></div>
                    <ResultList response={response} selected={selectedHit?.id ?? null} onSelect={setSelectedId}/>
                  </div>
                  <div className="min-w-0 p-5">{selectedHit ? <ResultDetails key={selectedHit.id} hit={selectedHit} repository={resultTarget?.repository ?? ''} boundRevision={resultTarget?.revision ?? null} targetId={resultTarget?.id ?? 'default'}/> : <EmptyPanel icon={Code2} title="Nenhuma evidência selecionada" message="Execute outra busca ou ajuste os critérios."/>}</div>
                </div>}
            </section>
            {response && <section className="panel p-4">
              <div className="mb-3 flex items-center justify-between"><span className="flex items-center gap-2 text-sm font-semibold text-white"><Activity size={16} className="text-accent"/> Diagnóstico de execução</span><span className="text-xs text-muted">{response.backend} · {response.status}</span></div>
              <div className="grid gap-3 md:grid-cols-3">
                <div className="diagnostic"><Clock3 size={16}/><span>Duration</span><strong>{response.duration_ms} ms</strong></div>
                <div className="diagnostic"><Fingerprint size={16}/><span>Fingerprints</span><strong>{Object.keys(response.telemetry?.fingerprints ?? {}).length}</strong></div>
                <div className="diagnostic"><Layers3 size={16}/><span>Stages</span><strong>{response.telemetry?.stages?.length ?? 0}</strong></div>
              </div>
              <details className="mt-3 rounded-lg border border-line p-3"><summary className="cursor-pointer text-xs font-semibold text-muted">Metadata da resposta (original)</summary><JsonDump data={response.metadata}/></details>
            </section>}
          </div>
        </div>
        <ComparisonLab runs={runs} onInspect={inspectRecordedCandidate}/>
        <footer className="mt-8 flex flex-wrap items-center justify-between gap-2 border-t border-line py-5 text-xs text-muted"><span>FetchCore · Repositories → evidence</span><span>Local-only tooling · Sem agentes · Sem authority</span></footer>
      </div>
    </main>
  </div>
}
