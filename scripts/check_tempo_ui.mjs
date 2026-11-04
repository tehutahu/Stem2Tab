// Synthetic API fixtures, real built React UI + alphaTab renderer/player.
// All API requests are intercepted; no backend, inference or private audio is used.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.UI_ORIGIN || 'http://127.0.0.1:4315';
const out = process.env.UI_PROOF_OUT || '/tmp/stem2tab-tempo-ui-proof';
await mkdir(out, { recursive: true });
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const original = await readFile(new URL('../frontend/src/test/fixtures/musical-clock/regular.musicxml', import.meta.url), 'utf8');
const derivativeXml = await readFile(new URL('../frontend/src/test/fixtures/musical-clock/pickup.musicxml', import.meta.url), 'utf8');
const id = '12345678-1234-4123-8123-123456789012', job = '23456789-1234-4123-8123-123456789012';
const source = { kind: 'stored-selection', transcription_job_id: job, midi_trial_id: id, midi_sha256: 'a'.repeat(64), audio_sha256: 'b'.repeat(64), audio_bytes: 640044, duration_seconds: 20, source_origin_seconds: 12.25, sample_rate: 16000, channels: 1, format: 'WAV', correspondence: 'verified-completed-job' };
const summary = { trial_id: id, source_kind: 'muscriptor-small', status: 'completed', original_filename: 'synthetic.mid', input_bytes: 100, input_note_count: 4, source_duration_seconds: 8, last_note_end_seconds: 4.25, input_sha256: source.midi_sha256,
  storage_clock: { ppq: 960, tempo_bpm: 120, meter: '4/4', musical_tempo_and_meter_verified: false }, config: { strings: 4, tuning_midi: [28, 33, 38, 43], max_fret: 24 }, gp5: { requested: false, status: 'not_requested', mapped_count: 0, unmappable: [] }, warnings: [], error: null,
  artifacts: [{ key: 'original-midi', kind: 'midi', filename: 'synthetic.mid', bytes: 100, sha256: source.midi_sha256, url: `/api/v1/midi-trials/${id}/files/original-midi` }, { key: 'storage-clock-xml', kind: 'xml', filename: 'synthetic.musicxml', bytes: original.length, sha256: sha(original), url: `/api/v1/midi-trials/${id}/files/storage-clock-xml` }] };
const candidate = (bpm, relation, family_id = 'pulse-1') => ({ bpm, relation, family_id, periodicity: ['primary', 'alternative'].includes(relation) ? .72 : null, supporting_windows: ['primary', 'alternative'].includes(relation) ? 2 : 0, adoptable: bpm <= 300 });
function report(audioSource = source) {
  return { schema_version: '1.0', source: audioSource, status: 'candidates', reason_codes: ['multiple_pulse_hypotheses'], method: { name: 'spectral-flux-autocorrelation-v1', analysis_sample_rate: 8000, hop_seconds: .01, bpm_min: 40, bpm_max: 240, quality_calibration: 'uncalibrated' }, analyzed_intervals: [{ start_seconds: 0, end_seconds: 20 }], coverage: 'full',
    candidates: [candidate(120, 'primary'), candidate(60, 'half'), candidate(240, 'double'), candidate(100, 'alternative', 'pulse-2'), candidate(50, 'half', 'pulse-2'), candidate(200, 'double', 'pulse-2')],
    windows: [0, 10].map(start => ({ start_seconds: start, end_seconds: start + 10, status: 'periodic', bpm: 120, periodicity: .72, onset_count: 20 })), warnings: ['Synthetic signal only. Candidate lists are not exhaustive.'] };
}
function syntheticWave() {
  const sampleRate = 16000, count = 20 * sampleRate, bytes = Buffer.alloc(44 + count * 2);
  bytes.write('RIFF'); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write('WAVEfmt ', 8); bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22); bytes.writeUInt32LE(sampleRate, 24); bytes.writeUInt32LE(sampleRate * 2, 28); bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34); bytes.write('data', 36); bytes.writeUInt32LE(count * 2, 40);
  for (let sample = 0; sample < count; sample++) { const phase = ((sample / sampleRate - .173) % .5 + .5) % .5; const value = phase < .035 ? Math.sin(2 * Math.PI * 180 * phase) * Math.exp(-phase * 100) * .7 : 0; bytes.writeInt16LE(Math.round(value * 32767), 44 + sample * 2); }
  return bytes;
}
// Inspect the actual SDK through React's existing refs. This never changes the SDK.
function scoreSnapshot() {
  const host = document.getElementById('root'), key = Object.keys(host).find(name => name.startsWith('__reactContainer'));
  const stack = [host[key].stateNode.current]; let api;
  while (stack.length) { const fiber = stack.pop(); if (fiber.child) stack.push(fiber.child); if (fiber.sibling) stack.push(fiber.sibling); let hook = fiber.memoizedState;
    while (hook && typeof hook === 'object') { const ref = hook.memoizedState?.current; if (ref?.renderer && ref?.tickCache && ref?.score) api = ref; hook = hook.next; } }
  if (!api) throw Error('Rendered alphaTab API unavailable');
  return { tempo: api.score.tempo, speed: api.playbackSpeed, time: api.timePosition,
    notes: api.score.tracks.flatMap(track => track.staves.flatMap(staff => staff.bars.flatMap(bar => bar.voices.flatMap(voice => voice.beats.flatMap(beat => beat.notes.filter(note => !note.isTieDestination).map(note => [note.realValue, beat.absolutePlaybackStart, beat.absolutePlaybackStart + beat.playbackDuration])))))),
    position: document.querySelector('.midi-trial-controls input[type=range]')?.value,
    spacing: [...document.querySelectorAll('select')].find(select => select.closest('label')?.textContent.startsWith('横配置'))?.value };
}
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--autoplay-policy=no-user-gesture-required'] });
const results = [];
async function ready(page) { await page.waitForFunction(() => document.querySelector('.midi-trial-controls button')?.disabled === false); }
async function verify(page) { await page.getByLabel('推定する音声').selectOption('stored'); await page.getByRole('button', { name: '保存音声の対応を確認', exact: true }).click(); await page.getByText(/対応確認済み/).waitFor(); }
const estimateButton = page => page.getByRole('button', { name: '選択した音声からBPM候補を推定', exact: true });
async function run(name, width = 390) {
  const context = await browser.newContext({ viewport: { width, height: 1000 } }), page = await context.newPage();
  const calls = [], errors = []; let attempts = 0;
  page.on('pageerror', error => errors.push(error.message));
  const upload = syntheticWave();
  await context.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    calls.push({ method: request.method(), path });
    if (path === `/api/v1/midi-trials/${id}`) return route.fulfill({ json: summary });
    if (path.endsWith('/files/storage-clock-xml')) return route.fulfill({ contentType: 'application/xml', body: original });
    if (path === '/api/v1/tempo-estimates/sources') return route.fulfill({ json: source });
    if (path === '/api/v1/tempo-estimates/stored') {
      assert.deepEqual(request.postDataJSON(), source); attempts++;
      if (name === 'recovery' && attempts === 1) return route.abort('failed');
      if (name === 'wrong-source') return route.fulfill({ json: { ...report(), source: { ...source, audio_sha256: 'f'.repeat(64) } } });
      if (name === 'unstable') return route.fulfill({ json: { ...report(), status: 'unstable', candidates: [], reason_codes: ['window_disagreement'] } });
      if (name === 'insufficient') return route.fulfill({ json: { ...report(), status: 'insufficient_evidence', candidates: [], reason_codes: ['weak_periodicity'] } });
      return route.fulfill({ json: report() });
    }
    if (path === '/api/v1/tempo-estimates/upload') return route.fulfill({ json: report({ kind: 'uploaded-audio', audio_sha256: sha(upload), audio_bytes: upload.length, duration_seconds: 20, sample_rate: 16000, channels: 1, format: 'WAV', correspondence: 'unverified-upload' }) });
    if (path === `/api/v1/musical-clock/${id}/derive`) {
      const body = request.postDataJSON();
      assert.equal(body.input_sha256, summary.input_sha256);
      assert.deepEqual(body.clock, { bpm: 120, numerator: 3, denominator: 4, origin_seconds: .25, evidence: 'synthetic manual baseline', provenance: 'explicit-user-input' });
      return route.fulfill({ json: { musicxml: derivativeXml, musicxml_sha256: sha(derivativeXml), report: { clock: body.clock, source_sha256: summary.input_sha256, storage_bpm: 120, storage_meter: '4/4', estimation_status: 'not-estimated', confidence: null, ambiguity_bpm: [60, 240], note_count: 4, divisions: 5000, horizon_seconds: '8', quantization: 'none', preview_events: [[28, .125, .5], [33, .5, 1.25], [38, 2, 4.25], [40, .25, 2.75]].map(([pitch, start, end]) => [pitch, start * 1920, end * 1920]), preview_horizon_ticks: 15360, preview_eligible: true, preview_reason: null, scope: 'Synthetic exact source times' } } });
    }
    throw Error(`Unexpected API request: ${request.method()} ${path}`);
  });
  const url = `${origin}/midi-trial/saved/${id}?view=tab&spacing=time&tempo_source_job=${job}`;
  await page.goto(url); await ready(page);
  assert.equal(calls.filter(call => call.path.includes('tempo-estimates')).length, 0);
  await page.getByLabel('音楽的拍子', { exact: true }).fill('3/4');
  await page.getByLabel('拍原点（小節頭・元MIDI開始から秒）', { exact: true }).fill('0.25');
  if (name === 'manual-preview') {
    await page.getByLabel('音楽的BPM（四分音符）', { exact: true }).fill('120');
    await page.getByLabel('設定の根拠メモ（任意）', { exact: true }).fill('synthetic manual baseline');
    await page.getByRole('button', { name: '入力した音楽的クロックで別版を作成', exact: true }).click();
    await page.getByRole('link', { name: '別版MusicXMLを保存', exact: true }).waitFor();
    await page.getByRole('button', { name: /この別版を通常譜刻で表示/ }).click(); await ready(page);
    await page.getByRole('button', { name: '再生', exact: true }).click(); await page.waitForTimeout(220);
    await page.getByRole('button', { name: '一時停止', exact: true }).click(); await page.waitForTimeout(150);
    await page.getByRole('slider').fill('2'); await page.waitForTimeout(100);
  }
  const before = await page.evaluate(scoreSnapshot);
  const derivativeHref = name === 'manual-preview' ? await page.getByRole('link', { name: '別版MusicXMLを保存', exact: true }).getAttribute('href') : null;
  if (name === 'upload') {
    await page.getByLabel('推定する音声').selectOption('upload');
    await page.getByLabel('推定用音声 WAV/FLAC', { exact: true }).setInputFiles({ name: 'synthetic.wav', mimeType: 'audio/wav', buffer: upload });
  } else await verify(page);
  await estimateButton(page).click();
  if (name === 'wrong-source') {
    await page.getByRole('alert').filter({ hasText: /対応情報が不正/ }).waitFor(); assert.equal(await page.getByRole('button', { name: /BPMを入力候補に選ぶ/ }).count(), 0);
  } else if (name === 'unstable' || name === 'insufficient') {
    await page.getByText(name === 'unstable' ? '区間間で不安定・一定BPMは選べません' : '根拠不足・BPMは選べません', { exact: true }).waitFor(); assert.equal(await page.getByRole('button', { name: /BPMを入力候補に選ぶ/ }).count(), 0);
  } else {
    if (name === 'recovery') { await page.getByRole('alert').filter({ hasText: /BPM候補を確認できません/ }).waitFor(); await verify(page); await estimateButton(page).click(); }
    await page.getByText('BPM候補あり・未反映', { exact: true }).waitFor();
    assert.deepEqual(await page.evaluate(scoreSnapshot), before);
    const proposal = page.getByRole('button', { name: /^60 BPMを入力候補に選ぶ/ }); await proposal.click();
    if (name === 'manual-preview') {
      assert.equal(await page.getByLabel('音楽的BPM（四分音符）', { exact: true }).inputValue(), '120');
      assert.equal(await page.getByRole('region', { name: 'BPM候補への置き換え確認' }).evaluate(e => document.activeElement === e), true);
      await page.getByRole('button', { name: '手入力を保持する', exact: true }).click();
      await proposal.click(); await page.getByLabel('音楽的BPM（四分音符）', { exact: true }).fill('131');
      assert.equal(await page.getByRole('region', { name: 'BPM候補への置き換え確認' }).count(), 0);
      await proposal.click(); await page.getByRole('button', { name: '入力済みBPM・根拠メモを60 BPMの候補で置き換える', exact: true }).click();
      assert.equal(await page.getByRole('link', { name: '別版MusicXMLを保存', exact: true }).getAttribute('href'), derivativeHref);
      assert.equal(calls.filter(call => call.path.endsWith('/derive')).length, 1);
    }
    assert.equal(await page.getByLabel('音楽的BPM（四分音符）', { exact: true }).inputValue(), '60');
    assert.equal(await page.getByLabel('音楽的拍子', { exact: true }).inputValue(), '3/4');
    assert.equal(await page.getByLabel('拍原点（小節頭・元MIDI開始から秒）', { exact: true }).inputValue(), '0.25');
    assert.ok((await page.getByLabel('設定の根拠メモ（任意）', { exact: true }).inputValue()).includes(name === 'upload' ? sha(upload) : source.audio_sha256));
    assert.deepEqual(await page.evaluate(scoreSnapshot), before);
    const exported = await page.getByRole('link', { name: '音声BPM推定レポートを保存', exact: true }).getAttribute('href');
    assert.equal(JSON.parse(decodeURIComponent(exported.split(',').slice(1).join(','))).source.audio_sha256, name === 'upload' ? sha(upload) : source.audio_sha256);
  }
  const overflow = [];
  for (const width of [320, 390, 1280]) { await page.setViewportSize({ width, height: 1000 }); await page.waitForTimeout(150); const measure = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth })); if (measure.scroll > measure.width + 1) { await page.screenshot({path: `${out}/${name}-overflow.png`,fullPage:true}); console.log(await page.evaluate(() => [...document.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > innerWidth + 2).map(e => ({tag:e.tagName,cls:e.className,text:e.textContent.slice(0,100),width:e.getBoundingClientRect().width,scroll:e.scrollWidth})).slice(0,35))); } assert.ok(measure.scroll <= measure.width + 1, JSON.stringify(measure)); overflow.push(measure); }
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  if (name === 'manual-preview') {
    const previousCalls = calls.length;
    await page.evaluate(({ job, id }) => { history.pushState(null, '', `/midi-trial/saved/${id}?view=tab&spacing=time&tempo_source_job=${job}`); dispatchEvent(new PopStateEvent('popstate')); }, { job: '34567890-1234-4123-8123-123456789012', id });
    await page.waitForFunction(() => [...document.querySelectorAll('select')].find(select => select.closest('label')?.textContent.startsWith('推定する音声'))?.value === '');
    assert.equal(await page.getByLabel('音楽的BPM（四分音符）', { exact: true }).inputValue(), '60');
    assert.equal(await page.getByRole('link', { name: '別版MusicXMLを保存', exact: true }).getAttribute('href'), derivativeHref);
    assert.equal(calls.length, previousCalls);
  }
  const beforeReload = calls.filter(call => call.path.includes('tempo-estimates')).length;
  await page.reload(); await ready(page);
  assert.equal(calls.filter(call => call.path.includes('tempo-estimates')).length, beforeReload);
  assert.equal(await page.getByLabel('音楽的BPM（四分音符）', { exact: true }).inputValue(), '');
  assert.equal(await page.getByRole('button', { name: /BPMを入力候補に選ぶ/ }).count(), 0);
  assert.deepEqual(errors, []);
  results.push({ name, before, calls, overflow, errors }); await context.close();
}
try { for (const name of ['manual-preview', 'recovery', 'upload', 'unstable', 'insufficient', 'wrong-source']) await run(name); await writeFile(`${out}/results.json`, JSON.stringify(results, null, 2)); console.log(JSON.stringify(results, null, 2)); }
finally { await browser.close(); }
