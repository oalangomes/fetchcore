import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'

vi.mock('@uiw/react-codemirror', () => ({
  default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) =>
    <textarea aria-label="SearchIntent JSON" value={value} onChange={event => onChange(event.target.value)}/>,
}))

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    if (path === '/api/config') return { ok: true, json: async () => ({
      repository: 'demo/catalog', backend: 'local-semantic', revision: null, retrieval: null,
      capabilities: { lexical: true, dense: false }, max_results: 100,
      targets: [
        { id: 'default', repository: 'demo/catalog', backend: 'local-semantic', collection: 'default-collection', revision: null, capabilities: { lexical: true }, retrieval: null },
        { id: 'alternate', repository: 'demo/catalog', backend: 'local-semantic', collection: 'other-collection', revision: null, capabilities: { lexical: true }, retrieval: null },
        { id: 'other-repo', repository: 'demo/other', backend: 'local-semantic', collection: 'other-repo-collection', revision: null, capabilities: { lexical: true }, retrieval: null },
      ],
    }) }
    if (path.startsWith('/api/readiness?target_id=')) return { ok: true, json: async () => ({
      schema_version: 1, status: 'not_ready', ready: false, backend: 'local-semantic',
      repository: 'demo/catalog', indexed_revision: null, requested_revision: 'head', mode: 'lexical',
      checks: { service: { state: 'ready' }, snapshot: { state: 'absent', category: 'snapshot_missing' },
        currentness: { state: 'absent', category: 'snapshot_missing' } }, fingerprints: {},
    }) }
    if (path === '/api/search') {
      const request = JSON.parse(String(options?.body))
      return { ok: true, json: async () => ({
        backend: 'local-semantic', status: 'ok', duration_ms: 7, metadata: {},
        results: [{ id: '1', source: 'eligibility.py', source_ref: 'repository://demo/catalog/eligibility.py',
          knowledge_type: 'code', score: 1.3, revision: 'indexed-rev', content: 'def eligibility_validator(): pass', query_matches: [
            { match_type: 'lexical', channels: ['lexical'], matched_values: [request.intent.canonical_query], source_ref: 'x', signal: {} },
          ] }],
        telemetry: { mode: 'lexical', duration_ms: 7, candidates_examined: 2, candidates_returned: 1, returned_chars: 20, stages: [] },
      }) }
    }
    return { ok: true, json: async () => ({ path: 'eligibility.py', line_start: 1, line_end: 1, total_lines: 1, content: 'def eligibility_validator(): pass', truncated: false }) }
  }))
})

describe('Search Lab', () => {
  it('sends edited SearchIntent and inspects a live result', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.change(screen.getByLabelText('Canonical query'), { target: { value: 'eligibility_validator' } })
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    expect(screen.getAllByText(/eligibility_validator\(\)/).length).toBeGreaterThan(0)
    const fetch = vi.mocked(globalThis.fetch)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/search', expect.anything()))
    const body = JSON.parse(String((fetch.mock.calls.find(call => call[0] === '/api/search')?.[1] as RequestInit).body))
    expect(body.intent.canonical_query).toBe('eligibility_validator')
    expect(screen.getByText('QueryMatchEvidence')).toBeInTheDocument()
  })

  it('rejects broken JSON on the client instead of silently broadening a query', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.click(screen.getByRole('button', { name: 'JSON completo' }))
    fireEvent.change(screen.getByLabelText('SearchIntent JSON'), { target: { value: '{ invalid' } })
    fireEvent.click(screen.getByTestId('run-search'))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(call => call[0] === '/api/search')).toHaveLength(0)
  })
})

describe('Query builder and authorized target selectors', () => {
  it('retains advanced JSON semantics when editing through the form', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.click(screen.getByRole('button', { name: 'JSON completo' }))
    const intent = {
      schema_version: 2, canonical_query: 'find source',
      anchors: [], hints: [], expansions: [], signals: [],
      expression: { type: 'all_of', children: [
        { type: 'term', value: 'source' }, { type: 'anchor', kind: 'path', value: 'pkg/source.py', origin: 'caller' },
      ] },
    }
    fireEvent.change(screen.getByLabelText('SearchIntent JSON'), { target: { value: JSON.stringify(intent) } })
    fireEvent.click(screen.getByRole('button', { name: 'Formulário' }))
    expect(screen.getByLabelText('Editor visual de expression')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Termos exatos'), { target: { value: 'source_ref' } })
    fireEvent.click(screen.getByRole('button', { name: 'JSON completo' }))
    const jsonText = (screen.getByLabelText('SearchIntent JSON') as HTMLTextAreaElement).value
    const mapped = JSON.parse(jsonText)
    expect(mapped.exact_terms).toEqual(['source_ref'])
    expect(mapped.expression).toEqual(intent.expression)
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    const fetch = vi.mocked(globalThis.fetch)
    const body = JSON.parse(String((fetch.mock.calls.find(call => call[0] === '/api/search')?.[1] as RequestInit).body))
    expect(body.intent.expression).toEqual(intent.expression)
  })

  it('generates typed required anchors and selects an operator-bound collection', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    await screen.findByRole('option', { name: /other-collection/ })
    await waitFor(() => expect(screen.getByLabelText('Coleção / índice configurado')).toBeEnabled())
    fireEvent.change(screen.getByLabelText('Coleção / índice configurado'), { target: { value: 'alternate' } })
    expect((screen.getByLabelText('Coleção / índice configurado') as HTMLSelectElement).value).toBe('alternate')
    fireEvent.click(screen.getByRole('button', { name: '+ Anchor' }))
    fireEvent.change(screen.getByLabelText('Valor do anchor 1'), { target: { value: 'eligibility.py' } })
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    const calls = vi.mocked(globalThis.fetch).mock.calls.filter(call => call[0] === '/api/search')
    const body = JSON.parse(String((calls[0][1] as RequestInit).body))
    expect(body.target_id).toBe('alternate')
    expect(body.repository).toBe('demo/catalog')
    expect(body.intent.anchors).toEqual([{ type: 'anchor', kind: 'path', value: 'eligibility.py', origin: 'caller' }])
    fireEvent.change(screen.getByLabelText('Repositório autorizado'), { target: { value: 'demo/other' } })
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    const newCalls = vi.mocked(globalThis.fetch).mock.calls.filter(call => call[0] === '/api/search')
    const nextBody = JSON.parse(String((newCalls[1][1] as RequestInit).body))
    expect(nextBody.repository).toBe('demo/other')
    expect(nextBody.target_id).toBe('other-repo')
  })

  it('blocks switching to form with malformed JSON while preserving the draft', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.click(screen.getByRole('button', { name: 'JSON completo' }))
    fireEvent.change(screen.getByLabelText('SearchIntent JSON'), { target: { value: '{ broken' } })
    fireEvent.click(screen.getByRole('button', { name: 'Formulário' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Corrija o JSON')
    expect((screen.getByLabelText('SearchIntent JSON') as HTMLTextAreaElement).value).toBe('{ broken')
  })
})

describe('On-demand operational readiness', () => {
  it('does not probe until explicitly requested and never conflates healthy service with ready index', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    expect(vi.mocked(globalThis.fetch).mock.calls.some(call => String(call[0]).includes('readiness'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Inspecionar índice' }))
    expect(await screen.findByText('Not ready')).toBeInTheDocument()
    expect(screen.getAllByText(/snapshot_missing/).length).toBeGreaterThan(0)
    expect(vi.mocked(globalThis.fetch).mock.calls.some(call => call[0] === '/api/readiness?target_id=default')).toBe(true)
  })
})

describe('Advanced SearchIntent v2 builder', () => {
  it('edits nested AND/OR branches without flattening their JSON or losing origin', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.click(screen.getByRole('button', { name: '+ AND · todas as condições' }))
    fireEvent.change(screen.getByLabelText('Termo branch 0'), { target: { value: 'eligibility_validator' } })
    fireEvent.click(screen.getByRole('button', { name: '+ OR · qualquer alternativa' }))
    fireEvent.change(screen.getByLabelText('Termo branch 1.0'), { target: { value: 'eligible' } })
    fireEvent.click(screen.getAllByRole('button', { name: '+ Anchor obrigatório' })[0])
    fireEvent.change(screen.getByLabelText('Anchor branch 1.1'), { target: { value: 'src/auth.py' } })
    fireEvent.change(screen.getByLabelText('Origem branch 1.1'), { target: { value: 'caller:explicit' } })
    fireEvent.click(screen.getByRole('button', { name: 'Inspecionar payload final' }))
    const prepared = JSON.parse(screen.getByLabelText('JSON final da busca').textContent ?? '{}')
    expect(prepared.intent.expression).toEqual({ type: 'all_of', children: [
      { type: 'term', value: 'eligibility_validator' },
      { type: 'any_of', children: [
        { type: 'term', value: 'eligible' },
        { type: 'anchor', kind: 'path', value: 'src/auth.py', origin: 'caller:explicit' },
      ] },
    ] })
    fireEvent.click(screen.getByRole('button', { name: 'JSON completo' }))
    const full = JSON.parse((screen.getByLabelText('SearchIntent JSON') as HTMLTextAreaElement).value)
    expect(full.expression).toEqual(prepared.intent.expression)
    fireEvent.click(screen.getByRole('button', { name: 'Formulário' }))
    expect(screen.getByLabelText('Anchor branch 1.1')).toHaveValue('src/auth.py')
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    const sent = JSON.parse(String((vi.mocked(globalThis.fetch).mock.calls.find(call => call[0] === '/api/search')?.[1] as RequestInit).body))
    expect(sent.intent.expression).toEqual(full.expression)
  })

  it('requires explicit preset action and refuses OR broadening on merge', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.change(screen.getByLabelText('Preset'), { target: { value: 'behavior' } })
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar preset' }))
    expect(screen.getByLabelText('Editor visual de expression')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /Combinar listas/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar preset' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Ambas as queries possuem expression')
    fireEvent.click(screen.getByRole('button', { name: 'JSON completo' }))
    const json = JSON.parse((screen.getByLabelText('SearchIntent JSON') as HTMLTextAreaElement).value)
    expect(json.expression.type).toBe('all_of')
    expect(json.expression.children).toHaveLength(2)
  })

  it('requires explicit import confirmation and blocks incompatible schema without overwriting edits', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.click(screen.getByRole('button', { name: 'Importar JSON' }))
    fireEvent.change(screen.getByLabelText(/Cole JSON completo para importar/), {
      target: { value: '{"schema_version":8,"canonical_query":"bad"}' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar importação' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('schema_version')
    expect(screen.getByLabelText('Canonical query')).toHaveValue('where is authentication validated?')
    const imported = {
      schema_version: 2, canonical_query: 'imported',
      anchors: [], expression: { type: 'any_of', children: [
        { type: 'term', value: 'authorization' },
        { type: 'anchor', kind: 'source_ref', value: 'repository://demo/catalog/src/auth.py', origin: 'caller' },
      ] }, expansions: [{ value: 'authz', origin: 'manual' }],
      signals: [], hints: [],
    }
    fireEvent.change(screen.getByLabelText(/Cole JSON completo para importar/), {
      target: { value: JSON.stringify(imported) },
    })
    expect(screen.getByLabelText('Canonical query')).toHaveValue('where is authentication validated?')
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar importação' }))
    expect(screen.getByLabelText('Canonical query')).toHaveValue('imported')
    fireEvent.click(screen.getByRole('button', { name: 'JSON completo' }))
    expect(JSON.parse((screen.getByLabelText('SearchIntent JSON') as HTMLTextAreaElement).value)).toEqual(imported)
  })

  it('blocks submission of blank expression nodes without silent fallback', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.click(screen.getByRole('button', { name: '+ OR · qualquer alternativa' }))
    fireEvent.click(screen.getByTestId('run-search'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Term')
    expect(vi.mocked(globalThis.fetch).mock.calls.some(call => call[0] === '/api/search')).toBe(false)
  })

  it('exports a valid complete intent with no extra backend endpoints', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:search-intent')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.click(screen.getByRole('button', { name: 'Exportar JSON' }))
    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob)
    expect(click).toHaveBeenCalledOnce()
    click.mockRestore()
  })
})

describe('Bounded query run history', () => {
  it('records a real request, restores the draft and exposes the replay preflight guard', async () => {
    mount()
    await screen.findByLabelText('Repositório autorizado')
    fireEvent.change(screen.getByLabelText('Canonical query'), { target: { value: 'eligibility_validator' } })
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    expect(await screen.findByText('1/20')).toBeInTheDocument()
    const history = screen.getByLabelText('Histórico de execuções')
    expect(history).toHaveTextContent('eligibility_validator')
    fireEvent.change(screen.getByLabelText('Canonical query'), { target: { value: 'different draft' } })
    fireEvent.click(screen.getByRole('button', { name: 'Restaurar query' }))
    expect(screen.getByLabelText('Canonical query')).toHaveValue('eligibility_validator')
    fireEvent.click(screen.getByRole('button', { name: 'Replay verificado' }))
    expect(await screen.findByRole('status')).toHaveTextContent('não há garantia de replay equivalente')
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(call => call[0] === '/api/search')).toHaveLength(1)
    expect(vi.mocked(globalThis.fetch).mock.calls.some(call => call[0] === '/api/readiness?target_id=default')).toBe(true)
  })
  it('imports safely without executing and requires confirmation to clear history', async () => {
    mount()
    await screen.findByText('API conectada')
    await waitFor(() => expect(screen.getByTestId('run-search')).toBeEnabled())
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    expect(await screen.findByText('1/20')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Limpar' }))
    expect(screen.getByText(/Apagar as execuções/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    expect(screen.getByText('1/20')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Limpar' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar limpeza' }))
    expect(screen.getByText('0/20')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Importar histórico' }))
    fireEvent.change(screen.getByLabelText(/Bundle JSON v1/), { target: { value: '{"schema_version":9,"runs":[]}' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar importação' }))
    expect(await screen.findByRole('status')).toHaveTextContent('incompatível')
    expect(screen.getByText('0/20')).toBeInTheDocument()
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(call => call[0] === '/api/search')).toHaveLength(1)
  })
})

describe('Retrieval Comparison Lab', () => {
  it('compares two captured executions without re-querying and exposes incompatibility, observations and measured cost', async () => {
    mount()
    await screen.findByText('API conectada')
    expect(screen.getByLabelText('Retrieval Comparison Lab')).toHaveTextContent('pelo menos duas queries')
    fireEvent.change(screen.getByLabelText('Canonical query'), { target: { value: 'eligibility_validator' } })
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    await screen.findByText('1/20')
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('2/20')
    const panel = screen.getByLabelText('Retrieval Comparison Lab')
    expect(panel).toHaveTextContent('incompatível como experimento controlado')
    expect(panel).toHaveTextContent('Corpus/repository revision não comprovadamente idênticos')
    expect(panel).toHaveTextContent('Tempo total')
    expect(panel).toHaveTextContent('7 ms')
    expect(panel).toHaveTextContent('não medido')
    expect(screen.getByLabelText('Diferenças dos candidatos')).toBeInTheDocument()
    const calls = vi.mocked(globalThis.fetch).mock.calls.filter(call => call[0] === '/api/search')
    expect(calls).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Somente B' }))
    expect(panel).toHaveTextContent('Nenhuma identidade nesta seleção.')
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(call => call[0] === '/api/search')).toHaveLength(2)
  })
  it('shows A vs C only after an explicit third run is selected', async () => {
    mount()
    await screen.findByText('API conectada')
    for (let i = 0; i < 3; i++) {
      fireEvent.click(screen.getByTestId('run-search'))
      await screen.findByText(String(i + 1) + '/20')
    }
    const panel = screen.getByLabelText('Retrieval Comparison Lab')
    expect(panel).not.toHaveTextContent('Terceiro braço:')
    const c = screen.getByLabelText('C · opcional')
    const value = (c as HTMLSelectElement).options[1]?.value
    expect(value).toBeTruthy()
    fireEvent.change(c, { target: { value } })
    expect(panel).toHaveTextContent('Terceiro braço: comparação A × C')
  })
})
