/** Declarative SearchIntent v2 editing helpers. Never rewrite retrieval meaning implicitly. */
export type AnchorKind = 'identifier' | 'qualified_symbol' | 'path' | 'route' | 'source_ref'
export type ExpressionType = 'term' | 'anchor' | 'all_of' | 'any_of'
export type Expression =
  | { type: 'term'; value: string }
  | { type: 'anchor'; kind: AnchorKind; value: string; origin: string }
  | { type: 'all_of' | 'any_of'; children: Expression[] }
export type Intent = Record<string, unknown>
export interface IntentPreset { id: string; name: string; version: 1; detail: string; intent: Intent }

const rootFields = new Set([
  'schema_version', 'canonical_query', 'anchors', 'expression', 'expansions',
  'exact_terms', 'semantic_terms', 'hints', 'signals',
])
const signalFields = new Set([
  'value', 'type', 'classification', 'priority', 'weight', 'origin',
  'source', 'section', 'signals', 'derived_from', 'protected', 'required',
])
export const anchorKinds: AnchorKind[] =
  ['identifier', 'qualified_symbol', 'path', 'route', 'source_ref']
const classifications = new Set([
  'direct_reference', 'exact_identifier', 'path', 'architecture_concept',
  'functional_objective', 'behavioral_constraint', 'completion_instruction',
  'domain_concept', 'semantic_phrase', 'semantic_term', 'repository_identity',
  'scope_hint', 'product_identity', 'generic_instruction',
])
const MAX_NODES = 256
const MAX_DEPTH = 16
const MAX_JSON_CHARS = 100_000

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function keys(value: Record<string, unknown>, allowed: Set<string>, at: string): void {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new Error(at + ': campo não suportado: ' + key)
  }
}
function items(value: unknown, name: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(name + ': esperado array')
  return value
}
function strings(value: unknown, name: string): void {
  if (items(value, name).some(item => typeof item !== 'string')) {
    throw new Error(name + ': esperado array de strings')
  }
}
function nonblank(value: unknown, name: string): void {
  if (typeof value !== 'string' || !value.trim()) throw new Error(name + ': informe um valor')
}

export function validateExpression(value: unknown, depth = 0, counter = { nodes: 0 }): asserts value is Expression {
  if (depth > MAX_DEPTH) throw new Error('Expression excede profundidade máxima 16')
  if (++counter.nodes > MAX_NODES) throw new Error('Expression excede limite de 256 nós')
  if (!object(value)) throw new Error('Expression deve ser um objeto tipado')
  switch (value.type) {
    case 'term':
      keys(value, new Set(['type', 'value']), 'term')
      nonblank(value.value, 'Term')
      return
    case 'anchor':
      keys(value, new Set(['type', 'kind', 'value', 'origin']), 'anchor')
      if (!anchorKinds.includes(value.kind as AnchorKind)) throw new Error('Anchor kind inválido')
      nonblank(value.value, 'Anchor')
      nonblank(value.origin ?? 'caller', 'Anchor origin')
      return
    case 'all_of':
    case 'any_of': {
      keys(value, new Set(['type', 'children']), 'group')
      const children = items(value.children, 'children')
      if (!children.length) throw new Error('Grupo AND/OR não pode estar vazio')
      for (const child of children) validateExpression(child, depth + 1, counter)
      return
    }
    default: throw new Error('Expression type não suportado')
  }
}

export function validateIntent(intent: unknown): asserts intent is Intent {
  if (!object(intent)) throw new Error('SearchIntent deve ser um objeto JSON')
  keys(intent, rootFields, 'SearchIntent')
  const version = intent.schema_version ?? 1
  if (version !== 1 && version !== 2) throw new Error('SearchIntent schema_version não suportada')
  if (version === 1 && ['canonical_query', 'anchors', 'expression', 'expansions'].some(key => key in intent)) {
    throw new Error('Campos v2 exigem schema_version 2')
  }
  if ('canonical_query' in intent && typeof intent.canonical_query !== 'string') {
    throw new Error('canonical_query deve ser uma string')
  }
  for (const field of ['exact_terms', 'semantic_terms', 'hints']) {
    if (field in intent) strings(intent[field], field)
  }
  let nodeCount = 0
  if ('anchors' in intent) {
    for (const anchor of items(intent.anchors, 'anchors')) {
      validateExpression(anchor)
      if (!object(anchor) || anchor.type !== 'anchor') throw new Error('anchors aceita apenas anchor')
      nodeCount += 1
    }
  }
  if ('expansions' in intent) {
    for (const item of items(intent.expansions, 'expansions')) {
      if (!object(item)) throw new Error('Expansion deve ser objeto')
      keys(item, new Set(['value', 'origin']), 'expansion')
      nonblank(item.value, 'Expansion')
      nonblank(item.origin, 'Expansion origin')
      nodeCount += 1
    }
  }
  if (intent.expression != null) {
    const count = { nodes: nodeCount }
    validateExpression(intent.expression, 0, count)
  } else if (nodeCount > MAX_NODES) {
    throw new Error('SearchIntent excede limite de 256 nós')
  }
  if ('signals' in intent) for (const [index, item] of items(intent.signals, 'signals').entries()) {
    if (!object(item)) throw new Error('Signal ' + index + ' deve ser objeto')
    keys(item, signalFields, 'signal')
    if (!['exact', 'semantic', 'reference', 'hint'].includes(String(item.type))) throw new Error('Signal type inválido')
    if (!classifications.has(String(item.classification))) throw new Error('Signal classification inválida')
    for (const field of ['value', 'origin', 'source', 'section']) {
      if (item[field] !== undefined && typeof item[field] !== 'string') throw new Error('Signal ' + field + ' deve ser string')
    }
    if (item.priority !== undefined && (!Number.isInteger(item.priority))) throw new Error('Signal priority inválida')
    if (item.weight !== undefined && (typeof item.weight !== 'number' || !Number.isFinite(item.weight))) {
      throw new Error('Signal weight inválida')
    }
    for (const field of ['protected', 'required']) {
      if (item[field] !== undefined && typeof item[field] !== 'boolean') throw new Error('Signal ' + field + ' inválido')
    }
    if (item.derived_from !== undefined && item.derived_from !== null && typeof item.derived_from !== 'string') {
      throw new Error('Signal derived_from inválido')
    }
    if ('signals' in item) strings(item.signals, 'Signal signals')
  }
}

export function parseValidatedIntent(text: string): Intent {
  if (text.length > MAX_JSON_CHARS) throw new Error('SearchIntent excede 100 KB')
  const value: unknown = JSON.parse(text)
  validateIntent(value)
  return value
}

export function validateVisualIntent(text: string): Intent {
  const intent = parseValidatedIntent(text)
  if (intent.schema_version !== 2) throw new Error('Formulário avançado requer SearchIntent v2')
  return intent
}

export function createExpression(type: ExpressionType): Expression {
  if (type === 'term') return { type, value: '' }
  if (type === 'anchor') return { type, kind: 'path', value: '', origin: 'caller' }
  return { type, children: [{ type: 'term', value: '' }] }
}

/** Paths address exact child indices; no implicit flattening/reparenting. */
export function patchExpression(node: Expression, path: number[], change: (value: Expression) => Expression): Expression {
  if (!path.length) return change(node)
  if (node.type !== 'all_of' && node.type !== 'any_of') throw new Error('Expression path inválido')
  const [index, ...rest] = path
  if (!Number.isInteger(index) || index < 0 || index >= node.children.length) throw new Error('Expression index inválido')
  return { ...node, children: node.children.map((item, i) =>
    i === index ? patchExpression(item, rest, change) : item,
  ) }
}
export function addExpressionChild(node: Expression, path: number[], type: ExpressionType): Expression {
  return patchExpression(node, path, current => {
    if (current.type !== 'all_of' && current.type !== 'any_of') throw new Error('Somente grupos aceitam children')
    return { ...current, children: [...current.children, createExpression(type)] }
  })
}
export function removeExpressionChild(node: Expression, parent: number[], index: number): Expression {
  return patchExpression(node, parent, group => {
    if (group.type !== 'all_of' && group.type !== 'any_of') throw new Error('Somente grupos têm children')
    if (group.children.length < 2) throw new Error('Remover único child apagaria semântica: remova grupo explicitamente')
    return { ...group, children: group.children.filter((_, i) => i !== index) }
  })
}
export function moveExpressionChild(node: Expression, parent: number[], index: number, by: -1 | 1): Expression {
  return patchExpression(node, parent, group => {
    if (group.type !== 'all_of' && group.type !== 'any_of') throw new Error('Somente grupos têm children')
    const next = index + by
    if (index < 0 || next < 0 || next >= group.children.length) return group
    const children = [...group.children]
    ;[children[index], children[next]] = [children[next], children[index]]
    return { ...group, children }
  })
}

export const intentPresets: IntentPreset[] = [
  { id: 'identifier', name: 'Identificador exato', version: 1, detail: 'Lookup estruturado de identifier', intent: {
    schema_version: 2, canonical_query: 'Localizar o identificador',
    anchors: [{ type: 'anchor', kind: 'identifier', value: 'my_identifier', origin: 'preset:identifier@1' }],
    expression: null, expansions: [], exact_terms: [], semantic_terms: [], hints: [], signals: [],
  } },
  { id: 'symbol', name: 'Símbolo qualificado', version: 1, detail: 'Lookup de símbolo', intent: {
    schema_version: 2, canonical_query: 'Localizar a definição do símbolo',
    anchors: [{ type: 'anchor', kind: 'qualified_symbol', value: 'module.ClassName', origin: 'preset:symbol@1' }],
    expression: null, expansions: [], exact_terms: [], semantic_terms: [], hints: [], signals: [],
  } },
  { id: 'path', name: 'Caminho do arquivo', version: 1, detail: 'Busca estruturada por path', intent: {
    schema_version: 2, canonical_query: 'Localizar arquivo',
    anchors: [{ type: 'anchor', kind: 'path', value: 'src/module.py', origin: 'preset:path@1' }],
    expression: null, expansions: [], exact_terms: [], semantic_terms: [], hints: [], signals: [],
  } },
  { id: 'architecture', name: 'Arquitetura', version: 1, detail: 'Termos conceituais sem inferir anchors', intent: {
    schema_version: 2, canonical_query: 'Onde está implementada esta arquitetura?',
    anchors: [], expression: null, expansions: [], exact_terms: [],
    semantic_terms: ['application boundary', 'adapter'], hints: [], signals: [],
  } },
  { id: 'behavior', name: 'Comportamento', version: 1, detail: 'Conjunção explícita em um mesmo candidato', intent: {
    schema_version: 2, canonical_query: 'Onde este comportamento é implementado?',
    anchors: [], expression: { type: 'all_of', children: [
      { type: 'term', value: 'validation' }, { type: 'term', value: 'repository' },
    ] }, expansions: [], exact_terms: [], semantic_terms: [], hints: [], signals: [],
  } },
]

/** Merge never invents an AND/OR between two expression trees. */
export function applyPreset(current: Intent, preset: IntentPreset, mode: 'replace' | 'merge'): Intent {
  validateIntent(current)
  validateIntent(preset.intent)
  if (mode === 'replace') return JSON.parse(JSON.stringify(preset.intent)) as Intent
  if (current.schema_version !== 2) throw new Error('Merge requer intenção v2')
  if (current.expression != null && preset.intent.expression != null) {
    throw new Error('Ambas as queries possuem expression: escolha substituir ou faça merge explícito no editor')
  }
  const append = (name: string): unknown[] => [
    ...(Array.isArray(current[name]) ? current[name] : []),
    ...(Array.isArray(preset.intent[name]) ? preset.intent[name] : []),
  ]
  return {
    ...current,
    canonical_query: (typeof current.canonical_query === 'string' && current.canonical_query.trim())
      ? current.canonical_query : preset.intent.canonical_query,
    exact_terms: append('exact_terms'), semantic_terms: append('semantic_terms'),
    hints: append('hints'), anchors: append('anchors'), expansions: append('expansions'),
    signals: append('signals'), expression: current.expression ?? preset.intent.expression ?? null,
  }
}
