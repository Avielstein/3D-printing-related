// lulz UI controller.
//
// Design notes:
// - Held-key jog: keydown sets `held[key] = true`. Each jog only fires
//   after the previous one resolves (in-flight gate). When the response
//   returns, if the key is still held, we fire the next one. Natural
//   throttle from the server's serial round-trip.
// - Status polled at 1Hz; temps appended to a ring buffer for the graph.
// - Print status polled at 1Hz only while a print is running.

const $ = (id) => document.getElementById(id);

let stepMm = 1.0;
let inFlight = false;
const held = { LEFT: false, RIGHT: false, UP: false, DOWN: false,
               ENTER: false, SPACE: false };

const TEMP_RING_LEN = 300; // ~5 min at 1Hz
const tempRing = []; // {t: 23.1, b: 24.0}

// ---------- API ----------

async function api(path, opts = {}) {
  const r = await fetch(path, {
    method: opts.method || 'GET',
    headers: opts.body ? { 'Content-Type': 'application/json' } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); if (j.detail) msg = j.detail; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

// ---------- jog ----------

function keyToJog(key) {
  switch (key) {
    case 'LEFT':  return { axis: 'X', mm: -stepMm };
    case 'RIGHT': return { axis: 'X', mm:  stepMm };
    case 'UP':    return { axis: 'Y', mm:  stepMm };
    case 'DOWN':  return { axis: 'Y', mm: -stepMm };
    case 'ENTER': return { axis: 'Z', mm:  stepMm };
    case 'SPACE': return { axis: 'Z', mm: -stepMm };
  }
  return null;
}

async function fireJog(key) {
  if (inFlight) return;
  const j = keyToJog(key);
  if (!j) return;
  inFlight = true;
  try {
    await api('/api/jog', { method: 'POST', body: j });
  } catch (e) {
    console.warn('jog failed', e);
    setConn(false);
  } finally {
    inFlight = false;
  }
  // If the key is still held, fire the next one. This is the throttle:
  // we never have more than one /api/jog in flight at a time.
  if (held[key]) {
    fireJog(key);
  }
}

function pressJog(key) {
  if (held[key]) return; // already started loop
  held[key] = true;
  fireJog(key);
}

function releaseJog(key) {
  held[key] = false;
}

// Mouse-driven jog buttons emulate held keys.
function bindJogButtons() {
  document.querySelectorAll('button.jog[data-axis]').forEach((btn) => {
    const axis = btn.dataset.axis;
    const dir = parseInt(btn.dataset.dir, 10);
    const key = mapBtnToKey(axis, dir);
    const start = (e) => { e.preventDefault(); pressJog(key); btn.classList.add('held'); };
    const stop  = (e) => { e.preventDefault(); releaseJog(key); btn.classList.remove('held'); };
    btn.addEventListener('mousedown', start);
    btn.addEventListener('mouseup', stop);
    btn.addEventListener('mouseleave', stop);
    btn.addEventListener('touchstart', start, { passive: false });
    btn.addEventListener('touchend', stop);
  });
  document.querySelectorAll('button.jog[data-action]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (btn.dataset.action === 'home') {
        try { await api('/api/home', { method: 'POST' }); }
        catch (e) { alert('home failed: ' + e.message); }
      }
    });
  });
}

function mapBtnToKey(axis, dir) {
  if (axis === 'X' && dir > 0) return 'RIGHT';
  if (axis === 'X' && dir < 0) return 'LEFT';
  if (axis === 'Y' && dir > 0) return 'UP';
  if (axis === 'Y' && dir < 0) return 'DOWN';
  if (axis === 'Z' && dir > 0) return 'ENTER';
  if (axis === 'Z' && dir < 0) return 'SPACE';
  return null;
}

// ---------- keyboard ----------

function bindKeys() {
  const keyMap = {
    ArrowLeft: 'LEFT', ArrowRight: 'RIGHT',
    ArrowUp: 'UP', ArrowDown: 'DOWN',
    Enter: 'ENTER', ' ': 'SPACE',
  };
  document.addEventListener('keydown', (e) => {
    // Don't intercept while typing in inputs.
    if (e.target.matches('input, select, textarea')) return;
    // Don't jog during a print — same lock as the buttons.
    if (document.body.classList.contains('printing')) return;
    const k = keyMap[e.key];
    if (k) {
      e.preventDefault();
      pressJog(k);
      return;
    }
    if (e.key === '1') setStep(0.1);
    if (e.key === '2') setStep(1);
    if (e.key === '3') setStep(10);
  });
  document.addEventListener('keyup', (e) => {
    const k = keyMap[e.key];
    if (k) {
      e.preventDefault();
      releaseJog(k);
    }
  });
  // Safety: blur or visibility change releases any held keys (avoids the
  // "tab away while holding" foot-gun).
  window.addEventListener('blur', releaseAll);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) releaseAll();
  });
}

function releaseAll() {
  for (const k of Object.keys(held)) held[k] = false;
}

// ---------- step size ----------

function bindStepRadios() {
  document.querySelectorAll('input[name="step"]').forEach((r) => {
    r.addEventListener('change', () => setStep(parseFloat(r.value)));
  });
}

function setStep(mm) {
  stepMm = mm;
  document.querySelectorAll('input[name="step"]').forEach((r) => {
    r.checked = parseFloat(r.value) === mm;
  });
  $('step-val').textContent = mm + ' mm';
}

// ---------- status polling ----------

function setConn(ok) {
  const dot = $('conn-dot');
  dot.classList.toggle('ok', ok);
  dot.classList.toggle('err', !ok);
}

async function pollStatus() {
  try {
    const s = await api('/api/status');
    setConn(true);
    renderStatus(s);
    pushTempSample(s.temps);
    renderTempGraph();
    const printRunning = !!(s.print && s.print.running);
    renderPrint(s.print || {});
    setControlsLocked(printRunning);
  } catch (e) {
    setConn(false);
  }
}

// While a print is streaming, the printer is committed to the gcode
// timeline — any extra jog / heat change / paper-test / new print would
// either be ignored (lock-serialized after the current move) or actively
// ruin the print. Cancel + Panic stay enabled. Step radios + status
// polling stay enabled too (no-op operations).
const LOCKABLE_SELECTORS = [
  'button.jog',                          // all jog grid + Z buttons + home
  '#btn-heat', '#btn-cool', '#heat-temp',
  '#btn-pt-start', '#btn-pt-done', '#btn-pt-write', '#pt-use-g28',
  '#btn-print', '#btn-refresh-files', '#file-pick',
];

function setControlsLocked(locked) {
  for (const sel of LOCKABLE_SELECTORS) {
    document.querySelectorAll(sel).forEach((el) => {
      el.disabled = locked;
    });
  }
  document.body.classList.toggle('printing', locked);
}

function fmtNum(n) {
  if (n === null || n === undefined) return '--';
  return Number(n).toFixed(1);
}

function renderStatus(s) {
  $('t-actual').textContent = fmtNum(s.temps.T);
  $('b-actual').textContent = fmtNum(s.temps.B);
  // We don't have target temps in /api/status (parse_temps only returns
  // current). Leave target as last-set or "--".
  $('pos-x').textContent = fmtNum(s.position.X);
  $('pos-y').textContent = fmtNum(s.position.Y);
  $('pos-z').textContent = fmtNum(s.position.Z);
  $('z-min').textContent = s.z_min || '--';
}

// ---------- temp graph ----------

function pushTempSample(t) {
  tempRing.push({ t: t.T, b: t.B, time: Date.now() });
  while (tempRing.length > TEMP_RING_LEN) tempRing.shift();
}

function renderTempGraph() {
  if (tempRing.length < 2) return;
  const W = 300, H = 100;
  // Y-axis: 0..250 °C
  const yFor = (v) => (v === null || v === undefined) ? null : H - (v / 250) * H;
  // X-axis: stretch ring to width
  const xFor = (i) => (i / (TEMP_RING_LEN - 1)) * W;
  const off = TEMP_RING_LEN - tempRing.length;

  const tPts = [];
  const bPts = [];
  tempRing.forEach((p, i) => {
    const x = xFor(off + i);
    const yt = yFor(p.t);
    const yb = yFor(p.b);
    if (yt !== null) tPts.push(`${x.toFixed(1)},${yt.toFixed(1)}`);
    if (yb !== null) bPts.push(`${x.toFixed(1)},${yb.toFixed(1)}`);
  });
  $('t-line').setAttribute('points', tPts.join(' '));
  $('b-line').setAttribute('points', bPts.join(' '));
}

// ---------- heat ----------

function bindHeat() {
  $('btn-heat').addEventListener('click', async () => {
    const t = parseInt($('heat-temp').value, 10);
    if (!Number.isFinite(t)) return alert('bad temp');
    try { await api('/api/heat', { method: 'POST', body: { temp: t } }); }
    catch (e) { alert('heat failed: ' + e.message); }
  });
  $('btn-cool').addEventListener('click', async () => {
    try { await api('/api/cool', { method: 'POST' }); }
    catch (e) { alert('cool failed: ' + e.message); }
  });
}

// ---------- paper test ----------

function bindPaperTest() {
  const ptStart = $('btn-pt-start');
  const ptDone = $('btn-pt-done');
  const ptWrite = $('btn-pt-write');
  const ptStatus = $('pt-status');

  ptStart.addEventListener('click', async () => {
    ptStatus.textContent = 'setting up… (safety lift + reference + park)';
    try {
      const r = await api('/api/papertest/start', {
        method: 'POST',
        body: { use_g28: $('pt-use-g28').checked },
      });
      ptStatus.textContent =
        `armed — mode=${r.mode}, ref Z=${r.post_ref_z.toFixed(3)}. ` +
        `jog Z- (Space) until paper drags, then click Done.`;
      ptDone.disabled = false;
    } catch (e) {
      ptStatus.textContent = 'start failed: ' + e.message;
    }
  });

  ptDone.addEventListener('click', async () => {
    try {
      const r = await api('/api/papertest/done', { method: 'POST' });
      ptStatus.textContent =
        `recorded: Z_sweet=${r.z_sweet.toFixed(3)}, offset=${r.offset.toFixed(3)} ` +
        `→ ${r.gcode}`;
      ptDone.disabled = true;
      ptWrite.disabled = false;
    } catch (e) {
      ptStatus.textContent = 'done failed: ' + e.message;
    }
  });

  ptWrite.addEventListener('click', async () => {
    if (!confirm('patch lulzbot_mini_tpu.ini with the recorded offset?')) return;
    try {
      const r = await api('/api/papertest/write', { method: 'POST' });
      ptStatus.textContent =
        `patched: ${r.patched.split('/').pop()} → G92 Z${r.offset.toFixed(3)} ` +
        `(backup written)`;
      ptWrite.disabled = true;
    } catch (e) {
      ptStatus.textContent = 'patch failed: ' + e.message;
    }
  });
}

// ---------- print ----------

async function refreshGcodeFiles() {
  try {
    const r = await api('/api/gcode-files');
    const sel = $('file-pick');
    sel.innerHTML = '';
    if (r.files.length === 0) {
      sel.innerHTML = '<option value="">(no .gcode files found)</option>';
      return;
    }
    for (const f of r.files) {
      const opt = document.createElement('option');
      opt.value = f.path;
      opt.textContent = `${f.name}  (${(f.size / 1024).toFixed(0)} KB)`;
      sel.appendChild(opt);
    }
  } catch (e) {
    $('file-pick').innerHTML = `<option>(${e.message})</option>`;
  }
}

function bindPrint() {
  $('btn-refresh-files').addEventListener('click', refreshGcodeFiles);
  $('btn-print').addEventListener('click', async () => {
    const file = $('file-pick').value;
    if (!file) return alert('no file selected');
    if (!confirm(`start print: ${file}?`)) return;
    try {
      await api('/api/print/start', { method: 'POST', body: { file } });
    } catch (e) {
      alert('print failed: ' + e.message);
    }
  });
  $('btn-cancel').addEventListener('click', async () => {
    if (!confirm('cancel print?')) return;
    try { await api('/api/print/cancel', { method: 'POST' }); }
    catch (e) { alert('cancel failed: ' + e.message); }
  });
}

function renderPrint(p) {
  const fill = $('print-fill');
  const info = $('print-info');
  if (p.running) {
    fill.style.width = (p.percent || 0) + '%';
    info.textContent =
      `printing ${p.file ? p.file.split('/').pop() : ''}: ` +
      `${p.line}/${p.total} (${p.percent || 0}%) — ${Math.floor(p.elapsed || 0)}s`;
  } else if (p.error) {
    fill.style.width = '0';
    info.textContent = 'ERROR: ' + p.error;
  } else if (p.cancelled) {
    info.textContent = `cancelled at ${p.line}/${p.total}`;
  } else if (p.total && p.line === p.total) {
    fill.style.width = '100%';
    info.textContent = `done: ${p.total} lines in ${Math.floor(p.elapsed || 0)}s`;
  } else {
    fill.style.width = '0';
    info.textContent = 'idle';
  }
}

// ---------- panic ----------

function bindPanic() {
  $('btn-panic').addEventListener('click', async () => {
    if (!confirm('PANIC: quickstop + heaters off?')) return;
    try { await api('/api/panic', { method: 'POST' }); }
    catch (e) { alert('panic failed: ' + e.message); }
  });
}

// ---------- init ----------

function init() {
  bindStepRadios();
  setStep(1.0);
  bindJogButtons();
  bindKeys();
  bindHeat();
  bindPaperTest();
  bindPrint();
  bindPanic();
  refreshGcodeFiles();
  pollStatus();
  setInterval(pollStatus, 1000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
