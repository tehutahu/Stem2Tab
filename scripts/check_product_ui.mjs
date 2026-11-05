// Synthetic browser proof. All API traffic is intercepted; no backend or model is used.
// Use the installed Playwright module via PLAYWRIGHT_MODULE when it is not in node_modules.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.UI_ORIGIN || 'http://127.0.0.1:4319';
const out = process.env.UI_PROOF_OUT || '/tmp/stem2tab-ui-proof';
await mkdir(out, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});
const health = { model_assets: 'configured', reasons: [], max_audio_seconds: 600, max_upload_bytes: 536870912 };
const id = '12345678-1234-4123-8123-123456789012';
const running = { job_id: id, status: 'running', stage: 'transcribing', cancel_requested: false, completed_chunks: 1, total_chunks: 3, available_files: [] };
const complete = { ...running, status: 'completed', stage: 'completed', midi_trial_id: id, available_files: ['original.audio', 'performance.mid', 'model-notes.json'] };
const review = { ...running, status: 'needs_review', stage: 'output_needs_review', available_files: ['model-events.mid', 'model-notes.json'], review: { kind: 'zero_duration', raw_note_count: 3, zero_duration_count: 1, positive_note_count: 2, recorded_status: 'needs_review', notes_modified: false } };
const results = [];
async function contrast(locator) {
  return locator.evaluate(element => {
    const channels = color => color.match(/[\d.]+/g).slice(0, 3).map(Number);
    const luminance = color => channels(color).map(value => {
      const ratio = value / 255;
      return ratio <= 0.04045 ? ratio / 12.92 : ((ratio + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
    let parent = element;
    while (parent.parentElement && ['transparent', 'rgba(0, 0, 0, 0)'].includes(getComputedStyle(parent).backgroundColor)) parent = parent.parentElement;
    const ink = luminance(getComputedStyle(element).color);
    const paper = luminance(getComputedStyle(parent).backgroundColor);
    return (Math.max(ink, paper) + 0.05) / (Math.min(ink, paper) + 0.05);
  });
}
async function scenario(name, width = 1280) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  const textContrasts = [];
  let posts = 0, reads = 0, recover = false;
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path.endsWith('/health')) return json(health);
    if (request.method() === 'POST' && path === '/api/v1/transcriptions') {
      posts++;
      if (name === 'rejected') return route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'invalid_selection', message: '指定区間が音源の範囲外です。' } }) });
      if (name === 'network') return route.abort('failed');
      return json(name === 'review' ? review : running);
    }
    if (request.method() === 'GET' && path === `/api/v1/transcriptions/${id}`) {
      reads++;
      return json(recover || name === 'success' ? complete : running);
    }
    throw new Error(`Unexpected synthetic request: ${request.method()} ${path}`);
  });
  // Stable fixture ID lets the proof assert same-ID recovery after reload.
  await page.addInitScript(fixtureId => { crypto.randomUUID = () => fixtureId; }, id);
  await page.goto(origin);
  await page.getByText(/配置済み（GPU/).waitFor();
  for (const selector of ['.workflow-intro', '.primary-nav a.active', '.service-card > p', '.source-picker > p', '.precision-note > p']) {
    const ratio = await contrast(page.locator(selector).first());
    assert.ok(ratio >= 4.5, `${selector} contrast ${ratio} is below 4.5`);
    textContrasts.push({ selector, ratio });
  }
  assert.equal(await page.locator('[aria-current="step"]').textContent(), '01音源と範囲を選ぶ');
  assert.equal(await page.locator('details.advanced-nav').evaluate(e => e.open), false);
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement?.textContent), '本文へ移動');
  const focusStyle = await page.getByRole('link', { name: '本文へ移動' }).evaluate(e => getComputedStyle(e).outlineWidth);
  assert.equal(focusStyle, '3px');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'main-content');
  const summary = page.getByText('実験・従来方式', { exact: true });
  await summary.focus(); await page.keyboard.press('Enter');
  assert.equal(await page.locator('details.advanced-nav').evaluate(e => e.open), true);
  await page.keyboard.press('Tab');
  assert.match(await page.evaluate(() => document.activeElement?.textContent), /MuScriptorで採譜/);
  await summary.focus(); await page.keyboard.press('Enter');
  const pageWidth = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  assert.ok(pageWidth.scroll <= pageWidth.client + 1, `Overflow at ${width}: ${JSON.stringify(pageWidth)}`);
  await page.screenshot({ path: `${out}/${name}-${width}-initial.png`, fullPage: true });
  if (name === 'initial') {
    await summary.focus(); await page.keyboard.press('Enter');
    await page.getByRole('link', { name: 'Basic Pitch（従来方式・別実行）' }).click();
    assert.equal(new URL(page.url()).pathname, '/basic-pitch');
    await page.getByRole('heading', { name: 'Basic Pitchで採譜する' }).waitFor();
    assert.equal(await page.locator('details.advanced-nav').evaluate(e => e.open), true);
    await page.getByRole('link', { name: '音源から採譜（MuScriptor）', exact: true }).click();
    await page.getByRole('heading', { name: '音源からベースTABを作る' }).waitFor();
  } else {
    await page.getByLabel('音源 WAV/FLAC/M4A/Opus', { exact: true }).setInputFiles({ name: 'synthetic.wav', mimeType: 'audio/wav', buffer: Buffer.from('synthetic fixture only') });
    await page.getByLabel(/使用権のある音源/).check();
    const submit = page.getByRole('button', { name: /分離して採譜/ });
    const primaryContrast = await contrast(submit);
    assert.ok(primaryContrast >= 4.5);
    textContrasts.push({ selector: 'enabled primary button', ratio: primaryContrast });
    await submit.dblclick();
    if (name === 'rejected') {
      await page.getByRole('alert').filter({ hasText: '指定区間が音源の範囲外' }).waitFor();
      assert.equal(await page.getByLabel('音源 WAV/FLAC/M4A/Opus', { exact: true }).isEnabled(), true);
      assert.equal(await page.evaluate(() => sessionStorage.getItem('stem2tab.song.request')), null);
    } else if (name === 'network') {
      await page.getByRole('alert').filter({ hasText: '実行結果は未確認' }).waitFor();
      await page.getByRole('heading', { name: '応答は未確認です' }).waitFor();
      assert.equal(await page.getByLabel('音源 WAV/FLAC/M4A/Opus', { exact: true }).isDisabled(), true);
      await page.screenshot({ path: `${out}/${name}-${width}-unknown.png`, fullPage: true });
      await page.getByRole('button', { name: '同じ処理IDを再確認' }).click();
      await page.getByText('採譜区間 1 / 3', { exact: true }).waitFor();
      recover = true;
      await page.reload();
      await page.getByRole('heading', { name: '採譜結果を保存しました' }).waitFor();
      assert.ok(reads >= 2);
    } else if (name === 'review') {
      await page.getByRole('heading', { name: '譜面化の前に確認してください', exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: /ゼロ長1件を除いた別版/ }).isEnabled(), true);
      assert.equal(await page.getByRole('button', { name: /新しい試行として再実行/ }).count(), 0);
      assert.equal(reads, 0);
    } else {
      await page.getByRole('heading', { name: '採譜結果を保存しました' }).waitFor();
      assert.equal(await page.getByRole('link', { name: '3. 試聴・五線譜・TAB・保存へ' }).getAttribute('href'), `/midi-trial/saved/${id}?tempo_source_job=${id}`);
      const diagnostics = page.getByText('音符JSON・推論記録を保存', { exact: true });
      await diagnostics.focus(); await page.keyboard.press('Enter');
      assert.equal(await page.getByRole('link', { name: '元の全音符JSONを保存' }).isVisible(), true);
    }
    assert.equal(posts, 1, `${name} must submit only once`);
    assert.equal(await page.getByText(/長曲の実モデル動作・採譜精度は未検証/).isVisible(), true);
    await page.screenshot({ path: `${out}/${name}-${width}-result.png`, fullPage: true });
  }
  assert.deepEqual(errors, []);
  const resultWidth = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  assert.ok(resultWidth.scroll <= resultWidth.client + 1, `Result overflow at ${width}`);
  results.push({ name, width, posts, reads, pageWidth, resultWidth, keyboardFocusOutline: focusStyle, textContrasts, errors });
  await context.close();
}
try {
  for (const width of [1280, 390, 320]) await scenario('initial', width);
  await scenario('success');
  await scenario('review', 390);
  await scenario('rejected', 320);
  await scenario('network', 390);
  await writeFile(`${out}/results.json`, JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
} finally { await browser.close(); }
