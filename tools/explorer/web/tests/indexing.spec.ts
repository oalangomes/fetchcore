import { test, expect } from '@playwright/test'
import path from 'node:path'
const fixtures = path.resolve('../fixtures/index-runs')

test('offline timeline imports CLI evidence, compares, exports and fails closed', async ({ page }) => {
  const writes: string[] = []
  page.on('request', request => {
    if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname)
  })
  await page.goto('/')
  const timeline = page.getByRole('region', { name: 'Indexing Timeline', exact: true })
  const input = timeline.getByLabel('Importar artefatos de indexação')
  await input.setInputFiles([path.join(fixtures, 'noop.json'), path.join(fixtures, 'incremental.json')])
  await expect(timeline.getByText('Comparação compatível', { exact: true })).toBeVisible()
  await expect(timeline.getByText('Revisão de origem mudou: true')).toBeVisible()
  await expect(timeline.getByText('desconhecido', { exact: true }).first()).toBeVisible()
  const download = page.waitForEvent('download')
  await timeline.getByRole('button', { name: 'Exportar observação' }).first().click()
  expect((await download).suggestedFilename()).toBe('index-run.json')
  await input.setInputFiles(path.join(fixtures, 'failure.json'))
  await expect(timeline.getByText(/Persistência e rollback: desconhecidos/)).toBeVisible()
  await input.setInputFiles({ name: 'invalid.json', mimeType: 'application/json', buffer: Buffer.from('{"schema_version":99}') })
  await expect(timeline.getByRole('alert')).toContainText('Unsupported indexing artifact')
  await expect(timeline.getByRole('article')).toHaveCount(0)
  expect(writes.every(url => url === '/api/indexing/artifacts')).toBe(true)
})

test('initial plans remain distinct from persisted rebuilds and incompatible destinations', async ({ page }) => {
  await page.goto('/')
  const timeline = page.getByRole('region', { name: 'Indexing Timeline', exact: true })
  const input = timeline.getByLabel('Importar artefatos de indexação')
  await input.setInputFiles([path.join(fixtures, 'initial-plan.json'), path.join(fixtures, 'rebuild.json')])
  await expect(timeline.getByText('Mutações planejadas (sem persistência)')).toBeVisible()
  await expect(timeline.getByText('Comparação bloqueada', { exact: true })).toBeVisible()
  await timeline.getByRole('button', { name: 'Limpar observações' }).click()
  await expect(timeline.getByRole('article')).toHaveCount(0)
})

test('different repositories block comparison and rebuild-required reasons stay explicit', async ({ page }) => {
  await page.goto('/')
  const timeline = page.getByRole('region', { name: 'Indexing Timeline', exact: true })
  const input = timeline.getByLabel('Importar artefatos de indexação')
  const { readFileSync } = await import('node:fs')
  const raw = readFileSync(path.join(fixtures, 'incremental.json'), 'utf8')
  const other = JSON.parse(raw)
  other.repository = 'other/repository'
  other.command.result.repository = 'other/repository'
  other.command.result.execution.snapshot.repository = 'other/repository'
  await input.setInputFiles([
    { name: 'before.json', mimeType: 'application/json', buffer: Buffer.from(raw) },
    { name: 'after.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(other)) },
  ])
  await expect(timeline.getByText('Comparação bloqueada', { exact: true })).toBeVisible()
  await expect(timeline.getByText('repository_changed', { exact: false })).toBeVisible()
  await input.setInputFiles(path.join(fixtures, 'incompatible-plan.json'))
  await expect(timeline.getByText('indexing_contract_changed', { exact: true })).toBeVisible()
  await expect(timeline.getByRole('heading', { name: /rebuild_required/ })).toBeVisible()
})
