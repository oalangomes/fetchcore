import { useState } from 'react'
import { parseIntent } from './api'
import { ExpressionEditor } from './ExpressionEditor'
import type { Expression } from './intentBuilder'

/** A form is only a view over SearchIntent v2; JSON remains the lossless source. */
type AnchorKind = 'identifier' | 'qualified_symbol' | 'path' | 'route' | 'source_ref'
interface AnchorRow { type: 'anchor'; kind: AnchorKind; value: string; origin: string }
interface ExpansionRow { value: string; origin: string }
interface SignalRow {
  value: string; type: string; classification: string
  priority: number; weight: number; origin: string; source: string; section: string
  signals: string[]; protected: boolean; required: boolean
}
const kinds: AnchorKind[] = ['identifier', 'qualified_symbol', 'path', 'route', 'source_ref']
const classifications = [
  'direct_reference', 'exact_identifier', 'path', 'architecture_concept',
  'functional_objective', 'behavioral_constraint', 'completion_instruction',
  'domain_concept', 'semantic_phrase', 'semantic_term', 'repository_identity',
  'scope_hint', 'product_identity', 'generic_instruction',
]
function lines(value: unknown): string {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string').join('\n') : ''
}
function list(value: string): string[] {
  return value.split('\n').map(row => row.trim()).filter(Boolean)
}
function anchors(value: unknown): AnchorRow[] {
  if (!Array.isArray(value)) return []
  return value.filter((a): a is AnchorRow =>
    typeof a === 'object' && a !== null && a.type === 'anchor' && typeof a.value === 'string'
    && typeof a.kind === 'string' && kinds.includes(a.kind as AnchorKind),
  )
}
function expansions(value: unknown): ExpansionRow[] {
  if (!Array.isArray(value)) return []
  return value.filter((e): e is ExpansionRow => typeof e === 'object' && e !== null && typeof e.value === 'string')
}
function signals(value: unknown): SignalRow[] {
  if (!Array.isArray(value)) return []
  return value.filter((s): s is SignalRow => typeof s === 'object' && s !== null && typeof s.value === 'string')
}
const newSignal = (): SignalRow => ({
  value: '', type: 'semantic', classification: 'semantic_term', priority: 0, weight: 1,
  origin: 'caller', source: 'explorer', section: 'query', signals: [], protected: false, required: false,
})

export function SearchIntentForm({ intentText, onChange }: {
  intentText: string; onChange: (value: string) => void
}) {
  const intent = parseIntent(intentText)
  // Keep raw draft whitespace so pressing Enter doesn't erase separators.
  const [termDraft, setTermDraft] = useState(() => ({
    exact_terms: lines(intent.exact_terms),
    semantic_terms: lines(intent.semantic_terms),
    hints: lines(intent.hints),
  }))
  function update(field: string, value: unknown) {
    onChange(JSON.stringify({ ...intent, [field]: value }, null, 2))
  }
  const anchorRows = anchors(intent.anchors)
  const expansionRows = expansions(intent.expansions)
  const signalRows = signals(intent.signals)
  function patchAnchor(index: number, patch: Partial<AnchorRow>) {
    update('anchors', anchorRows.map((row, i) => i === index ? { ...row, ...patch } : row))
  }
  function patchExpansion(index: number, patch: Partial<ExpansionRow>) {
    update('expansions', expansionRows.map((row, i) => i === index ? { ...row, ...patch } : row))
  }
  function patchSignal(index: number, patch: Partial<SignalRow>) {
    update('signals', signalRows.map((row, i) => i === index ? { ...row, ...patch } : row))
  }
  return <div className="space-y-5" aria-label="Construtor visual de SearchIntent">
    <p className="text-xs leading-5 text-muted">Refine a intenção somente quando necessário. Estes campos descrevem a busca: nenhum sinal, score ou anchor prova sozinho que uma evidência atende à necessidade do consumidor. Ao editar, o JSON v2 original e as expressões estruturadas são preservados.</p>
    <div className="grid gap-4 sm:grid-cols-2">
      {(['exact_terms', 'semantic_terms', 'hints'] as const).map(field =>
        <div key={field}>
          <label htmlFor={field} className="field-label">{{
            exact_terms: 'Termos exatos', semantic_terms: 'Termos semânticos', hints: 'Hints (opcionais)',
          }[field]}</label>
          <textarea id={field} className="field-input min-h-[76px] resize-y" value={termDraft[field]}
            onChange={event => {
              const value = event.target.value
              setTermDraft(current => ({ ...current, [field]: value }))
              update(field, list(value))
            }}
            placeholder="Um termo por linha" rows={3}/>
          <p className="mt-1 text-xs text-muted">{{ exact_terms: 'Termos literais declarados; não prometem correspondência exata em todo backend.', semantic_terms: 'Conceitos declarados; não ligam dense ou embeddings automaticamente.', hints: 'Pistas opcionais para descoberta; não são restrições obrigatórias.' }[field]}</p>
        </div>,
      )}
    </div>

    <div className="space-y-3 rounded-xl border border-line bg-background/20 p-3">
      <div className="flex items-center justify-between gap-3">
        <div><strong className="text-xs text-white">Required anchors</strong>
          <p className="mt-1 text-xs text-muted">Referências explícitas (arquivo, símbolo, identifier, rota ou source ref). São exigências declaradas, não prova automática de satisfação nem filtro garantido em todo backend.</p></div>
        <button type="button" className="switch-button border border-line" onClick={() => update('anchors', [
          ...anchorRows, { type: 'anchor', kind: 'path', value: '', origin: 'caller' },
        ])}>+ Anchor</button>
      </div>
      {anchorRows.map((row, index) =>
        <div key={index} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto]">
          <select aria-label={'Tipo do anchor ' + (index + 1)} className="field-input" value={row.kind}
            onChange={event => patchAnchor(index, { kind: event.target.value as AnchorKind })}>
            {kinds.map(kind => <option key={kind} value={kind}>{kind}</option>)}
          </select>
          <input aria-label={'Valor do anchor ' + (index + 1)} className="field-input" value={row.value}
            placeholder="Caminho ou identificador" onChange={event => patchAnchor(index, { value: event.target.value })}/>
          <button type="button" className="switch-button border border-line" aria-label={'Remover anchor ' + (index + 1)}
            onClick={() => update('anchors', anchorRows.filter((_, i) => index !== i))}>Remover</button>
        </div>,
      )}
    </div>

    <div className="space-y-3 rounded-xl border border-line bg-background/20 p-3">
      <div className="flex items-center justify-between gap-3">
        <div><strong className="text-xs text-white">Expansions</strong><p className="mt-1 text-xs text-muted">Termos adicionais explícitos para ampliar descoberta (recall). Não substituem a query canônica nem criam OR silenciosamente.</p></div>
        <button type="button" className="switch-button border border-line" onClick={() => update('expansions', [...expansionRows, { value: '', origin: 'caller' }])}>+ Expansion</button>
      </div>
      {expansionRows.map((row, index) =>
        <div key={index} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
          <input aria-label={'Valor da expansion ' + (index + 1)} className="field-input" value={row.value}
            onChange={event => patchExpansion(index, { value: event.target.value })} placeholder="Termo de expansão"/>
          <button type="button" className="switch-button border border-line" aria-label={'Remover expansion ' + (index + 1)}
            onClick={() => update('expansions', expansionRows.filter((_, i) => index !== i))}>Remover</button>
        </div>,
      )}
    </div>

    <div className="space-y-3 rounded-xl border border-line bg-background/20 p-3">
      <div className="flex items-center justify-between gap-3">
        <div><strong className="text-xs text-white">Query signals</strong><p className="mt-1 text-xs text-muted">Sinais tipados para informar intenção, classificação e provenance. A opção required não comprova correspondência ou satisfação.</p></div>
        <button type="button" className="switch-button border border-line" onClick={() => update('signals', [...signalRows, newSignal()])}>+ Signal</button>
      </div>
      {signalRows.map((row, index) =>
        <div key={index} className="space-y-2 rounded-lg border border-line/80 p-2">
          <div className="grid gap-2 sm:grid-cols-2">
            <input aria-label={'Valor do signal ' + (index + 1)} value={row.value} className="field-input"
              onChange={event => patchSignal(index, { value: event.target.value })} placeholder="Signal value"/>
            <select aria-label={'Tipo do signal ' + (index + 1)} value={row.type} className="field-input"
              onChange={event => patchSignal(index, { type: event.target.value })}>
              {['exact', 'semantic', 'reference', 'hint'].map(item => <option key={item}>{item}</option>)}
            </select>
            <select aria-label={'Classificação do signal ' + (index + 1)} value={row.classification} className="field-input"
              onChange={event => patchSignal(index, { classification: event.target.value })}>
              {classifications.map(item => <option key={item}>{item}</option>)}
            </select>
            <label className="flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={Boolean(row.required)}
                onChange={event => patchSignal(index, { required: event.target.checked })}/> Required
            </label>
          </div>
          <button type="button" className="switch-button border border-line" onClick={() => update('signals', signalRows.filter((_, i) => index !== i))}>Remover signal {index + 1}</button>
        </div>,
      )}
    </div>
    <p className="text-xs leading-5 text-muted">AND exige todas as condições declaradas na expressão; OR representa alternativas. Veja o payload final antes de executar. O resultado só atesta matches reportados, não satisfação semântica.</p>
    <ExpressionEditor
      expression={(intent.expression ?? null) as Expression | null}
      occupiedNodes={anchorRows.length + expansionRows.length}
      onChange={value => update('expression', value)}
    />
  </div>
}
