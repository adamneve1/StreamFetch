// Dependency-free DOM logic checks, not a substitute for visual browser QA.
// Run with: node --test tests/test_frontend.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.className = ''; this.value = ''; this.checked = false; this.hidden = false;
    this.open = false; this.disabled = false; this.listeners = {}; this._text = '';
    this.classList = { toggle: (name, enabled) => {
      const classes = new Set(this.className.split(' ').filter(Boolean));
      if (enabled) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' ');
    } };
  }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get parentElement() { return this.parentNode; }
  get isConnected() { return !!this.root || !!this.parentNode?.isConnected; }
  append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parentNode = null;
    this.children = []; this.append(...nodes);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'value') this.value = ''; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(selector) {
    const [base, pseudo] = selector.split(':');
    const matches = node => (base.startsWith('.') ? node.className.split(' ').includes(base.slice(1)) : node.tagName === base)
      && (pseudo !== 'checked' || node.checked);
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function fixture() {
  const root = path.resolve(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'app/static/index.html'), 'utf8');
  const body = new Element('body'); body.root = true;
  const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  body.append(...ids.values());
  ids.get('empty').append(new Element('strong'), new Element('p'));
  ids.get('quality').value = 'best'; ids.get('compression').value = 'balanced';
  ids.get('media-format').value = 'mp4'; ids.get('storage-target').value = 'local';
  const context = vm.createContext({
    document: { body, getElementById: id => ids.get(id), createElement: tag => new Element(tag),
      createElementNS: (_, tag) => new Element(tag), addEventListener() {} },
    window: { addEventListener() {} }, URLSearchParams, Intl,
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    // Keep auto-login pending; each test drives the state itself.
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'app/static/app.js'), 'utf8'), context);
  return { context, get: id => ids.get(id), run: code => vm.runInContext(code, context) };
}

test('download progress shows the real track and attempt, and resets when unknown', () => {
  const f = fixture();
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'recording',progress_phase:'audio',progress_percent:42.5,download_attempt:2,download_attempts:3})`);
  assert.equal(f.get('download-progress').hidden, false);
  assert.equal(f.get('progress-label').textContent, 'Mengunduh audio · Percobaan 2/3');
  assert.equal(f.get('progress-value').textContent, '42.5%');
  assert.equal(f.get('progress-bar').value, 42.5);
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'recording',progress_percent:null})`);
  assert.equal(f.get('progress-value').textContent, '');
  assert.equal(f.get('progress-bar').value, '');
  f.run(`renderDownloadProgress({source:'youtube',is_live:false,state:'finalizing',progress_percent:100})`);
  assert.equal(f.get('progress-value').textContent, '');
  assert.equal(f.get('progress-label').textContent, 'Menggabungkan dan memproses');
  f.run(`renderDownloadProgress({source:'youtube',is_live:true,state:'recording'})`);
  assert.equal(f.get('download-progress').hidden, true);
});

test('status uses semantic colors and retains the entire diagnostic text', () => {
  const f = fixture();
  for (const [state, tone] of [['recording', 'working'], ['waiting', 'waiting'], ['ready', 'success'], ['failed', 'error']]) {
    f.context.job = { source: 'youtube', is_live: false, state, detail: 'YouTube belum siap.\nFile parsial tetap disimpan. '.repeat(10) };
    f.run('active=job; renderOperationalStatus()');
    assert.equal(f.get('status-monitor').dataset.tone, tone);
    assert.equal(f.get('status-detail').textContent, f.context.job.detail);
    assert.equal(f.get('duration-label').textContent, 'WAKTU PROSES');
  }
});

test('format controls group media settings and hide technical video specs for MP3', () => {
  const f = fixture();
  f.run(`setMode('youtube')`);
  assert.equal(f.get('format-field').hidden, false);
  assert.match(f.get('media-options').className, /has-format/);
  assert.equal(f.get('compression-details').hidden, false);
  assert.match(f.get('compression-spec').textContent, /CRF 23/);
  f.get('media-format').value = 'mp3'; f.run('updateFormatControls()');
  assert.equal(f.get('compression').disabled, true);
  assert.equal(f.get('quality').disabled, true);
  assert.equal(f.get('compression-details').hidden, true);
  f.run(`setMode('oryx')`);
  assert.equal(f.get('format-field').hidden, true);
  assert.equal(f.get('compression').disabled, false);
});

test('history keeps expanded errors and selection through refresh without interpreting HTML', () => {
  const f = fixture();
  f.context.rows = [{ job_id: 'error-job', source: 'youtube', state: 'failed', compression: 'compact',
    detail: '<script>alert(1)</script>\n' + 'Arsip live belum siap. '.repeat(15) }];
  f.run('historyRows=rows; renderHistory(rows)');
  const table = f.get('results');
  assert.equal(table.children[0].children.length, 7);
  assert.equal(table.querySelector('.status-badge').dataset.tone, 'error');
  assert.equal(table.querySelector('.preset-badge').textContent, 'Hemat');
  assert.equal(table.querySelector('.recording-detail-body').children[0].textContent, f.context.rows[0].detail);
  assert.equal(table.querySelector('script'), null);
  assert.equal(table.querySelector('.action-trigger'), null); // No empty menu for normal users.
  table.querySelector('.recording-details').open = true;
  table.querySelector('.history-select').checked = true;
  f.run('renderHistory(rows)');
  assert.equal(table.querySelector('.recording-details').open, true);
  assert.equal(table.querySelector('.history-select').checked, true);
  table.querySelector('.recording-details').open = false;
  f.run('renderHistory(rows)');
  assert.equal(table.querySelector('.recording-details').open, false);
});

test('legacy presets are not invented and MP3 retains its own label and download action', () => {
  const f = fixture();
  f.context.rows = [
    { job_id: 'legacy', source: 'oryx', state: 'ready', filename: 'old.mp4' },
    { job_id: 'audio', source: 'youtube', state: 'ready', filename: 'audio.mp3', compression: 'original' },
  ];
  f.run('historyRows=rows; renderHistory(rows)');
  assert.equal(f.get('results').children[0].querySelector('.preset-badge').textContent, 'Preset tidak tercatat');
  assert.equal(f.get('results').children[1].querySelector('.preset-badge').textContent, 'Audio MP3');
  assert.equal(f.get('results').querySelectorAll('.download-direct').length, 2);
  assert.equal(f.get('results').querySelectorAll('.transcript-generate').length, 1);
});

test('polling retains the terminal result even when history filters hide the completed job', async () => {
  const f = fixture();
  const job = { job_id: 'download', source: 'youtube', is_live: false, state: 'recording', compression: 'balanced' };
  let activeJob = job, row = job;
  f.context.fetch = async url => ({
    ok: true, status: 200, headers: { get: () => 'application/json' },
    json: async () => url === '/api/status'
      ? { active: activeJob, online: true, queued: 0, disk: { available: true, can_record: true, free: 100, total: 200 } }
      : { recordings: url.includes('state=recording') && row.state !== 'recording' ? [] : [row], total: 1 },
  });
  f.run(`csrf='test'; mode='youtube'`);
  await f.run('refresh()');
  assert.equal(f.get('status-title').textContent, 'Mengunduh');
  f.get('filter-state').value = 'recording';
  activeJob = null; row = { ...job, state: 'ready', filename: 'result.mp4', detail: 'File siap digunakan.' };
  await f.run('refresh()');
  assert.equal(f.get('status-monitor').dataset.tone, 'success');
  assert.equal(f.get('status-detail').textContent, 'File siap digunakan.');
  assert.equal(f.get('record').disabled, false);
  assert.equal(f.get('stop').disabled, true);
  assert.equal(f.get('download-progress').hidden, true);
  assert.equal(f.get('results').children.length, 0);
  assert.equal(f.run('historyRows.length'), 0);
});
