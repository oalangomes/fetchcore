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
    expect(screen.getByText(/expressão booleana/)).toBeInTheDocument()
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
