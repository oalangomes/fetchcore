import { describe, expect, it } from 'vitest'
import {
  addExpressionChild, applyPreset, createExpression, intentPresets, moveExpressionChild,
  parseValidatedIntent, patchExpression, removeExpressionChild, validateVisualIntent,
} from './intentBuilder'
import type { Expression } from './intentBuilder'

const base = { schema_version: 2, canonical_query: 'existing', anchors: [], expansions: [], signals: [], exact_terms: [] }

describe('SearchIntent v2 validation', () => {
  it('retains a nested AND/OR tree, order, and caller origins without rewriting it', () => {
    const advanced = { ...base, expression: {
      type: 'all_of', children: [
        { type: 'term', value: 'validation' },
        { type: 'any_of', children: [
          { type: 'anchor', kind: 'path', value: 'src/source.py', origin: 'caller' },
          { type: 'anchor', kind: 'qualified_symbol', value: 'pkg.Engine', origin: 'user' },
        ] },
      ],
    } }
    expect(parseValidatedIntent(JSON.stringify(advanced))).toEqual(advanced)
    expect(validateVisualIntent(JSON.stringify(advanced))).toEqual(advanced)
  })
  it.each([
    { schema_version: 3, canonical_query: 'a' },
    { schema_version: 2, canonical_query: 'a', unknown: true },
    { schema_version: 2, expression: { type: 'any_of', children: [] } },
    { schema_version: 2, expression: { type: 'term', value: '' } },
    { schema_version: 2, expression: { type: 'anchor', kind: 'path', value: '' } },
    { schema_version: 2, anchors: [{ type: 'term', value: 'illegal' }] },
    { schema_version: 2, anchors: [{ type: 'anchor', kind: 'whatever', value: 'bad' }] },
    { schema_version: 2, expansions: [{ value: 'b', origin: '' }] },
    { schema_version: 2, signals: [{ type: 'semantic', classification: 'semantic_term', weight: Infinity }] },
  ])('rejects invalid schema or expressions without broadening: %j', value => {
    expect(() => parseValidatedIntent(JSON.stringify(value))).toThrow()
  })
  it('rejects over-depth and over-budget trees with backend-compatible limits', () => {
    let deeplyNested: Expression = { type: 'term', value: 'a' }
    for (let depth = 0; depth < 17; depth++) deeplyNested = { type: 'all_of', children: [deeplyNested] }
    expect(() => parseValidatedIntent(JSON.stringify({ ...base, expression: deeplyNested }))).toThrow(/profundidade/)
    expect(() => parseValidatedIntent(JSON.stringify({
      ...base, expression: { type: 'all_of', children: Array.from({ length: 256 },
        () => ({ type: 'term', value: 'a' })) },
    }))).toThrow(/256/)
    expect(() => parseValidatedIntent(JSON.stringify({
      ...base, anchors: Array.from({ length: 257 },
        () => ({ type: 'anchor', kind: 'path', value: 'a', origin: 'caller' })),
    }))).toThrow(/256/)
  })
  it('keeps valid legacy v1 for raw JSON but not for the visual v2 builder', () => {
    expect(parseValidatedIntent('{"exact_terms":["a"]}')).toEqual({ exact_terms: ['a'] })
    expect(() => validateVisualIntent('{"exact_terms":["a"]}')).toThrow(/v2/)
  })
})

describe('Branch editing', () => {
  it('changes explicitly between AND/OR, preserves origins and moves children deterministically', () => {
    let expr: Expression = { type: 'all_of', children: [
      { type: 'term', value: 'one' },
      { type: 'anchor', kind: 'path', value: 'file.py', origin: 'caller' },
    ] }
    expr = patchExpression(expr, [], current => current.type === 'all_of'
      ? { ...current, type: 'any_of' } : current)
    expr = moveExpressionChild(expr, [], 1, -1)
    expect(expr).toEqual({ type: 'any_of', children: [
      { type: 'anchor', kind: 'path', value: 'file.py', origin: 'caller' },
      { type: 'term', value: 'one' },
    ] })
    expr = addExpressionChild(expr, [], 'term')
    expr = patchExpression(expr, [2], current => ({ ...current, value: 'three' } as Expression))
    expect((expr as { children: Expression[] }).children[2]).toEqual({ type: 'term', value: 'three' })
    expr = removeExpressionChild(expr, [], 1)
    expect((expr as { children: Expression[] }).children).toHaveLength(2)
  })
  it('refuses implicit deletion of last branch', () => {
    expect(() => removeExpressionChild(createExpression('all_of'), [], 0)).toThrow(/único child/)
  })
})

describe('Versioned presets', () => {
  it('provides five valid starters with explicit provenance where required', () => {
    expect(intentPresets.map(item => item.id)).toEqual(['identifier', 'symbol', 'path', 'architecture', 'behavior'])
    for (const item of intentPresets) {
      expect(item.version).toBe(1)
      expect(parseValidatedIntent(JSON.stringify(item.intent))).toEqual(item.intent)
    }
  })
  it('merge retains the original canonical query/metadata and never invents OR or AND', () => {
    const old = { ...base, hints: ['caller-hint'], anchors: [
      { type: 'anchor', kind: 'path', value: 'one.py', origin: 'caller' },
    ], expression: null }
    const merged = applyPreset(old, intentPresets[0], 'merge')
    expect(merged.canonical_query).toBe('existing')
    expect(merged.hints).toEqual(['caller-hint'])
    expect(merged.anchors).toEqual([
      old.anchors[0], { type: 'anchor', kind: 'identifier', value: 'my_identifier', origin: 'preset:identifier@1' },
    ])
    expect(merged.expression).toBeNull()
    expect(() => applyPreset({ ...base, surprise: true }, intentPresets[0], 'merge')).toThrow(/não suportado/)
  })
  it('blocks combining independent expression trees and leaves the caller draft intact', () => {
    const before = { ...base, expression: { type: 'term', value: 'source' } }
    const serialized = JSON.stringify(before)
    expect(() => applyPreset(before, intentPresets[4], 'merge')).toThrow(/expression/)
    expect(JSON.stringify(before)).toBe(serialized)
    expect(applyPreset(before, intentPresets[4], 'replace')).toEqual(intentPresets[4].intent)
  })
})
