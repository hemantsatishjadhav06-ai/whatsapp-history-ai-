import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('memory correction and forgetting keep the source history separate', async ({ page }) => {
  await page.goto('/memory/demo-memory-maya');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('textbox', { name: 'Remembered context' }).fill('Maya prefers a short, thoughtful reply.');
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Maya prefers a short, thoughtful reply/ })).toBeVisible();
  await page.getByRole('button', { name: /Maya prefers a short, thoughtful reply/ }).click();
  await page.getByRole('button', { name: 'Forget memory', exact: true }).click();
  await expect(page.getByText('Maya prefers a short, thoughtful reply.', { exact: true })).toHaveCount(0);
  await page.goto('/inbox/chat_maya');
  await expect(page.getByRole('textbox', { name: 'Message Maya', exact: true })).toBeVisible();
  await expect(page.getByRole('log')).not.toBeEmpty();
});

test('owner reminder is created with an exact time and can be canceled', async ({ page }) => {
  await page.goto('/actions');
  await page.getByRole('button', { name: 'Schedule something' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', { name: 'What should we remind you about?' }).fill('Review the synthetic launch checklist');
  await dialog.getByRole('textbox', { name: 'Purpose', exact: true }).fill('Owner reminder, no external audience');
  const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0,16);
  await dialog.getByLabel('Local date & time').fill(tomorrow);
  await dialog.getByRole('textbox', { name: 'Timezone', exact: true }).fill('UTC');
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const row = page.getByRole('article').filter({ hasText: 'Review the synthetic launch checklist' });
  await expect(row).toBeVisible();
  await expect(row).toContainText('Owner reminder');
  await row.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(row).toContainText(/canceled/i);
});

test('uncertain receipt deep link never offers a blind resend', async ({ page }) => {
  await page.goto('/activity/demo-action-uncertain');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(/uncertain/i);
  await expect(dialog.getByRole('button', { name: /retry|resend/i })).toHaveCount(0);
  const audit = await new AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
  expect(audit.violations).toEqual([]);
});

test('recipient text survives switching chats and asks for exact review', async ({ page }) => {
  await page.goto('/inbox/chat_maya');
  await page.getByRole('textbox', { name: 'Message Maya', exact: true }).fill('This text belongs only to Maya.');
  const back = page.getByRole('button', { name: 'Back to inbox', exact: true });
  if (await back.isVisible()) await back.click();
  await page.getByRole('button', { name: /Ravi.*Auto/ }).click();
  await expect(page.getByRole('textbox', { name: 'Message Ravi', exact: true })).toHaveValue('');
  if (await back.isVisible()) await back.click();
  await page.getByRole('button', { name: /Maya.*Auto/ }).click();
  await expect(page.getByRole('textbox', { name: 'Message Maya', exact: true })).toHaveValue('This text belongs only to Maya.');
  await page.getByRole('button', { name: 'Prepare message', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Review this exact message' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve exact text', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /simulate acceptance|send approved/i })).toHaveCount(0);
});

test('Milo displays unavailable voice without starting the microphone', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Catch me up', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Talk', exact: true }).click();
  await expect(dialog).toContainText('no microphone has been activated');
  await expect(dialog).toContainText('Home context');
  await dialog.getByRole('button', { name: 'Close Milo' }).click();
  await expect(dialog).toHaveCount(0);
});
