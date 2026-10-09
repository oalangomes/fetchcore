import { test, expect } from '@playwright/test'

test('real Solr search → provenance → authorized source read (FetchCore)', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Conectado · local')).toBeVisible()
  await expect(page.getByText('solr', { exact: true }).first()).toBeVisible()
  await page.getByLabel('Canonical query').fill('eligibility_validator')
  await page.getByTestId('run-search').click()

  await expect(page.getByText('Trecho recuperado')).toBeVisible()
  await expect(page.getByText('Repository binding autorizado')).toBeVisible()
  await expect(page.getByText(/eligibility.py/).first()).toBeVisible()
  await expect(page.getByText('QueryMatchEvidence')).toBeVisible()
  await expect(page.getByText('Provenance e identidade')).toBeVisible()

  await page.getByRole('tab', { name: 'Código-fonte' }).click()
  await expect(page.locator('pre.code-output').filter({ hasText: 'def eligibility_validator' })).toBeVisible()
  await page.getByRole('button', { name: 'Telemetry' }).click()
  await expect(page.getByText('Estágios de aquisição')).toBeVisible()
})
