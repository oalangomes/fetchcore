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
    await screen.findByText('demo/catalog')
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
    await screen.findByText('demo/catalog')
    fireEvent.change(screen.getByLabelText('SearchIntent JSON'), { target: { value: '{ invalid' } })
    fireEvent.click(screen.getByTestId('run-search'))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(call => call[0] === '/api/search')).toHaveLength(0)
  })
})

describe('Source revision handling', () => {
  it('does not pass an unbound indexed revision as an authorized source precondition', async () => {
    mount()
    await screen.findByText('demo/catalog')
    fireEvent.click(screen.getByTestId('run-search'))
    await screen.findByText('Trecho recuperado')
    fireEvent.click(screen.getByRole('tab', { name: 'Código-fonte' }))
    await waitFor(() => {
      const sourceCall = vi.mocked(globalThis.fetch).mock.calls.find(call => call[0] === '/api/source')
      expect(sourceCall).toBeDefined()
      const payload = JSON.parse(String((sourceCall?.[1] as RequestInit).body))
      expect(payload.revision).toBeUndefined()
      expect(payload.repository).toBe('demo/catalog')
    })
    expect(screen.getByText(/não prova equivalência ao trecho indexado/)).toBeInTheDocument()
  })
})
