import { useState, type ReactNode, type FormEvent } from 'react'
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
import { getConfig, parseIntent, readSource, search, sourcePayloadForHit } from './api'
import type { SearchHit, SearchPayload, SearchResponse, RetrievalTelemetry } from './types'

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

function ResultDetails({ hit, repository, boundRevision }: { hit: SearchHit; repository: string; boundRevision: string | null }) {
  // A result revision is observed provenance, not authority over materialized workspace bytes.
  // SourceReadRequest.revision is an exact precondition on the authorized binding only.
  const revisionPrecondition = hit.revision && hit.revision === boundRevision ? hit.revision : undefined
  const [tab, setTab] = useState('evidence')
  const source = useQuery({
    queryKey: ['source', repository, hit.source_ref, revisionPrecondition, hit.line_start, hit.line_end],
    queryFn: () => readSource(sourcePayloadForHit(hit, repository, boundRevision)),
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
  const [editorOpen, setEditorOpen] = useState(true)
  const run = useMutation({ mutationFn: search, onSuccess: (response) => {
    setSelectedId(response.results[0]?.id ?? null)
    setActivePanel('results')
  } })
  const response = run.data
  const selectedHit = response?.results.find(hit => hit.id === selectedId) ?? response?.results[0]

  function updateQuery(value: string) {
    setQuery(value)
    try {
      const parsed = parseIntent(intentText)
      setIntentText(JSON.stringify({ ...parsed, schema_version: 2, canonical_query: value }, null, 2))
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
  function submit(event: FormEvent) {
    event.preventDefault()
    try {
      const intent = parseIntent(intentText)
      setParseError('')
      run.mutate({
        repository: config.data?.repository ?? '',
        limit, mode,
        knowledge_types: knowledgeTypes.split(',').map(item => item.trim()).filter(Boolean),
        intent,
      })
    } catch (error) {
      setParseError(error instanceof Error ? error.message : 'SearchIntent JSON inválido')
    }
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
        <p className="break-all font-mono text-xs text-white">{config.data?.repository ?? 'repository indisponível'}</p>
        <div className="flex items-center gap-2 text-xs text-muted"><Database size={13}/>{config.data?.backend ?? '—'}</div>
        <div className="flex items-center gap-2 text-xs text-muted"><GitBranch size={13}/>{shorten(config.data?.revision ?? 'working tree', 20)}</div>
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
            {Object.entries(config.data?.capabilities ?? {}).filter(([,enabled]) => enabled).map(([capability]) => <span key={capability} className="chip chip-teal"><Check size={11}/>{capability}</span>)}
          </div>
        </div>
        {config.isError && <div role="alert" className="error-panel mb-5 flex items-start gap-3"><CircleAlert size={18}/><div><strong>API indisponível</strong><p className="mt-1 text-sm">{config.error.message}. Confirme que o FastAPI está rodando em 127.0.0.1:8765.</p></div></div>}

        <div className="grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
          <section className="panel self-start" aria-label="Editor de busca">
            <div className="panel-heading"><div className="flex items-center gap-2"><TerminalSquare size={17} className="text-accent"/><h2>Configurar busca</h2></div><span className="chip">SearchIntent v2</span></div>
            <form className="space-y-5 p-5" onSubmit={submit}>
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
              <div className="rounded-xl border border-line bg-background/30">
                <button type="button" className="flex w-full items-center justify-between p-3 text-sm font-semibold text-white" onClick={() => setEditorOpen(!editorOpen)} aria-expanded={editorOpen}>
                  <span className="flex items-center gap-2"><Braces size={16} className="text-blue"/> Editor JSON · SearchIntent</span><SlidersHorizontal size={16} className="text-muted"/>
                </button>
                {editorOpen && <div className="border-t border-line p-2">
                  <CodeMirror aria-label="SearchIntent JSON" value={intentText} height="295px" theme="dark" extensions={editorExtensions} onChange={updateIntent} basicSetup={{ lineNumbers: true, foldGutter: true }}/>
                  <div className="px-2 pt-2 text-xs text-muted">O JSON enviado é validado pelo contrato Python do FetchCore.</div>
                </div>}
              </div>
              <div className="rounded-lg border border-line bg-surface/50 px-3 py-2.5 text-xs text-muted">
                <div className="mb-1 flex items-center gap-2 font-semibold text-white"><Workflow size={14}/> Retrieval policy</div>
                <code className="break-all">{config.data?.retrieval ? text(config.data.retrieval) : 'configuração padrão do backend'}</code>
                <p className="mt-2">Lexical / dense / hybrid vêm da configuração do servidor. Não alteramos o backend pela UI.</p>
              </div>
              {parseError && <div className="error-panel" role="alert">{parseError}</div>}
              {run.isError && <div className="error-panel" role="alert"><strong>Busca falhou:</strong> {run.error.message}</div>}
              <Button type="submit" className="w-full py-3" disabled={!config.data || run.isPending} data-testid="run-search">
                {run.isPending ? <LoaderCircle size={16} className="animate-spin"/> : <Play size={16}/>}
                {run.isPending ? 'Buscando evidências...' : 'Executar busca'} <ArrowRight size={16} className="ml-auto"/>
              </Button>
              <div className="flex items-center justify-between text-[11px] text-muted"><span>Repository binding autorizado</span><span>limit 1–100</span></div>
            </form>
          </section>

          <div className="min-w-0 space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Metric label="Candidatos" value={response?.results.length ?? '—'} hint="retornados"/>
              <Metric label="Duração" value={response ? String(response.duration_ms) + ' ms' : '—'} hint="response"/>
              <Metric label="Examinados" value={response?.telemetry?.candidates_examined ?? '—'} hint="telemetry"/>
              <Metric label="Backend" value={response?.backend ?? config.data?.backend ?? '—'} hint="operador define"/>
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
                  <div className="min-w-0 p-5">{selectedHit ? <ResultDetails key={selectedHit.id} hit={selectedHit} repository={config.data?.repository ?? ''} boundRevision={config.data?.revision ?? null}/> : <EmptyPanel icon={Code2} title="Nenhuma evidência selecionada" message="Execute outra busca ou ajuste os critérios."/>}</div>
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
        <footer className="mt-8 flex flex-wrap items-center justify-between gap-2 border-t border-line py-5 text-xs text-muted"><span>FetchCore · Repositories → evidence</span><span>Local-only tooling · Sem agentes · Sem authority</span></footer>
      </div>
    </main>
  </div>
}
