import { useState } from 'react'
import {
  addExpressionChild, anchorKinds, createExpression, moveExpressionChild,
  patchExpression, removeExpressionChild,
} from './intentBuilder'
import type { Expression, ExpressionType } from './intentBuilder'

interface Props {
  expression: Expression | null
  occupiedNodes: number
  onChange: (next: Expression | null) => void
}

const types: { value: ExpressionType; title: string }[] = [
  { value: 'term', title: 'Termo literal' }, { value: 'anchor', title: 'Anchor obrigatório' },
  { value: 'all_of', title: 'AND · todas as condições' },
  { value: 'any_of', title: 'OR · qualquer alternativa' },
]

function nodes(node: Expression | null): number {
  if (!node) return 0
  return node.type === 'all_of' || node.type === 'any_of'
    ? 1 + node.children.reduce((sum, child) => sum + nodes(child), 0)
    : 1
}

export function ExpressionEditor({ expression, occupiedNodes, onChange }: Props) {
  const [message, setMessage] = useState('')
  const total = occupiedNodes + nodes(expression)
  function perform(operation: () => Expression | null) {
    try {
      const next = operation()
      if (occupiedNodes + nodes(next) > 256) throw new Error('Limite de 256 nós incluindo anchors e expansions')
      onChange(next)
      setMessage('')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Expressão inválida')
    }
  }
  function renderNode(node: Expression, path: number[], depth: number): React.ReactNode {
    const group = node.type === 'all_of' || node.type === 'any_of'
    const parentPath = path.slice(0, -1)
    const parent = path.length ? lookup(expression, parentPath) : null
    const siblings = parent?.type === 'all_of' || parent?.type === 'any_of' ? parent.children.length : 1
    const index = path[path.length - 1] ?? 0
    function change(fn: (old: Expression) => Expression) {
      perform(() => patchExpression(expression!, path, fn))
    }
    return <div key={path.join('.') || 'root'} className="rounded-lg border border-line bg-background/30 p-2.5" style={{ marginLeft: depth ? Math.min(depth, 5) * 6 : 0 }}>
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label={'Estrutura ' + (path.join('.') || 'root')} className="field-input w-auto max-w-full"
          value={node.type} onChange={event => {
            const next = event.target.value as ExpressionType
            if (next === node.type) return
            // AND↔OR preserves every child; leaf→group replacement is explicit.
            change(old => (
              (next === 'all_of' || next === 'any_of') && (old.type === 'all_of' || old.type === 'any_of')
                ? { ...old, type: next }
                : createExpression(next)
            ))
          }}>
          {types.map(item => <option key={item.value} value={item.value}>{item.title}</option>)}
        </select>
        <span className="text-[11px] text-muted">{path.length ? 'Branch ' + path.join('.') : 'Raiz'}</span>
        {path.length > 0 && <>
          <button type="button" className="switch-button border border-line"
            aria-label={'Subir branch ' + path.join('.')} disabled={index === 0}
            onClick={() => perform(() => moveExpressionChild(expression!, parentPath, index, -1))}>↑</button>
          <button type="button" className="switch-button border border-line"
            aria-label={'Descer branch ' + path.join('.')} disabled={index === siblings - 1}
            onClick={() => perform(() => moveExpressionChild(expression!, parentPath, index, 1))}>↓</button>
        </>}
        <button type="button" className="switch-button border border-line"
          aria-label={'Remover branch ' + (path.join('.') || 'root')}
          disabled={path.length > 0 && siblings < 2}
          title={path.length && siblings < 2 ? 'Remova o grupo explicitamente: o último child não pode ser descartado' : ''}
          onClick={() => perform(() => path.length
            ? removeExpressionChild(expression!, parentPath, index)
            : null)}>Remover</button>
      </div>
      {node.type === 'term' && <input className="field-input mt-2" aria-label={'Termo branch ' + (path.join('.') || 'root')}
        placeholder="Termo lexical literal obrigatório" value={node.value}
        onChange={event => change(old => ({ ...old, value: event.target.value } as Expression))}/>}
      {node.type === 'anchor' && <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <select className="field-input" aria-label={'Kind branch ' + (path.join('.') || 'root')}
          value={node.kind} onChange={event => change(old => ({ ...old, kind: event.target.value } as Expression))}>
          {anchorKinds.map(kind => <option key={kind} value={kind}>{kind}</option>)}
        </select>
        <input className="field-input" aria-label={'Anchor branch ' + (path.join('.') || 'root')}
          placeholder="Valor obrigatório" value={node.value}
          onChange={event => change(old => ({ ...old, value: event.target.value } as Expression))}/>
        <input className="field-input" aria-label={'Origem branch ' + (path.join('.') || 'root')}
          placeholder="Origem" value={node.origin ?? 'caller'}
          onChange={event => change(old => ({ ...old, origin: event.target.value } as Expression))}/>
      </div>}
      {group && <div className="mt-3 space-y-2">
        <p className="text-xs text-muted">{node.type === 'all_of'
          ? 'AND: todas as condições precisam aparecer em um mesmo registro retornado.'
          : 'OR: pelo menos uma alternativa declarada; nenhuma expansão implícita.'}</p>
        {node.children.map((child, i) => renderNode(child, [...path, i], depth + 1))}
        <div className="flex flex-wrap gap-1.5">
          {types.map(type =>
            <button key={type.value} type="button" className="switch-button border border-line"
              disabled={total >= 256 || depth >= (type.value === 'all_of' || type.value === 'any_of' ? 15 : 16)}
              onClick={() => perform(() => addExpressionChild(expression!, path, type.value))}>
              + {type.title}
            </button>)}
        </div>
      </div>}
    </div>
  }
  return <section aria-label="Editor visual de expression" className="space-y-3 rounded-xl border border-line bg-background/20 p-3">
    <div>
      <strong className="text-xs text-white">Boolean expression · branches explícitos</strong>
      <p className="mt-1 text-xs text-muted">
        Até 16 níveis e 256 nós (incluindo anchors/expansions). O modelo original é preservado no JSON.
        Termos vazios e grupos vazios bloqueiam a busca, nunca são ignorados silenciosamente.
      </p>
      <p className="mt-1 text-xs text-muted">Nós utilizados: {total}/256</p>
    </div>
    {expression ? renderNode(expression, [], 0) :
      <div className="space-y-2">
        <p className="text-xs text-muted">Sem expression (nenhum AND/OR declarado).</p>
        <div className="flex flex-wrap gap-1.5">
          {types.map(type => <button key={type.value} type="button" className="switch-button border border-line"
            disabled={total >= 256}
            onClick={() => perform(() => createExpression(type.value))}>+ {type.title}</button>)}
        </div>
      </div>}
    {message && <p className="text-xs text-rose-300" role="alert">{message}</p>}
  </section>
}

function lookup(root: Expression | null, path: number[]): Expression | null {
  let node = root
  for (const index of path) {
    if (node?.type !== 'all_of' && node?.type !== 'any_of') return null
    node = node.children[index] ?? null
  }
  return node
}
