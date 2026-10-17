// Well-Well web client. Plain DOM, no framework. Every piece of server data goes in
// through textContent; the only innerHTML is model markdown after DOMPurify.

const C = {
  bg: '#0E1217', surface: '#151B22', surface2: '#1C242E', border: '#27313D', text: '#E6EAF0',
  muted: '#8A96A6', faint: '#5B6675', accent: '#4C9EEB', danger: '#E5484D', warning: '#F2A93B',
  success: '#3DB57A', slate: '#64748B',
};

const TOOL_LABELS = {
  well_status: 'Checking well status',
  wells_not_producing: 'Finding wells that are not producing',
  temperature_history: 'Reading temperature history',
  spills: 'Finding spills',
  downtime: 'Counting downtime',
  aggregate: 'Adding up the numbers',
  repairs: 'Looking up repairs',
  rca_due: 'Checking root-cause rules',
  compliance: 'Checking rules',
  inspection_detail: 'Opening the inspection',
  wells: 'Listing wells',
  search_documents: 'Searching manuals and SOPs',
  query_sql: 'Querying the records',
  recall_my_conversations: 'Recalling your earlier chats',
  record_correction: 'Recording your correction',
  record_note: 'Saving a shared note',
  record_rule: 'Saving a team rule',
  record_claim: 'Recording your statement',
};

const ID_RE = /^[A-Z]{2,4}-\d{4}/;
const CITE_PART_RE = /^(INS|MNT|CTX|REG|COR|FB|CLM|RULE|RPT|NOTE|SOP|OM|T\d{3})\b[\w\s§.\-›:,]*$/;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const state = {
  boot: null, users: [], user: null, conversationId: null, busy: false, epoch: 0,
  askController: null, convController: null, memory: null, memoryState: 'idle', memoryError: '', memoryTab: 'shared',
};
const $ = (id) => document.getElementById(id);

// ---------- small helpers ----------

function el(tag, cls, ...kids) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}

function svg(path, cls = 'w-3.5 h-3.5') {
  const ns = 'http://www.w3.org/2000/svg';
  const s = document.createElementNS(ns, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('fill', 'none');
  s.setAttribute('stroke', 'currentColor');
  s.setAttribute('class', cls);
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', path);
  p.setAttribute('stroke-width', '2');
  p.setAttribute('stroke-linecap', 'round');
  p.setAttribute('stroke-linejoin', 'round');
  s.append(p);
  return s;
}

const ICONS = {
  check: 'M5 13l4 4L19 7',
  up: 'M7 10v10H4V10h3zm0 0l4-7c1.5 0 2.5 1 2.2 2.6L12.6 9H18a2 2 0 012 2.3l-1.2 7A2 2 0 0116.8 20H7',
  down: 'M17 14V4h3v10h-3zm0 0l-4 7c-1.5 0-2.5-1-2.2-2.6l.6-3.4H6a2 2 0 01-2-2.3l1.2-7A2 2 0 017.2 4H17',
  warn: 'M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  arrow: 'M14 5l7 7m0 0l-7 7m7-7H3',
  close: 'M6 6l12 12M18 6L6 18',
};

async function api(path, body = {}, signal = undefined) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const err = new Error((data && (data.error || data.detail)) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

const isAbort = (err) => err && err.name === 'AbortError';

// Plotly and Leaflet are big, so they load the first time a chart or map needs them.
const libs = {};
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const tag = document.createElement('script');
    tag.src = src;
    tag.onload = resolve;
    tag.onerror = () => { tag.remove(); reject(new Error(`could not load ${src}`)); };
    document.head.append(tag);
  });
}
function needLib(name) {
  if (!libs[name]) {
    const job = name === 'plotly'
      ? loadScript('https://cdn.plot.ly/plotly-2.35.2.min.js')
      : (() => {
        const css = document.createElement('link');
        css.rel = 'stylesheet';
        css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
        document.head.append(css);
        return loadScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js');
      })();
    libs[name] = job.catch((err) => { libs[name] = null; throw err; });
  }
  return libs[name];
}

// ---------- skeletons and small status blocks ----------

function skel(cls) {
  const s = el('div', `skel ${cls}`);
  s.setAttribute('aria-hidden', 'true');
  return s;
}

function busyBox(cls, label, ...kids) {
  const box = el('div', cls, kids);
  box.setAttribute('aria-busy', 'true');
  box.append(el('span', 'sr-only', label));
  return box;
}

function retryBlock(message, onRetry, cls = '') {
  const btn = el('button', 'px-2.5 py-1 rounded-chip border border-border text-[11px] text-accent hover:border-accent transition-colors', 'Retry');
  btn.type = 'button';
  btn.addEventListener('click', onRetry);
  const box = el('div', `flex flex-wrap items-center gap-2 text-[12px] text-muted ${cls}`, el('span', 'text-warning', svg(ICONS.warn)), el('span', '', message), btn);
  box.setAttribute('role', 'alert');
  return box;
}

function convSkeleton(n = 4) {
  return busyBox('space-y-0.5', 'Loading conversations', Array.from({ length: n }, () => el('div', 'px-2 py-1.5 space-y-1.5', skel('h-3 w-[85%]'), skel('h-2.5 w-12'))));
}

function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage blocked */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function humanize(key) {
  return String(key).replace(/_c$/, ' (°C)').replace(/_bbl$/, ' (bbl)').replace(/_usd$/, ' (USD)').replace(/_/g, ' ');
}
function sentence(text) {
  const s = humanize(text);
  return s.charAt(0).toUpperCase() + s.slice(1);
}
function initials(name) {
  return String(name || '?').split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();
}

function fmtDate(value, withTime = false) {
  if (!value) return '';
  const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
  if (!m) return String(value);
  const base = `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
  return withTime && m[4] ? `${base} ${m[4]}:${m[5]}` : base;
}
function shortDate(value) {
  const m = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : String(value || '');
}
function relDate(value) {
  const today = state.boot?.today;
  if (!value || !today) return shortDate(value);
  const days = Math.round((Date.parse(today) - Date.parse(String(value).slice(0, 10))) / 86400000);
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  return shortDate(value);
}
function fmtNum(v) {
  if (typeof v !== 'number') return String(v);
  return Number.isInteger(v) ? v.toLocaleString('en-US') : v.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

const AVATAR_HUES = ['#4C9EEB', '#3DB57A', '#F2A93B', '#B48EF0', '#E58A7A', '#4FB8B0'];
function hueFor(id) {
  let h = 0;
  for (const c of String(id || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return AVATAR_HUES[h % AVATAR_HUES.length];
}
function paintAvatar(node, user) {
  const hue = hueFor(user?.user_id);
  node.textContent = initials(user?.name);
  node.style.background = `${hue}26`;
  node.style.color = hue;
  return node;
}
function avatar(user, size = 'w-7 h-7 text-[10px]') {
  const a = el('span', `${size} rounded-full font-mono font-semibold flex items-center justify-center shrink-0`);
  a.setAttribute('aria-hidden', 'true');
  return paintAvatar(a, user);
}

const KIND_ICONS = {
  status_card: 'M4 5h16v14H4z M8 9h8 M8 13h5',
  table: 'M4 5h16v14H4z M4 10h16 M10 5v14',
  line: 'M4 19h16 M5 15l4-5 4 3 6-7',
  bar: 'M4 19h16 M7 16v-5 M12 16V7 M17 16v-8',
  map: 'M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2z M9 4v14 M15 6v14',
  heatmap: 'M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z',
  answer: 'M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z',
};

// Status words that carry meaning get a colour; anything else stays plain text.
function cellTone(value) {
  const v = String(value ?? '').trim().toLowerCase();
  if (!v || v.length > 28) return null;
  if (/^(not producing|breach|breached|overdue|open|down|failed|violation|retracted|rejected)$/.test(v)) return 'danger';
  if (/^(pending|out of date|unconfirmed|disputed|due|in progress)$/.test(v)) return 'warning';
  if (/^(producing|met|ok|closed|confirmed|applied|active|resolved|filed|not required)$/.test(v)) return 'success';
  return null;
}

function badge(text, tone) {
  const tones = {
    danger: 'bg-danger/10 border-danger/30 text-[#F0787B]',
    warning: 'bg-warning/15 border-warning/30 text-warning',
    success: 'bg-success/10 border-success/30 text-success',
    neutral: 'bg-surface-2 border-border text-muted',
  };
  return el('span', `inline-flex items-center gap-1.5 border font-mono text-[10px] px-1.5 py-0.5 rounded-chip whitespace-nowrap ${tones[tone] || tones.neutral}`, text);
}

// ---------- citation chips and the record viewer ----------

function chip(id) {
  const b = el('button', 'chip hover:border-accent transition-colors', `[${id}]`);
  b.type = 'button';
  b.title = 'Show this record';
  b.addEventListener('click', (e) => { e.stopPropagation(); openRecord(id); });
  return b;
}

function isCitation(part, known) {
  const p = part.trim();
  return p.length > 1 && p.length < 80 && (known.has(p) || CITE_PART_RE.test(p));
}

// Turns "[INS-0001-0203]" or "[INS-1, MNT-2]" inside rendered markdown into chips.
function chipify(root, citations) {
  const known = new Set(citations || []);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.nodeValue.includes('[')) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (node.parentElement?.closest('code, pre, a')) continue;
    const text = node.nodeValue;
    const re = /\[([^\[\]]{2,160})\]/g;
    let last = 0;
    let match;
    const frag = document.createDocumentFragment();
    let changed = false;
    while ((match = re.exec(text))) {
      const parts = match[1].split(/\s*[,;]\s*/);
      if (!parts.every((p) => isCitation(p, known))) continue;
      frag.append(text.slice(last, match.index));
      parts.forEach((p, i) => { if (i) frag.append(' '); frag.append(chip(p.trim())); });
      last = match.index + match[0].length;
      changed = true;
    }
    if (!changed) continue;
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

function renderMarkdown(text, citations) {
  const box = el('div', 'md');
  try {
    box.innerHTML = DOMPurify.sanitize(marked.parse(String(text || ''), { breaks: false, gfm: true }));
    box.querySelectorAll('table').forEach((t) => {
      const wrap = el('div', 'md-table scroll-thin');
      t.replaceWith(wrap);
      wrap.append(t);
    });
    box.querySelectorAll('a').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    chipify(box, citations);
  } catch {
    box.textContent = String(text || '');
  }
  return box;
}

let recordPanel = null;
function ensureRecordPanel() {
  if (recordPanel) return recordPanel;
  const scrim = el('div', 'hidden fixed inset-0 bg-black/50 z-[60]');
  const body = el('div', 'flex-1 overflow-y-auto scroll-thin p-5 text-[12px]');
  const title = el('div', 'font-mono text-[13px] font-semibold');
  const kind = el('div', 'text-[11px] text-muted');
  const close = el('button', 'w-7 h-7 rounded-card border border-border hover:bg-surface-2 flex items-center justify-center text-muted hover:text-text', svg(ICONS.close, 'w-4 h-4'));
  close.type = 'button';
  close.setAttribute('aria-label', 'Close');
  const panel = el('div', 'hidden fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[min(640px,calc(100vw-32px))] max-h-[80vh] bg-surface border border-border rounded-card z-[70] flex flex-col',
    el('div', 'px-5 py-3.5 border-b border-border flex items-center justify-between gap-3', el('div', 'min-w-0', title, kind), close), body);
  panel.id = 'record-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  const hide = () => { recordController?.abort(); scrim.classList.add('hidden'); panel.classList.add('hidden'); };
  close.addEventListener('click', hide);
  scrim.addEventListener('click', hide);
  document.body.append(scrim, panel);
  recordPanel = { scrim, panel, body, title, kind, hide };
  return recordPanel;
}

let recordController = null;
async function openRecord(id) {
  const p = ensureRecordPanel();
  recordController?.abort();
  const controller = new AbortController();
  recordController = controller;
  p.title.textContent = id;
  p.kind.textContent = 'Loading the record…';
  p.body.replaceChildren(busyBox('grid grid-cols-[minmax(110px,170px)_1fr] gap-x-4 gap-y-2.5', 'Loading the record',
    Array.from({ length: 6 }, (_, i) => [skel('h-3 w-24'), skel(`h-3 ${['w-40', 'w-56', 'w-32', 'w-64', 'w-28', 'w-48'][i]}`)])));
  p.scrim.classList.remove('hidden');
  p.panel.classList.remove('hidden');
  try {
    const rec = await api('/api/record', { record_id: id }, controller.signal);
    if (controller !== recordController) return;
    p.body.replaceChildren();
    p.kind.textContent = [rec.kind, rec.title].filter(Boolean).join(' · ');
    if (rec.fields) p.body.append(kvList(rec.fields));
    else if (rec.excerpt) {
      if (rec.path) p.body.append(el('div', 'font-mono text-[10px] text-faint mb-2', rec.path));
      p.body.append(renderMarkdown(rec.excerpt, []));
    } else p.body.append(kvList(rec));
  } catch (err) {
    if (isAbort(err) || controller !== recordController) return;
    if (err.status === 404) {
      p.kind.textContent = 'Record not found';
      p.body.replaceChildren(el('div', 'text-muted', 'There is no record with this ID in the data.'));
    } else {
      p.kind.textContent = "Couldn't load the record";
      p.body.replaceChildren(retryBlock(err.message, () => openRecord(id)));
    }
  }
}

// ---------- generic values, key/value lists and tables ----------

function valueNode(v, key = '') {
  if (v === null || v === undefined || v === '') return el('span', 'text-faint', '—');
  if (typeof v === 'boolean') return el('span', v ? 'text-text' : 'text-muted', v ? 'Yes' : 'No');
  if (typeof v === 'number') return el('span', 'font-mono', fmtNum(v));
  if (Array.isArray(v)) {
    if (!v.length) return el('span', 'text-faint', '—');
    if (v.every((x) => typeof x !== 'object' || x === null)) {
      if (key === 'citations' || v.every((x) => ID_RE.test(String(x)))) {
        return el('span', 'inline-flex flex-wrap gap-1', v.map((x) => chip(String(x))));
      }
      return el('span', '', v.join(', '));
    }
    const d = el('details', '');
    d.append(el('summary', 'text-accent text-[11px] font-mono', el('span', 'caret mr-1', '›'), `${v.length} item${v.length === 1 ? '' : 's'}`));
    d.append(el('div', 'mt-1.5', dataTable(v, { compact: true })));
    return d;
  }
  if (typeof v === 'object') {
    const parts = Object.entries(v).filter(([, x]) => x !== null && x !== '' && typeof x !== 'object');
    return el('span', 'text-muted text-[11px]', parts.map(([k, x]) => `${humanize(k)}: ${x}`).join(' · ') || '—');
  }
  const s = String(v);
  if (/^(INS|MNT|CTX|REG|COR|FB|CLM|RULE)-[\w-]+$/.test(s)) return chip(s);
  if (ID_RE.test(s) && s.length < 40 && !/\s/.test(s)) return el('span', 'font-mono whitespace-nowrap', s);
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return el('span', 'font-mono whitespace-nowrap', fmtDate(s, s.length > 10));
  return el('span', '', s);
}

function kvList(obj) {
  const wrap = el('div', 'grid grid-cols-[minmax(110px,170px)_1fr] gap-x-4 gap-y-1.5 text-[12px]');
  for (const [k, v] of Object.entries(obj || {})) {
    wrap.append(el('div', 'text-muted', sentence(k)), el('div', 'min-w-0 break-words', valueNode(v, k)));
  }
  return wrap;
}

function cellNode(v, key) {
  if (typeof v === 'boolean' && /producing/.test(key)) return badge(v ? 'producing' : 'not producing', v ? 'success' : 'danger');
  if (typeof v === 'boolean' && /still_open|overdue|breach/.test(key)) return v ? badge(key === 'still_open' ? 'open' : humanize(key), 'danger') : el('span', 'text-muted', 'No');
  if (typeof v === 'string' && cellTone(v)) return badge(v, cellTone(v));
  if (typeof v === 'string' && /out of date/i.test(v)) {
    return el('span', 'inline-flex flex-wrap items-center gap-1', v.replace(/\s*\(?out of date\)?/i, ''), badge('out of date', 'warning'));
  }
  if (key === 'status' && typeof v === 'string') {
    const tone = /breach|overdue|open/i.test(v) ? 'text-[#F0787B]' : /met|ok|closed/i.test(v) ? 'text-success' : '';
    return el('span', tone, v);
  }
  return valueNode(v, key);
}

function dataTable(rows, { compact = false } = {}) {
  const list = (rows || []).filter((r) => r && typeof r === 'object');
  if (!list.length) return el('div', 'text-[12px] text-muted py-2', 'No rows.');
  const cols = [];
  list.forEach((r) => Object.keys(r).forEach((k) => { if (!cols.includes(k) && k !== 'lat' && k !== 'lon') cols.push(k); }));
  const thead = el('thead', 'bg-surface-2 text-muted text-[10px] uppercase tracking-wider border-b border-border',
    el('tr', '', cols.map((c) => el('th', `${compact ? 'py-1.5 px-2' : 'py-2.5 px-3'} font-medium font-mono whitespace-nowrap text-left`, humanize(c)))));
  const marker = (r) => {
    const t = cellTone(r.status);
    if (t === 'danger' || r.breach === true) return 'border-l-2 border-l-danger';
    if (t === 'warning' || r.still_open === true) return 'border-l-2 border-l-warning';
    return 'border-l-2 border-l-transparent';
  };
  const tbody = el('tbody', 'divide-y divide-border', list.map((r) => el('tr', 'hover:bg-surface-2/50',
    cols.map((c, i) => el('td', `${i === 0 && !compact ? marker(r) : ''} ${compact ? 'py-1.5 px-2' : 'py-2.5 px-3'} align-top ${c === 'well' || c === 'asset_id' ? 'font-mono font-semibold whitespace-nowrap' : ''} ${/reason|detail|note|text|cause/.test(c) ? 'min-w-[220px] text-muted' : ''}`, cellNode(r[c], c))))));
  return fadeScroll(el('div', `overflow-auto scroll-thin ${compact ? 'max-h-[260px] border border-border rounded-chip' : 'max-h-[460px]'}`,
    el('table', `w-full text-left ${compact ? 'text-[11px]' : 'text-[12px]'}`, thead, tbody)));
}

// A soft fade on the right edge says "there is more, scroll sideways".
function fadeScroll(scroller) {
  const wrap = el('div', 'fade-wrap', scroller, el('div', 'fade-hint'));
  scroller.addEventListener('scroll', () => updateFade(wrap), { passive: true });
  return wrap;
}
function updateFade(wrap) {
  const sc = wrap.firstElementChild;
  if (!sc) return;
  wrap.classList.toggle('has-more', sc.scrollWidth - sc.clientWidth - sc.scrollLeft > 4);
}
function updateFades() {
  document.querySelectorAll('.fade-wrap').forEach(updateFade);
}

// ---------- visuals ----------

const mountHooks = [];
function afterMount(fn) { mountHooks.push(fn); }
function runMountHooks() {
  while (mountHooks.length) {
    const fn = mountHooks.shift();
    try { fn(); } catch (err) { console.warn('visual mount failed', err); }
  }
  requestAnimationFrame(updateFades);
}

const liveMaps = new Set();
let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    liveMaps.forEach((m) => { if (m.getContainer().isConnected) m.invalidateSize(); else liveMaps.delete(m); });
    updateFades();
  }, 120);
});

// Fixed-size stand-in shown until the chart library is ready, so nothing jumps.
function libPlaceholder(box, label) {
  box.setAttribute('aria-busy', 'true');
  box.replaceChildren(el('div', 'w-full h-full skel flex items-center justify-center', el('span', 'relative text-[11px] font-mono text-muted', label)));
}
function libFailed(box, what) {
  box.removeAttribute('aria-busy');
  box.replaceChildren(el('div', 'w-full h-full flex items-center justify-center text-[12px] text-muted bg-surface-2 rounded-chip',
    `Couldn't load the ${what}. The numbers are under View data.`));
}

function frame(title, legend, body, footer) {
  return el('div', 'bg-surface border border-border rounded-card overflow-hidden',
    el('div', 'px-4 pt-3.5 pb-2.5 flex items-start justify-between gap-3 flex-wrap',
      el('div', 'font-mono text-[12px] font-semibold uppercase tracking-[0.06em]', title || ''), legend || null),
    el('div', 'px-4 pb-3', body),
    footer ? el('div', 'px-4 py-2.5 border-t border-border text-[11px] font-mono text-muted', footer) : null);
}

function viewData(rows) {
  if (!rows?.length) return null;
  const d = el('details', 'border-t border-border');
  d.append(el('summary', 'px-4 py-2 text-[11px] font-mono text-accent', el('span', 'caret mr-1', '›'), `View data · ${rows.length} row${rows.length === 1 ? '' : 's'}`));
  d.append(dataTable(rows));
  return d;
}

// Extra facts tools put next to their rows: period, totals, excluded detections.
function cardFacts(card) {
  if (!card || typeof card !== 'object') return null;
  const bits = [];
  if (card.period?.label) bits.push(card.period.label);
  if (card.total_bbl != null) bits.push(`total ${fmtNum(card.total_bbl)} bbl`);
  if (card.breaches != null) bits.push(`${card.breaches} breach${card.breaches === 1 ? '' : 'es'}`);
  if (card.fines_usd) bits.push(`fines USD ${fmtNum(card.fines_usd)}`);
  if (card.open_fines_usd) bits.push(`open fines USD ${fmtNum(card.open_fines_usd)}`);
  if (card.next?.well) bits.push(`next: ${card.next.well}${card.next.downtime_days != null ? ` · ${card.next.downtime_days} days` : ''}`);
  const wrap = el('div', 'space-y-1.5');
  if (bits.length) wrap.append(el('div', '', bits.join(' · ')));
  if (Array.isArray(card.excluded_detections) && card.excluded_detections.length) {
    const d = el('details', '');
    d.append(el('summary', 'text-muted', el('span', 'caret mr-1', '›'), `${card.excluded_detections.length} AI detection${card.excluded_detections.length === 1 ? '' : 's'} excluded after correction`));
    d.append(el('div', 'mt-1.5 font-sans', dataTable(card.excluded_detections, { compact: true })));
    wrap.append(d);
  }
  return wrap.childNodes.length ? wrap : null;
}

function legendItem(swatch, label) {
  return el('span', 'inline-flex items-center gap-1.5 text-[11px] font-mono text-muted', swatch, label);
}

function renderLine(v) {
  const rows = (v.rows || []).filter((r) => r && r.date);
  if (!rows.length) throw new Error('no dated rows');
  const x = rows.map((r) => String(r[v.hint?.x || 'date']).slice(0, 10));
  const plot = el('div', 'w-full h-[320px]');
  libPlaceholder(plot, 'Drawing chart…');
  const traces = [];
  if (rows.some((r) => typeof r.wellhead_temp_c === 'number')) {
    traces.push({ x, y: rows.map((r) => r.wellhead_temp_c), name: 'Wellhead', mode: 'lines+markers', line: { color: C.text, width: 2 }, marker: { size: 5, color: C.text } });
  }
  if (rows.some((r) => typeof r.air_temp_c === 'number')) {
    traces.push({ x, y: rows.map((r) => r.air_temp_c), name: 'Air', mode: 'lines', line: { color: C.slate, width: 2, dash: 'dash' } });
    if (rows.some((r) => typeof r.threshold_c === 'number')) {
      traces.push({ x, y: rows.map((r) => (typeof r.threshold_c === 'number' ? r.air_temp_c + r.threshold_c : null)), name: 'Air + rule margin', mode: 'lines', line: { color: C.accent, width: 1, dash: 'dot' }, opacity: 0.6 });
    }
  }
  if (!traces.length) throw new Error('no temperature columns');
  const off = rows.filter((r) => r.producing === false);
  if (off.length) {
    traces.push({ x: off.map((r) => String(r.date).slice(0, 10)), y: off.map((r) => r.wellhead_temp_c), name: 'not producing', mode: 'markers', marker: { size: 11, color: C.danger, line: { color: C.bg, width: 2 } } });
  }
  afterMount(() => needLib('plotly').then(() => {
    plot.replaceChildren();
    plot.removeAttribute('aria-busy');
    return Plotly.newPlot(plot, traces, {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', height: 320,
    margin: { l: 48, r: 12, t: 8, b: plot.clientWidth < 560 ? 72 : 40 },
    font: { family: 'JetBrains Mono, monospace', size: 11, color: C.muted },
    xaxis: { gridcolor: C.border, linecolor: C.border, tickfont: { color: C.faint } },
    yaxis: { gridcolor: C.border, zeroline: false, ticksuffix: '°C', tickfont: { color: C.faint } },
    legend: plot.clientWidth < 560
      ? { orientation: 'h', x: 0, xanchor: 'left', y: -0.22, font: { color: C.muted } }
      : { orientation: 'h', x: 1, xanchor: 'right', y: 1.12, font: { color: C.muted } },
    hovermode: 'x unified', hoverlabel: { bgcolor: C.surface2, bordercolor: C.border, font: { color: C.text } },
  }, { displayModeBar: false, responsive: true }).then(() => setTimeout(() => Plotly.Plots.resize(plot), 150));
  }).catch(() => libFailed(plot, 'chart')));
  return frame(v.title, null, plot, cardFacts(v.card));
}

function renderBar(v) {
  const rows = (v.rows || []).filter((r) => r && typeof r === 'object');
  const xKey = v.hint?.x || Object.keys(rows[0] || {}).find((k) => typeof rows[0][k] === 'string');
  const yKey = (Array.isArray(v.hint?.y) ? v.hint.y[0] : v.hint?.y) || Object.keys(rows[0] || {}).find((k) => typeof rows[0][k] === 'number');
  const bars = rows.filter((r) => typeof r[yKey] === 'number');
  if (!xKey || !yKey || !bars.length) throw new Error('bar needs x and a numeric y');
  const max = Math.max(...bars.map((r) => r[yKey]), 1);
  const types = [...new Set(bars.map((r) => r.type).filter(Boolean))];
  const shades = ['#64748B', '#9AA8BA', '#3A4452', '#7C8AA0'];
  const shade = (t) => shades[Math.max(0, types.indexOf(t)) % shades.length];
  const unit = /days/.test(yKey) ? 'days' : /bbl/.test(yKey) ? 'bbl' : /usd/.test(yKey) ? 'USD' : '';
  const list = el('div', 'divide-y divide-border/0 space-y-2.5 pt-1');
  for (const r of bars) {
    const fill = el('div', 'h-full rounded-[2px]');
    fill.style.width = `${Math.max(1, (r[yKey] / max) * 100)}%`;
    fill.style.background = r.type ? shade(r.type) : C.slate;
    list.append(el('div', 'grid grid-cols-[110px_auto_1fr_80px] items-center gap-3',
      el('span', 'font-mono text-[12px] font-semibold truncate', String(r[xKey])),
      r.type ? el('span', 'font-mono text-[10px] text-muted border border-border rounded-chip px-1', r.type) : el('span', ''),
      el('div', 'h-4 bg-surface-2 rounded-[2px]', fill),
      el('span', 'text-right font-mono text-[13px] font-semibold', fmtNum(r[yKey]), unit ? el('span', 'text-[10px] text-muted font-normal ml-1', unit) : null)));
  }
  const legend = types.length > 1 ? el('div', 'flex gap-3', types.map((t) => {
    const sw = el('span', 'w-2.5 h-2.5 rounded-[2px] inline-block');
    sw.style.background = shade(t);
    return legendItem(sw, t);
  })) : null;
  return frame(v.title || humanize(yKey), legend, el('div', '', el('div', 'text-[11px] text-muted mb-2', `${sentence(yKey)} by ${humanize(xKey)}`), list), cardFacts(v.card));
}

function renderMap(v) {
  const rows = (v.rows || []).filter((r) => r && typeof r.lat === 'number' && typeof r.lon === 'number');
  if (!rows.length) throw new Error('no coordinates');
  const box = el('div', 'w-full h-[360px] rounded-chip overflow-hidden border border-border');
  libPlaceholder(box, 'Loading map…');
  const overdue = (r) => /overdue|not filed|missing|none filed/i.test(String(r.regulator_report || ''));
  afterMount(() => needLib('leaflet').then(() => {
    box.replaceChildren();
    box.removeAttribute('aria-busy');
    const map = L.map(box, { scrollWheelZoom: false, attributionControl: true });
    liveMaps.add(map);
    // CARTO's dark tiles now answer "API key required", so use Esri's keyless dark canvas.
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors', maxZoom: 16,
    }).addTo(map);
    const pts = [];
    for (const r of rows) {
      const vol = typeof r.volume_bbl === 'number' ? r.volume_bbl : null;
      const late = overdue(r);
      const marker = L.circleMarker([r.lat, r.lon], {
        radius: vol ? Math.min(22, 6 + Math.sqrt(vol) * 4) : 7,
        color: late ? C.warning : C.danger, weight: late ? 3 : 1.5, fillColor: C.danger, fillOpacity: 0.45,
      }).addTo(map);
      const pop = el('div', 'space-y-1',
        el('div', 'font-mono font-semibold', String(r.well || r.asset_id || 'Well')),
        r.date ? el('div', 'text-muted', fmtDate(r.date)) : null,
        vol != null ? el('div', '', `Volume ${fmtNum(vol)} bbl${r.class ? ` · ${r.class}` : ''}`) : null,
        r.regulator_report ? el('div', late ? 'text-warning' : 'text-muted', `Regulator report: ${r.regulator_report}`) : null,
        r.inspection_id ? el('div', '', chip(String(r.inspection_id))) : null);
      marker.bindPopup(pop);
      pts.push([r.lat, r.lon]);
    }
    if (pts.length === 1) map.setView(pts[0], 12);
    else map.fitBounds(pts, { padding: [36, 36], maxZoom: 13 });
    requestAnimationFrame(() => map.invalidateSize());
    setTimeout(() => map.invalidateSize(), 250);
  }).catch(() => libFailed(box, 'map')));
  const dot = (color, ring) => {
    const s = el('span', 'w-2.5 h-2.5 rounded-full inline-block');
    s.style.background = ring ? 'transparent' : color;
    s.style.border = `2px solid ${color}`;
    return s;
  };
  const legend = el('div', 'flex gap-3', legendItem(dot(C.danger), 'Spill'), rows.some(overdue) ? legendItem(dot(C.warning, true), 'Report overdue') : null,
    rows.some((r) => typeof r.volume_bbl === 'number') ? el('span', 'text-[11px] font-mono text-faint', 'size = volume') : null);
  const skipped = (v.rows || []).length - rows.length;
  const facts = cardFacts(v.card);
  const footer = skipped > 0 ? el('div', '', facts, el('div', '', `${skipped} row${skipped === 1 ? '' : 's'} without coordinates not shown`)) : facts;
  return frame(v.title, legend, box, footer);
}

function monthLabel(col) {
  const m = String(col).match(/^\d{4}-(\d{2})$/);
  return m ? MONTHS[Number(m[1]) - 1] : String(col);
}

function heatColor(t) {
  const a = [0x1c, 0x24, 0x2e];
  const b = [0xe5, 0x48, 0x4d];
  return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(',')})`;
}

function renderHeatmap(v) {
  const rows = (v.rows || []).filter((r) => r && typeof r === 'object');
  const yKey = v.hint?.y || 'area';
  const cols = Array.isArray(v.hint?.columns) && v.hint.columns.length
    ? v.hint.columns
    : Object.keys(rows[0] || {}).filter((k) => k !== yKey && k !== 'total' && typeof rows[0][k] === 'number');
  if (!rows.length || !cols.length) throw new Error('heatmap needs rows and columns');
  const values = rows.flatMap((r) => cols.map((c) => Number(r[c]) || 0));
  const max = Math.max(...values, 1);
  const totals = rows.map((r) => (typeof r.total === 'number' ? r.total : cols.reduce((s, c) => s + (Number(r[c]) || 0), 0)));
  const maxTotal = Math.max(...totals);
  const head = el('tr', '', el('th', 'text-left py-2 pr-3 font-medium', humanize(yKey)), cols.map((c) => el('th', 'py-2 px-0.5 font-medium text-center', monthLabel(c))), el('th', 'py-2 pl-4 text-right font-medium border-l border-border', 'Total'));
  const body = rows.map((r, i) => el('tr', '',
    el('td', 'py-1 pr-3 font-mono text-[12px] whitespace-nowrap', String(r[yKey] ?? '')),
    cols.map((c) => {
      const n = Number(r[c]) || 0;
      const t = n / max;
      const cell = el('div', `h-9 min-w-[34px] rounded-[3px] flex items-center justify-center font-mono text-[12px] ${n === 0 ? 'text-faint' : t > 0.55 ? 'text-white' : 'text-text'}`, String(n));
      cell.style.background = heatColor(t);
      cell.title = `${r[yKey]} · ${monthLabel(c)}: ${n}`;
      return el('td', 'p-0.5', cell);
    }),
    el('td', `py-1 pl-4 text-right font-mono text-[13px] border-l border-border ${totals[i] === maxTotal ? 'text-[#F0787B] font-semibold' : ''}`, fmtNum(totals[i]))));
  const scale = el('div', 'flex items-center gap-1 font-mono text-[10px] text-muted', '0', [0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1].map((t) => {
    const s = el('span', 'w-4 h-2.5 rounded-[2px] inline-block');
    s.style.background = heatColor(t);
    return s;
  }), String(max));
  const table = fadeScroll(el('div', 'overflow-x-auto scroll-thin', el('table', 'w-full text-[10px] uppercase text-muted font-mono', el('thead', '', head), el('tbody', 'normal-case', body))));
  return frame(v.title, scale, table, cardFacts(v.card));
}

function row(label, ...content) {
  return el('div', 'flex items-start gap-2', el('span', 'text-muted shrink-0 w-32', label), el('div', 'min-w-0 flex-1', content));
}

function correctionLine(c) {
  return el('div', 'border border-success/30 bg-success/10 rounded-card px-3 py-2 text-[11px] font-mono flex flex-wrap items-center gap-x-2 gap-y-1',
    el('span', 'text-success font-semibold', 'CORRECTION APPLIED'),
    c.correction_id ? chip(c.correction_id) : null,
    c.field ? el('span', '', humanize(c.field)) : null,
    el('span', 'line-through text-muted', String(c.original_value ?? '—')), '→',
    el('span', 'text-success font-semibold', String(c.corrected_value ?? '—')),
    el('span', 'font-sans text-muted', `corrected by ${c.corrected_by || c.author || 'someone'}${c.reason ? ` — ${c.reason}` : ''}`));
}

function disagreeBox(items, title = 'Records disagree') {
  const list = (items || []).filter(Boolean);
  if (!list.length) return null;
  return el('div', 'border border-warning/40 border-l-2 border-l-warning bg-warning/5 rounded-card p-3 text-[12px] space-y-1',
    el('div', 'flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[0.06em] text-warning', svg(ICONS.warn), title),
    list.map((t) => el('div', 'text-text', typeof t === 'string' ? t : Object.values(t).filter((x) => typeof x === 'string').join(' · '))));
}

function renderWellStatus(card) {
  const ins = card.latest_inspection || {};
  const rule = card.rule || {};
  const producing = card.producing_now;
  const status = producing === true ? badge('● PRODUCING', 'success') : producing === false ? badge('● NOT PRODUCING', 'danger') : badge('UNKNOWN', 'neutral');
  const loc = card.location && typeof card.location.lat === 'number' ? `${card.location.lat}, ${card.location.lon}` : null;
  const left = [['Type', card.well_type], ['Area', card.area], ['Team', card.team]];
  const right = [['In charge', card.in_charge], ['Location', loc], ['Downtime this year', card.downtime_days_this_year != null ? `${card.downtime_days_this_year} days` : null]];
  const col = (pairs) => el('div', 'space-y-1.5', pairs.filter(([, val]) => val != null && val !== '').map(([k, val]) => el('div', 'flex justify-between gap-3 text-muted', el('span', '', k), el('span', 'text-text text-right', String(val)))));
  const above = typeof ins.temp_above_air_c === 'number' ? ins.temp_above_air_c : null;
  const readout = above != null ? el('div', 'font-mono text-[11px] bg-surface-2 px-2.5 py-1 rounded-chip border border-border text-muted',
    `Wellhead ${ins.wellhead_temp_c} °C · Air ${ins.ambient_temp_c} °C · Δ ${above} °C ${rule.min_above_air_c != null ? `${above >= rule.min_above_air_c ? '≥' : '<'} ${rule.min_above_air_c} °C` : ''}`) : null;
  const tempChanged = ins.original_wellhead_temp_c != null && ins.original_wellhead_temp_c !== ins.wellhead_temp_c;

  const details = el('div', 'space-y-2 text-[12px]');
  if (ins.inspection_id) {
    details.append(row('Last inspection', ins.inspection_id ? chip(ins.inspection_id) : null,
      ` · ${[fmtDate(ins.inspected_at, true), ins.method, ins.inspector].filter(Boolean).join(' · ')}`,
      ins.inspector_note ? el('span', 'italic', ` · "${ins.inspector_note}"`) : null,
      tempChanged ? el('div', 'text-[11px] text-muted mt-0.5', 'Wellhead reading corrected from ', el('span', 'line-through', `${ins.original_wellhead_temp_c} °C`), ` to ${ins.wellhead_temp_c} °C`) : null));
  }
  if (card.reasons_not_producing?.length) details.append(row('Why not producing', el('ul', 'list-disc pl-4 space-y-0.5', card.reasons_not_producing.map((r) => el('li', '', String(r))))));
  for (const e of card.open_regulatory_events || []) {
    details.append(row('Open regulator item', el('span', 'inline-flex flex-wrap items-center gap-1.5', e.event_id ? chip(e.event_id) : null,
      badge([humanize(e.event_type || ''), e.protocol_id, fmtDate(e.date), e.fine_usd ? `USD ${fmtNum(e.fine_usd)}` : null, 'OPEN'].filter(Boolean).join(' · '), 'danger'))));
  }
  const rep = card.last_repair;
  if (rep && (rep.maintenance_id || rep.issue)) {
    details.append(row('Last repair', rep.maintenance_id ? chip(rep.maintenance_id) : null,
      ` · ${[fmtDate(rep.started_at), rep.issue ? `"${rep.issue}"` : null, rep.cost_total_usd != null ? `USD ${fmtNum(rep.cost_total_usd)}` : null].filter(Boolean).join(' · ')}`));
  }
  for (const n of card.current_and_planned_notes || []) {
    details.append(row('Active note', n.note_id ? chip(n.note_id) : null, ` · ${n.author || ''} `,
      n.valid_from ? badge(`valid ${shortDate(n.valid_from)}–${n.valid_to ? shortDate(n.valid_to) : 'open'}`, 'success') : null,
      el('div', 'text-muted italic text-[11px] mt-1', `"${n.text}"`)));
  }
  for (const c of card.user_claims || []) {
    details.append(row('User statement', c.claim_id ? chip(c.claim_id) : null, ` · ${c.author || ''} `,
      badge(c.status || 'unconfirmed', /confirm/.test(c.status || '') && !/un/.test(c.status || '') ? 'success' : 'warning'),
      el('div', 'italic text-[11px] mt-1', `"${c.statement}"`)));
  }
  const ruleLine = rule.min_above_air_c != null
    ? `Rule: producing when the wellhead is at least ${rule.min_above_air_c} °C above air (${rule.source || 'manual'})${rule.overridden_by_team_rule ? ' · team rule applies' : ''}`
    : null;

  return el('div', 'space-y-2',
    el('div', 'bg-surface border border-border rounded-card p-4 text-[13px] space-y-3.5',
      el('div', 'flex items-center justify-between gap-3 pb-2.5 border-b border-border',
        el('div', 'flex items-baseline gap-2 min-w-0', el('span', 'font-mono font-semibold text-[18px]', card.asset_id || ''), el('span', 'text-[11px] text-muted truncate', card.name || '')), status),
      el('div', 'grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-1.5 text-[12px]', col(left), col(right)),
      el('div', 'py-2.5 border-y border-border flex items-center justify-between gap-3 flex-wrap text-[12px]',
        el('div', 'flex items-center gap-2', el('span', 'text-muted', 'Recorded status:'), el('span', 'font-medium', card.recorded_status || '—'), card.recorded_status_out_of_date ? badge('out of date', 'warning') : null),
        el('div', 'flex items-center gap-2', el('span', 'text-muted', 'Inferred:'), el('span', producing === false ? 'text-[#F0787B] font-semibold' : 'text-success font-semibold', producing === false ? 'not producing' : producing === true ? 'producing' : 'unknown')),
        readout),
      details,
      ruleLine ? el('div', 'text-[11px] font-mono text-muted pt-2 border-t border-border', ruleLine) : null),
    (card.corrections_applied || []).map(correctionLine),
    disagreeBox(card.disagreements));
}

// Effects often start with a tag like "[breach]"; that tag is the part worth colouring.
function effectLine(text) {
  const m = String(text).match(/^\[([a-z ]+)\]\s*(.*)$/i);
  if (!m) return el('li', 'flex gap-2', el('span', 'text-faint', '•'), el('span', '', String(text)));
  return el('li', 'flex items-start gap-2', badge(m[1], cellTone(m[1]) || 'neutral'), el('span', '', m[2]));
}

function renderCorrectionCard(card) {
  const before = card.before || {};
  const after = card.after || {};
  const keys = Object.keys(after).filter((k) => JSON.stringify(after[k]) !== JSON.stringify(before[k]));
  return el('div', 'space-y-2',
    el('div', 'bg-surface border border-border border-l-2 border-l-success rounded-card p-4 space-y-3 text-[12px]',
      el('div', 'flex items-center gap-2 flex-wrap', badge('✓ CORRECTION APPLIED', 'success'), card.correction_id ? chip(card.correction_id) : null),
      keys.length ? el('div', 'bg-surface-2 border border-border rounded-card p-3 space-y-1 font-mono', keys.map((k) => {
        const simple = (x) => x === null || x === undefined || typeof x !== 'object';
        if (!simple(after[k]) || !simple(before[k])) {
          return el('div', '', el('span', 'text-muted', `${humanize(k)}: `), el('span', 'font-sans', valueNode(after[k], k)));
        }
        return el('div', '', el('span', 'text-muted', `${humanize(k)}: `), el('span', 'line-through text-[#F0787B]', String(before[k] ?? '—')), ' → ', el('span', 'text-success font-semibold', String(after[k] ?? '—')));
      })) : kvList(after),
      card.effects?.length ? el('div', '', el('div', 'font-mono text-[11px] text-muted mb-1', 'EFFECT'), el('ul', 'space-y-1', card.effects.map(effectLine))) : null),
    disagreeBox(card.disagreements));
}

function renderInspectionCard(card) {
  const top = { ...card };
  ['inspection_id', 'well', 'notes', 'corrections', 'repairs', 'regulatory_events', 'verdict'].forEach((k) => delete top[k]);
  const notes = (card.notes || []).map((n) => el('div', `pl-2 border-l-2 ${n.is_formal_correction ? 'border-success' : 'border-border'} space-y-0.5`,
    el('div', 'flex items-center gap-2 text-[11px] text-muted', n.note_id ? chip(n.note_id) : null, n.author || '', n.is_formal_correction ? badge('formal correction', 'success') : null),
    el('div', 'italic', `"${n.text}"`)));
  return el('div', 'bg-surface border border-border rounded-card p-4 space-y-3 text-[12px]',
    el('div', 'flex items-center justify-between gap-2 pb-2.5 border-b border-border',
      el('div', 'flex items-center gap-2', card.inspection_id ? chip(card.inspection_id) : null, el('span', 'font-mono font-semibold', card.well || '')),
      typeof card.producing === 'boolean' ? (card.producing ? badge('● PRODUCING', 'success') : badge('● NOT PRODUCING', 'danger')) : null),
    kvList(top),
    notes.length ? el('div', 'space-y-2', el('div', 'font-mono text-[11px] text-muted', 'NOTES'), notes) : null,
    ['corrections', 'repairs', 'regulatory_events'].filter((k) => Array.isArray(card[k]) && card[k].length).map((k) => el('div', '', el('div', 'font-mono text-[11px] text-muted mb-1', humanize(k).toUpperCase()), dataTable(card[k], { compact: true }))),
    card.verdict ? el('div', 'text-[11px] font-mono text-muted pt-2 border-t border-border', typeof card.verdict === 'string' ? card.verdict : JSON.stringify(card.verdict)) : null);
}

function renderCard(v) {
  const card = v.card || {};
  if (card.asset_id && 'producing_now' in card) return renderWellStatus(card);
  if (card.correction_id && (card.before || card.after)) return renderCorrectionCard(card);
  if (card.inspection_id) return renderInspectionCard(card);
  if (!Object.keys(card).length) throw new Error('empty card');
  return frame(v.title, null, kvList(card));
}

function renderVisual(v) {
  if (!v || typeof v !== 'object') return null;
  const rows = Array.isArray(v.rows) ? v.rows : [];
  const renderers = { status_card: renderCard, table: null, line: renderLine, bar: renderBar, map: renderMap, heatmap: renderHeatmap };
  const fn = renderers[v.kind];
  const fallback = () => {
    if (rows.length) return frame(v.title, null, dataTable(rows), cardFacts(v.card));
    if (v.card && Object.keys(v.card).length) return frame(v.title, null, kvList(v.card));
    return null;
  };
  let node = null;
  try {
    node = fn ? fn(v) : fallback();
  } catch (err) {
    console.warn('visual fell back to a table', v.kind, err);
    mountHooks.length = 0;
    return fallback();
  }
  if (node && fn && v.kind !== 'status_card' && rows.length) node.append(viewData(rows));
  return node;
}

// ---------- agent graph ----------

const VIOLET = '#9B7BF0';
const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs = {}, text) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  if (text !== undefined) n.textContent = text;
  return n;
}

function clip(text, max) {
  const s = String(text ?? '');
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

// Question → (playbook) → each tool step with the records it touched → answer.
function graphModel(msg) {
  const t = msg.trust || {};
  const playbook = Array.isArray(t.playbook) ? t.playbook : [];
  let steps = Array.isArray(msg.steps) && msg.steps.length ? msg.steps : null;
  if (!steps) {
    const n = t.tool_calls || 0;
    if (!n && !msg.citations?.length) return null;
    steps = [{ tool: null, label: n ? `${n} lookup${n === 1 ? '' : 's'}` : 'Lookups', latency_ms: t.tool_ms, citations: msg.citations || [], summary: '' }];
  }
  const modelMs = (t.model_calls || []).reduce((sum, c) => sum + (Number(c.ms) || 0), 0);
  return { playbook, steps, total: msg.latency_ms ?? (modelMs + (Number(t.tool_ms) || 0) || null), model: modelName(msg.model) };
}

function agentGraph(msg) {
  const g = graphModel(msg);
  if (!g) return null;
  const NODE_W = 132;
  const NODE_H = 44;
  const GAP = 28;
  const REC_H = 22;
  const REC_GAP = 6;
  const cols = [];
  cols.push({ kind: 'question', title: 'Question', sub: '' });
  if (g.playbook.length) cols.push({ kind: 'playbook', title: 'Playbook', sub: `${g.playbook.length} similar pattern${g.playbook.length === 1 ? '' : 's'}` });
  g.steps.forEach((s, i) => {
    const cites = [...new Set((s.citations || []).filter(Boolean))];
    cols.push({
      kind: 'step', index: i, step: s, error: !!s.error,
      title: s.label || TOOL_LABELS[s.tool] || sentence(s.tool || 'step'),
      sub: s.latency_ms != null ? `${fmtNum(Math.round(s.latency_ms))} ms` : '',
      records: cites.slice(0, 6), more: Math.max(0, cites.length - 6),
    });
  });
  cols.push({ kind: 'answer', title: 'Answer', sub: [g.model, g.total ? `${(g.total / 1000).toFixed(1)} s` : ''].filter(Boolean).join(' · ') });

  const maxRecs = Math.max(0, ...cols.map((c) => (c.records?.length || 0) + (c.more ? 1 : 0)));
  const width = cols.length * NODE_W + (cols.length - 1) * GAP + 2;
  const height = NODE_H + (maxRecs ? 16 + maxRecs * (REC_H + REC_GAP) : 0) + 2;
  const root = svgEl('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'How the agent answered, step by step' });
  root.style.display = 'block';
  const detail = el('div', 'hidden mt-2 text-[12px] text-muted border-l border-border pl-3');
  detail.setAttribute('aria-live', 'polite');
  const stroke = { question: C.accent, playbook: VIOLET, step: C.border, answer: C.success };

  const clickable = (node, label, onClick) => {
    node.setAttribute('tabindex', '0');
    node.setAttribute('role', 'button');
    node.setAttribute('aria-label', label);
    node.style.cursor = 'pointer';
    node.addEventListener('click', onClick);
    node.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(); } });
  };

  cols.forEach((c, i) => {
    const x = 1 + i * (NODE_W + GAP);
    if (i > 0) root.append(svgEl('line', { x1: x - GAP, y1: 1 + NODE_H / 2, x2: x, y2: 1 + NODE_H / 2, stroke: C.faint, 'stroke-width': 1 }));
    const grp = svgEl('g');
    grp.append(svgEl('rect', { x, y: 1, width: NODE_W, height: NODE_H, rx: 6, fill: C.surface2, stroke: c.error ? C.danger : stroke[c.kind], 'stroke-width': c.kind === 'step' && !c.error ? 1 : 1.5 }));
    grp.append(svgEl('text', { x: x + 10, y: 19, fill: C.text, 'font-size': 12, 'font-family': 'IBM Plex Sans, sans-serif' }, clip(c.title, 19)));
    if (c.sub) grp.append(svgEl('text', { x: x + 10, y: 35, fill: c.kind === 'playbook' ? VIOLET : C.muted, 'font-size': 10, 'font-family': 'JetBrains Mono, monospace' }, clip(c.sub, 22)));
    const tip = svgEl('title', {}, [c.title, c.sub].filter(Boolean).join(' · '));
    grp.append(tip);
    root.append(grp);
    if (c.kind === 'step') {
      const info = [c.step.summary, c.step.error ? `Error: ${c.step.error}` : ''].filter(Boolean).join(' ');
      if (info) {
        clickable(grp, `${c.title}: show what this step found`, () => {
          const same = detail.dataset.for === String(c.index) && !detail.classList.contains('hidden');
          detail.dataset.for = String(c.index);
          detail.textContent = `${c.title}: ${info}`;
          detail.classList.toggle('hidden', same);
        });
      }
    }
    if (c.kind === 'playbook') {
      clickable(grp, 'Show the learned patterns used', () => {
        const same = detail.dataset.for === 'playbook' && !detail.classList.contains('hidden');
        detail.dataset.for = 'playbook';
        detail.replaceChildren(...g.playbook.map((p) => el('div', 'mb-1', el('span', 'text-text', p.pattern || ''), p.plan ? el('span', 'font-mono text-[11px] ml-2', p.plan) : null,
          p.avoid ? el('span', 'text-warning ml-2', `avoid: ${p.avoid}`) : null)));
        detail.classList.toggle('hidden', same);
      });
    }
    const recs = [...(c.records || []), ...(c.more ? [`+${c.more}`] : [])];
    recs.forEach((r, k) => {
      const y = 1 + NODE_H + 16 + k * (REC_H + REC_GAP);
      const rx = x + 14;
      root.append(svgEl('path', { d: `M ${x + 8} ${1 + NODE_H} V ${y + REC_H / 2} H ${rx}`, fill: 'none', stroke: C.border, 'stroke-width': 1 }));
      const rg = svgEl('g');
      rg.append(svgEl('rect', { x: rx, y, width: NODE_W - 14, height: REC_H, rx: 4, fill: C.surface, stroke: C.border }));
      rg.append(svgEl('text', { x: rx + 7, y: y + 15, fill: r.startsWith('+') ? C.muted : C.accent, 'font-size': 10, 'font-family': 'JetBrains Mono, monospace' }, clip(r, 17)));
      rg.append(svgEl('title', {}, r));
      if (!r.startsWith('+')) clickable(rg, `Open record ${r}`, () => openRecord(r));
      root.append(rg);
    });
  });
  const scroller = el('div', 'overflow-x-auto scroll-thin pb-1');
  scroller.append(root);
  return el('div', 'mb-1', fadeScroll(scroller), detail);
}

// ---------- playbook ----------

let playbookState = { status: 'idle', data: null, error: '' };
let playbookController = null;

async function loadPlaybook() {
  playbookController?.abort();
  const controller = new AbortController();
  playbookController = controller;
  playbookState = { status: 'loading', data: playbookState.data, error: '' };
  if (drawerOpen() && state.memoryTab === 'playbook') renderMemory();
  try {
    const data = await api('/api/playbook', {}, controller.signal);
    if (controller !== playbookController) return;
    playbookState = { status: 'ready', data, error: '' };
  } catch (err) {
    if (isAbort(err) || controller !== playbookController) return;
    playbookState = { status: 'error', data: null, error: err.message };
  }
  if (drawerOpen()) renderMemory();
}

function thumbCount(icon, n, label) {
  const s = el('span', 'inline-flex items-center gap-1', svg(icon, 'w-3 h-3'), String(n ?? 0));
  s.title = label;
  s.setAttribute('aria-label', `${n ?? 0} ${label}`);
  return s;
}

function playbookItems(data) {
  const list = [...(data?.patterns || [])].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const top = Math.max(1, ...list.map((p) => Math.abs(p.score ?? 0)));
  return list.map((p) => {
    const score = Number(p.score) || 0;
    const fill = el('div', `h-full rounded-full ${score < 0 ? 'bg-danger' : ''}`);
    fill.style.width = `${Math.max(3, (Math.abs(score) / top) * 100)}%`;
    if (score >= 0) fill.style.background = VIOLET;
    return el('div', 'bg-surface-2 border border-border rounded-card p-3 space-y-2',
      el('p', 'text-[12px] leading-relaxed', p.pattern || ''),
      p.plan ? el('div', 'font-mono text-[11px] text-accent break-words', p.plan) : null,
      el('div', 'flex items-center justify-between gap-3 flex-wrap text-[11px] text-muted',
        el('span', 'inline-flex items-center gap-2 font-mono',
          thumbCount(ICONS.up, p.thumbs_up, 'thumbs up'), el('span', 'text-faint', '·'), thumbCount(ICONS.down, p.thumbs_down, 'thumbs down'), el('span', 'text-faint', '·'),
          `passed ${p.eval_passes ?? 0}`, el('span', 'text-faint', '·'), `used ${p.uses ?? 0}`, p.visual ? [el('span', 'text-faint', '·'), p.visual] : null),
        el('span', 'inline-flex items-center gap-2', el('span', 'w-24 h-1.5 rounded-full bg-surface overflow-hidden', fill), el('span', 'font-mono', `score ${fmtNum(score)}`))));
  });
}

// ---------- answer bubble ----------

function parseStep(text) {
  const s = String(text || '');
  const space = s.indexOf(' ');
  const tool = space < 0 ? s : s.slice(0, space);
  let detail = '';
  try {
    const args = JSON.parse(space < 0 ? '{}' : s.slice(space + 1));
    detail = Object.values(args).filter((x) => x !== null && x !== '' && typeof x !== 'object' || Array.isArray(x))
      .map((x) => (Array.isArray(x) ? x.join(', ') : humanize(String(x)))).filter((x) => x.length < 60).join(' · ');
  } catch { detail = ''; }
  return { label: TOOL_LABELS[tool] || sentence(tool), detail };
}

function summaryLine(msg) {
  const t = msg.trust || {};
  const tools = [...new Set((msg.steps || []).map((s) => TOOL_LABELS[s.tool] || sentence(s.tool || '')))];
  const bits = [];
  if (tools.length) bits.push(tools.join(', '));
  const via = (msg.steps || []).map((s) => String(s.summary || '').match(/\bvia (hybrid|semantic|keyword|bm25)/i)).find(Boolean);
  if (via) {
    const how = via[1].toLowerCase();
    bits.push(how === 'hybrid' ? 'searched with Smart (meaning + exact words)' : how === 'semantic' ? 'searched by meaning' : 'searched by exact words');
  }
  else if (t.tool_calls != null) bits.push(`${t.tool_calls} lookup${t.tool_calls === 1 ? '' : 's'}`);
  if (t.records_cited != null) bits.push(`${t.records_cited} record${t.records_cited === 1 ? '' : 's'} cited`);
  if (t.rules_applied?.length) bits.push(`${t.rules_applied.length} rule${t.rules_applied.length === 1 ? '' : 's'} applied`);
  const flags = (t.warnings?.length || 0) + (t.ungrounded_numbers?.length || 0) + (t.unknown_citations_removed?.length || 0);
  if (flags) bits.push(`${flags} check${flags === 1 ? '' : 's'} flagged`);
  return bits.join(' · ');
}

function howAnswered(msg) {
  const t = msg.trust || {};
  const line = summaryLine(msg);
  if (!line && !msg.steps?.length && !msg.citations?.length) return null;
  const d = el('details', 'text-[12px]');
  const flagged = t.warnings?.length || t.ungrounded_numbers?.length || t.unknown_citations_removed?.length || t.degraded;
  d.append(el('summary', 'flex items-center gap-2 text-muted hover:text-text',
    el('span', 'caret', '›'), el('span', 'font-mono text-[11px] uppercase tracking-[0.06em]', 'How this was answered'),
    el('span', `text-[11px] truncate ${flagged ? 'text-warning' : ''}`, line)));
  const body = el('div', 'mt-2 ml-4 pl-3 border-l border-border space-y-2');
  try {
    const graph = agentGraph(msg);
    if (graph) body.append(graph);
  } catch (err) {
    console.warn('agent graph skipped', err);
  }
  if (msg.steps?.length) {
    body.append(el('ol', 'space-y-1', msg.steps.map((s) => el('li', 'flex flex-wrap gap-x-2',
      el('span', 'font-mono text-faint', `${s.number ?? ''}.`),
      el('span', s.error ? 'text-[#F0787B]' : 'text-text', TOOL_LABELS[s.tool] || sentence(s.tool || 'step')),
      s.summary ? el('span', 'text-muted', s.summary) : null,
      s.error ? el('span', 'text-[#F0787B]', s.error) : null,
      s.latency_ms != null ? el('span', 'font-mono text-faint', `${fmtNum(Math.round(s.latency_ms))} ms`) : null))));
  }
  const list = (label, items, tone) => (items?.length ? el('div', '', el('div', `font-mono text-[10px] uppercase tracking-wider ${tone || 'text-muted'}`, label),
    el('ul', 'list-disc pl-4 text-muted', items.map((x) => el('li', '', String(x))))) : null);
  body.append(...[
    list('Rules applied', t.rules_applied),
    list('Warnings', t.warnings, 'text-warning'),
    list('Numbers not found in the records', t.ungrounded_numbers, 'text-warning'),
    list('Citations removed (not in the records)', t.unknown_citations_removed, 'text-warning'),
    t.degraded ? el('div', 'text-warning', 'The model was degraded for this answer; treat it with care.') : null,
  ].filter(Boolean));
  d.append(body);
  return d;
}

function evidenceRow(citations) {
  const ids = [...new Set((citations || []).filter(Boolean))];
  if (!ids.length) return null;
  const wrap = el('div', 'flex flex-wrap items-center gap-1.5', el('span', 'font-mono text-[10px] uppercase tracking-wider text-muted mr-1', 'Evidence'));
  const shown = 10;
  ids.slice(0, shown).forEach((id) => wrap.append(chip(id)));
  if (ids.length > shown) {
    const more = el('button', 'text-[11px] font-mono text-accent hover:underline', `+${ids.length - shown} more`);
    more.type = 'button';
    more.addEventListener('click', () => { more.remove(); ids.slice(shown).forEach((id) => wrap.append(chip(id))); });
    wrap.append(more);
  }
  return wrap;
}

function ratingBar(msg) {
  if (!msg.message_id) return null;
  const btn = (icon, label) => {
    const b = el('button', 'rate-btn w-8 h-8 rounded-chip border border-border text-muted hover:text-text hover:border-[#3A4452] flex items-center justify-center transition-colors', svg(icon));
    b.type = 'button';
    b.title = label;
    b.setAttribute('aria-label', label);
    b.setAttribute('aria-pressed', 'false');
    return b;
  };
  const up = btn(ICONS.up, 'Good answer');
  const down = btn(ICONS.down, 'Something was wrong');
  const note = el('span', 'text-[11px] text-muted inline-flex items-center gap-1');
  note.setAttribute('aria-live', 'polite');
  let saved = msg.rating === 'up' || msg.rating === 'down' ? msg.rating : null;
  let savedComment = msg.rating_comment || '';
  let settleTimer = null;
  const form = el('div', 'hidden w-full mt-1 flex flex-col gap-2');
  const area = el('textarea', 'w-full bg-surface-2 border border-border rounded-card p-2 text-[12px] focus:outline-none focus:border-accent resize-y');
  area.rows = 2;
  area.placeholder = 'What was wrong?';
  area.setAttribute('aria-label', 'What was wrong?');
  const save = el('button', 'self-start px-3 py-1 rounded-chip bg-accent text-bg text-[12px] font-medium hover:bg-[#3A8BD6]', 'Save');
  save.type = 'button';
  form.append(area, save);
  const commentBox = el('div', 'w-full');
  const mark = (rating) => [[up, 'up'], [down, 'down']].forEach(([b, r]) => {
    const on = r === rating;
    b.classList.toggle('rated', on);
    b.setAttribute('aria-pressed', String(on));
  });
  const showSettled = () => {
    note.replaceChildren(saved ? 'Rated' : '');
    commentBox.replaceChildren();
    if (saved === 'down' && savedComment) {
      const d = el('details', 'text-[12px] mt-1');
      d.append(el('summary', 'text-muted text-[11px]', el('span', 'caret mr-1', '›'), 'Your note'), el('div', 'mt-1 pl-3 border-l border-border text-muted whitespace-pre-wrap', savedComment));
      commentBox.append(d);
    }
  };
  const lock = (on) => [up, down, save].forEach((b) => { b.disabled = on; b.classList.toggle('opacity-50', on); });
  const send = async (rating, comment) => {
    clearTimeout(settleTimer);
    lock(true);
    note.replaceChildren(el('span', 'spin !w-3 !h-3'), 'Saving…');
    try {
      await api('/api/rate', { user_id: state.user.user_id, message_id: msg.message_id, rating, comment: comment || null });
      saved = rating;
      savedComment = rating === 'down' ? (comment || '') : '';
      mark(saved);
      note.replaceChildren(el('span', 'text-success tick-in', svg(ICONS.check, 'w-3 h-3')), 'Saved');
      settleTimer = setTimeout(showSettled, 1400);
    } catch {
      mark(saved);
      note.replaceChildren(el('span', 'text-warning', "Couldn't save"));
    } finally {
      lock(false);
    }
  };
  up.addEventListener('click', () => {
    form.classList.add('hidden');
    if (saved === 'up') return;
    mark('up');
    send('up');
  });
  down.addEventListener('click', () => {
    mark('down');
    area.value = savedComment;
    form.classList.toggle('hidden', saved === 'down' && !form.classList.contains('hidden'));
    if (!form.classList.contains('hidden')) area.focus();
  });
  save.addEventListener('click', () => { form.classList.add('hidden'); send('down', area.value.trim()); });
  mark(saved);
  showSettled();
  return el('div', 'flex flex-wrap items-center gap-2', up, down, note, form, commentBox);
}

// The first sentence reads as the headline of the answer.
function markLead(box) {
  const first = box.firstElementChild;
  if (!first || first.tagName !== 'P') return;
  const walker = document.createTreeWalker(first, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement.closest('button')) continue;
    const m = node.nodeValue.match(/[.!?](\s|$)/);
    if (!m) continue;
    const range = document.createRange();
    range.setStart(first, 0);
    range.setEnd(node, m.index + 1);
    const lead = el('span', 'lead');
    lead.append(range.extractContents());
    first.insertBefore(lead, first.firstChild);
    return;
  }
  first.classList.add('lead');
}

const KEY_NUMBER = /(?<![\w-])(USD\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:days?|bbl|°C|wells?|spills?|inspections?|repairs?|hours?|%))(?![\w])/g;
function emphasiseNumbers(box) {
  const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (node.parentElement.closest('button, code, strong, b, th')) continue;
    const text = node.nodeValue;
    KEY_NUMBER.lastIndex = 0;
    if (!KEY_NUMBER.test(text)) continue;
    KEY_NUMBER.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    let m;
    while ((m = KEY_NUMBER.exec(text))) {
      frag.append(text.slice(last, m.index), el('strong', 'font-semibold text-text', m[0]));
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

function learnedBadge(msg) {
  const used = Array.isArray(msg.trust?.playbook) ? msg.trust.playbook : [];
  if (!used.length) return null;
  const b = el('span', 'inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border whitespace-nowrap', 'Learned plan used');
  b.style.color = VIOLET;
  b.style.borderColor = `${VIOLET}55`;
  b.style.background = `${VIOLET}1A`;
  b.title = used.map((p) => `${p.pattern || ''}${p.plan ? ` → ${p.plan}` : ''}`).join('\n');
  return b;
}

function answerHeader(msg) {
  const v = msg.visual || {};
  const icon = KIND_ICONS[v.kind] || KIND_ICONS.answer;
  const producing = v.card && typeof v.card.producing_now === 'boolean' ? v.card.producing_now : null;
  const meta = [modelName(msg.model), msg.latency_ms != null ? `${(msg.latency_ms / 1000).toFixed(1)} s` : null].filter(Boolean).join(' · ');
  return el('div', 'flex items-center justify-between gap-3 flex-wrap',
    el('div', 'flex items-center gap-2 min-w-0',
      el('span', 'w-6 h-6 rounded-chip bg-surface-2 border border-border text-accent flex items-center justify-center shrink-0', svg(icon, 'w-3.5 h-3.5')),
      el('span', 'text-[11px] font-mono uppercase tracking-[0.06em] text-muted truncate', v.title || 'Well-Well'),
      producing === null ? null : el('span', `inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border ${producing ? 'bg-success/10 border-success/30 text-success' : 'bg-danger/10 border-danger/30 text-[#F0787B]'}`,
        el('span', `w-1.5 h-1.5 rounded-full ${producing ? 'bg-success' : 'bg-danger'}`), producing ? 'Producing' : 'Not producing'),
      msg.blocked ? badge('held back', 'warning') : null,
      learnedBadge(msg)),
    meta ? el('span', 'font-mono text-[10px] text-faint', meta) : null);
}

function assistantNode(msg) {
  const node = el('div', 'msg-in flex flex-col gap-3 w-full min-w-0');
  node.append(answerHeader(msg));
  const text = renderMarkdown(msg.answer ?? msg.content, msg.citations);
  try { markLead(text); emphasiseNumbers(text); } catch { /* plain text is fine */ }
  node.append(text);
  const visual = renderVisual(msg.visual);
  if (visual) node.append(visual);
  const ev = evidenceRow(msg.citations);
  if (ev) node.append(ev);
  const how = howAnswered(msg);
  if (how) node.append(how);
  const rating = ratingBar(msg);
  if (rating) node.append(rating);
  return node;
}

function userNode(text) {
  return el('div', 'msg-in flex justify-end items-start gap-2.5',
    el('div', 'bg-surface-2 border border-border rounded-2xl px-5 py-3 max-w-[680px] text-[13px] leading-relaxed whitespace-pre-wrap break-words', text),
    avatar(state.user));
}

// ---------- thread ----------

function showThread(show) {
  $('empty-state').classList.toggle('hidden', show);
  $('thread').classList.toggle('hidden', !show);
}
function scrollDown() {
  const s = $('thread-scroll');
  s.scrollTop = s.scrollHeight;
}
function appendToThread(node) {
  showThread(true);
  $('thread').append(node);
  runMountHooks();
}

function setBusy(busy) {
  state.busy = busy;
  $('send').disabled = busy;
  $('question').disabled = busy;
  $('send-spinner').classList.toggle('hidden', !busy);
  $('send-icon').classList.toggle('hidden', busy);
  $('send').setAttribute('aria-busy', String(busy));
  $('send').setAttribute('aria-label', busy ? 'Working on your question' : 'Send');
  document.querySelectorAll('[data-example]').forEach((b) => { b.disabled = busy; });
}

// Anything that changes what the thread shows cancels the request that was filling it.
function resetThreadWork() {
  state.epoch++;
  state.askController?.abort();
  state.askController = null;
  state.convController?.abort();
  state.convController = null;
  setBusy(false);
}

const SLOW_AFTER_MS = 45000;

function progressNode(onStop) {
  const list = el('ol', 'space-y-1.5 text-[12px]');
  list.setAttribute('aria-live', 'polite');
  const timer = el('span', 'font-mono text-[11px] text-faint', '0 s');
  const stop = el('button', 'ml-auto px-2.5 py-1 rounded-chip border border-border text-[11px] text-muted hover:text-text hover:border-[#3A4452] transition-colors', 'Stop');
  stop.type = 'button';
  stop.addEventListener('click', onStop);
  const thinking = el('span', 'thinking inline-flex items-center gap-1', el('span', ''), el('span', ''), el('span', ''));
  thinking.setAttribute('aria-hidden', 'true');
  const head = el('div', 'flex items-center gap-2 text-[12px] text-muted', thinking, el('span', '', 'Working on it — answers take up to a minute'), timer, stop);
  const slow = el('div', 'hidden text-[12px] text-warning', 'Still working — the model is slow right now.');
  slow.setAttribute('aria-live', 'polite');
  const spinner = () => el('span', 'spin text-accent !w-3.5 !h-3.5');
  list.append(el('li', 'flex items-center gap-2 text-text', spinner(), 'Reading your question'));
  const started = Date.now();
  const tick = setInterval(() => {
    const ms = Date.now() - started;
    timer.textContent = `${Math.round(ms / 1000)} s`;
    if (ms >= SLOW_AFTER_MS) slow.classList.remove('hidden');
  }, 1000);
  const node = el('div', 'bg-surface border border-border rounded-card p-4 space-y-3', head, list, slow);
  node.setAttribute('aria-busy', 'true');
  return {
    node,
    step(text) {
      const { label, detail } = parseStep(text);
      const last = list.lastElementChild;
      if (last) {
        last.firstChild.replaceWith(el('span', 'text-success tick-in', svg(ICONS.check)));
        last.classList.replace('text-text', 'text-muted');
      }
      list.append(el('li', 'flex items-center gap-2 text-text', spinner(), el('span', '', label),
        detail ? el('span', 'font-mono text-[11px] text-faint truncate', detail) : null));
      scrollDown();
    },
    stop() { clearInterval(tick); },
  };
}

function errorNode(message, onRetry) {
  const retry = onRetry ? el('button', 'mt-2 px-2.5 py-1 rounded-chip border border-border text-[11px] text-accent hover:border-accent transition-colors', 'Retry') : null;
  if (retry) {
    retry.type = 'button';
    retry.addEventListener('click', onRetry);
  }
  const node = el('div', 'border border-border border-l-2 border-l-warning bg-surface rounded-card px-4 py-3 text-[12px] flex items-start gap-2',
    el('span', 'text-warning mt-0.5', svg(ICONS.warn)),
    el('div', '', el('div', 'text-text', 'No answer this time.'), el('div', 'text-muted', message || 'Something went wrong.'),
      onRetry ? el('div', 'text-muted mt-1', 'Your question is back in the box below.') : null, retry));
  node.setAttribute('role', 'alert');
  return node;
}

function stoppedNode() {
  return el('div', 'text-[12px] text-muted font-mono pl-1', 'Stopped.');
}

async function ask(question, previous = []) {
  const text = question.trim();
  if (!text || state.busy || !state.user) return;
  previous.forEach((n) => n.remove());
  const epoch = state.epoch;
  const controller = new AbortController();
  state.askController = controller;
  let stoppedByUser = false;
  setBusy(true);
  $('question').value = '';
  autosize();
  const asked = userNode(text);
  appendToThread(asked);
  const progress = progressNode(() => { stoppedByUser = true; controller.abort(); });
  appendToThread(progress.node);
  scrollDown();
  let answered = false;
  const fail = (message) => {
    if (epoch !== state.epoch) return;
    const err = errorNode(message, () => {
      $('question').value = '';
      ask(text, [asked, err]);
    });
    progress.node.replaceWith(err);
    $('question').value = text;
    autosize();
  };
  try {
    const res = await fetch('/api/ask', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
      body: JSON.stringify({ user_id: state.user.user_id, question: text, conversation_id: state.conversationId, search_mode: effectiveSearchMode(), use_playbook: learningOn }),
    });
    if (!res.ok || !res.body) {
      let msg = `The server answered ${res.status}.`;
      try { msg = (await res.json()).error || msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const handle = (line) => {
      if (!line.trim() || answered) return;
      let event;
      try { event = JSON.parse(line); } catch { return; }
      if (epoch !== state.epoch) return;
      if (event.type === 'step') progress.step(event.text);
      else if (event.type === 'error') { answered = true; fail(event.message); }
      else if (event.type === 'answer') {
        answered = true;
        if (event.conversation_id) state.conversationId = event.conversation_id;
        progress.node.replaceWith(assistantNode(event));
        runMountHooks();
        loadConversations({ quiet: true });
        loadMemoryCounts();
      }
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      lines.forEach(handle);
    }
    handle(buffer);
    if (!answered) fail('The connection closed before an answer arrived.');
  } catch (err) {
    if (isAbort(err)) {
      if (stoppedByUser && epoch === state.epoch) progress.node.replaceWith(stoppedNode());
    } else if (!answered) {
      fail(err.message === 'Failed to fetch' || err.message?.includes('network') ? 'The connection to the server broke.' : err.message || 'Could not reach the server.');
    }
  } finally {
    progress.stop();
    if (state.askController === controller) state.askController = null;
    if (epoch === state.epoch) {
      setBusy(false);
      $('question').focus();
    }
  }
}

function threadSkeleton() {
  return busyBox('flex flex-col gap-7', 'Loading conversation',
    el('div', 'flex justify-end', skel('h-11 w-[46%] rounded-2xl')),
    el('div', 'flex flex-col gap-3',
      skel('h-3 w-20'), skel('h-3 w-[92%]'), skel('h-3 w-[78%]'), skel('h-3 w-[60%]'),
      skel('h-[320px] w-full rounded-card'),
      el('div', 'flex gap-1.5', skel('h-5 w-28'), skel('h-5 w-28'), skel('h-5 w-24'))));
}

async function openConversation(id) {
  if (!state.user) return;
  resetThreadWork();
  const epoch = state.epoch;
  const controller = new AbortController();
  state.convController = controller;
  state.conversationId = id;
  renderConversationList();
  $('thread').setAttribute('aria-busy', 'true');
  $('thread').replaceChildren(threadSkeleton());
  showThread(true);
  try {
    const msgs = await api('/api/conversations/messages', { user_id: state.user.user_id, conversation_id: id }, controller.signal);
    if (epoch !== state.epoch) return;
    $('thread').replaceChildren();
    if (!msgs.length) { showThread(false); return; }
    for (const m of msgs) appendToThread(m.role === 'user' ? userNode(m.content) : assistantNode(m));
    scrollDown();
  } catch (err) {
    if (isAbort(err) || epoch !== state.epoch) return;
    $('thread').replaceChildren(retryBlock(`Couldn't open this conversation. ${err.message}`, () => openConversation(id), 'border border-border bg-surface rounded-card px-4 py-3'));
  } finally {
    if (epoch === state.epoch) $('thread').removeAttribute('aria-busy');
    if (state.convController === controller) state.convController = null;
  }
}

function newChat() {
  resetThreadWork();
  state.conversationId = null;
  $('thread').replaceChildren();
  showThread(false);
  renderConversationList();
  $('question').focus();
}

// ---------- sidebar ----------

function roleLine(u) {
  return `${sentence(u.role || '')} · ${u.team || ''}`;
}

function renderUserPicker() {
  const u = state.user;
  paintAvatar($('user-initials'), u);
  paintAvatar($('mobile-avatar'), u);
  $('user-name').textContent = u?.name || 'Pick a user';
  $('user-role').textContent = u ? roleLine(u) : '';
  const list = $('user-list');
  list.replaceChildren(...state.users.map((x) => {
    const b = el('button', `w-full text-left px-2.5 py-1.5 flex items-center gap-2 hover:bg-surface-2 transition-colors ${x.user_id === u?.user_id ? 'bg-surface-2' : ''}`,
      avatar(x, 'w-6 h-6 text-[9px]'),
      el('span', 'min-w-0', el('div', `text-[11px] font-medium truncate ${x.user_id === u?.user_id ? 'text-accent' : ''}`, x.name), el('div', 'text-[10px] text-muted truncate', roleLine(x))));
    b.type = 'button';
    b.setAttribute('role', 'option');
    b.addEventListener('click', () => { toggleUserList(false); selectUser(x.user_id); closeNav(); });
    return b;
  }));
}

function toggleUserList(open) {
  const list = $('user-list');
  const show = open ?? list.classList.contains('hidden');
  list.classList.toggle('hidden', !show);
  $('user-trigger').setAttribute('aria-expanded', String(show));
}

let conversations = [];
let conversationsState = 'idle';
let listController = null;

function renderConversationList() {
  const box = $('conversation-list');
  box.removeAttribute('aria-busy');
  if (conversationsState === 'loading') {
    box.setAttribute('aria-busy', 'true');
    box.replaceChildren(convSkeleton());
    return;
  }
  if (conversationsState === 'error') {
    box.replaceChildren(retryBlock("Couldn't load conversations.", () => loadConversations(), 'px-2 py-1.5 !text-[11px]'));
    return;
  }
  if (!conversations.length) {
    box.replaceChildren(el('div', 'px-2 py-1.5 text-[11px] text-faint', 'No conversations yet'));
    return;
  }
  box.replaceChildren(...conversations.map((c) => {
    const active = c.conversation_id === state.conversationId;
    const b = el('button', `w-full text-left px-2 py-1.5 rounded-chip transition-colors group border-l-2 ${active ? 'bg-surface-2 border-accent' : 'border-transparent hover:bg-surface-2'}`,
      el('div', `text-[11px] font-medium truncate ${active ? 'text-text' : 'group-hover:text-accent'}`, c.title || 'New chat'),
      el('div', `text-[10px] font-mono ${active ? 'text-accent' : 'text-muted'}`, relDate(c.started_on)));
    b.type = 'button';
    b.title = c.title || 'New chat';
    if (active) b.setAttribute('aria-current', 'true');
    b.addEventListener('click', () => openConversation(c.conversation_id));
    return b;
  }));
}

// quiet: refresh in place after an answer, without flashing skeleton rows.
async function loadConversations({ quiet = false } = {}) {
  if (!state.user) return;
  const userId = state.user.user_id;
  listController?.abort();
  const controller = new AbortController();
  listController = controller;
  if (!quiet || conversationsState !== 'ready') {
    conversationsState = 'loading';
    renderConversationList();
  }
  try {
    const list = await api('/api/conversations/list', { user_id: userId }, controller.signal);
    if (controller !== listController || state.user?.user_id !== userId) return;
    conversations = Array.isArray(list) ? list : [];
    conversationsState = 'ready';
  } catch (err) {
    if (isAbort(err) || controller !== listController) return;
    if (!quiet || conversationsState !== 'ready') conversationsState = 'error';
  }
  renderConversationList();
}

function memoryTotals(m) {
  return {
    shared: (m.shared_notes?.length || 0) + (m.claims?.length || 0) + (m.rules?.length || 0),
    private: m.private_conversations?.length || 0,
    corrections: m.corrections?.length || 0,
  };
}

let memoryController = null;
async function loadMemory() {
  const userId = state.user?.user_id;
  if (!userId) return null;
  memoryController?.abort();
  const controller = new AbortController();
  memoryController = controller;
  state.memoryState = 'loading';
  state.memoryError = '';
  renderMemoryCounts();
  if (drawerOpen()) renderMemory();
  try {
    const m = await api('/api/memory', { user_id: userId }, controller.signal);
    if (controller !== memoryController || state.user?.user_id !== userId) return null;
    state.memory = m;
    state.memoryState = 'ready';
  } catch (err) {
    if (isAbort(err) || controller !== memoryController) return null;
    state.memoryState = 'error';
    state.memoryError = err.message;
  }
  renderMemoryCounts();
  if (drawerOpen()) renderMemory();
  return state.memory;
}

function renderMemoryCounts() {
  const box = $('memory-counts');
  box.removeAttribute('aria-busy');
  if (state.memoryState === 'loading' && !state.memory) {
    box.setAttribute('aria-busy', 'true');
    box.replaceChildren(el('span', 'inline-flex gap-2 pt-0.5', skel('h-2.5 w-14'), skel('h-2.5 w-14'), skel('h-2.5 w-20')));
    return;
  }
  if (state.memoryState === 'error' || !state.memory) {
    box.textContent = 'Memory unavailable';
    return;
  }
  const t = memoryTotals(state.memory);
  box.replaceChildren(...[['Shared', t.shared], ['Private', t.private], ['Corrections', t.corrections]].map(([k, n], i) =>
    el('span', `inline-flex items-center gap-1 ${i ? 'ml-2' : ''}`, k, el('span', `px-1 rounded-chip border border-border ${n ? 'text-accent' : 'text-faint'}`, String(n)))));
}

function loadMemoryCounts() {
  loadMemory().catch(() => {});
}

function selectUser(userId) {
  const u = state.users.find((x) => x.user_id === userId) || state.users[0];
  if (!u) return;
  state.user = u;
  state.memory = null;
  store('wellwell.user', u.user_id);
  conversations = [];
  conversationsState = 'loading';
  renderUserPicker();
  $('empty-caption').textContent = u.team || '';
  newChat();
  loadConversations();
  loadMemoryCounts();
}

function renderExamples(examples) {
  const box = $('examples');
  box.className = 'w-full grid grid-cols-1 md:grid-cols-2 gap-3 text-left';
  box.replaceChildren(...examples.map((ex) => {
    const b = el('button', 'lift bg-surface border border-border rounded-card px-3.5 py-3 min-h-[64px] text-left text-[13px] leading-snug hover:text-accent disabled:opacity-50', ex.question);
    b.type = 'button';
    b.dataset.example = '1';
    b.addEventListener('click', () => ask(ex.question));
    return b;
  }));
}

// ---------- field overview ----------

const TONE_HEX = { success: '#3DB57A', danger: '#E5484D', warning: '#F2A93B' };
let overviewController = null;

function overviewSkeleton() {
  return busyBox('space-y-4', 'Loading the field overview',
    el('div', 'grid grid-cols-2 md:grid-cols-4 gap-3', Array.from({ length: 4 }, () => el('div', 'bg-surface border border-border rounded-card p-3.5 h-[96px] space-y-2.5', skel('h-6 w-14'), skel('h-3 w-24'), skel('h-2.5 w-16')))),
    el('div', 'space-y-2', Array.from({ length: 4 }, () => el('div', 'flex items-center gap-3', skel('h-3 w-32'), skel('h-2 flex-1'), skel('h-3 w-12')))));
}

function renderOverview(ov) {
  $('overview-asof').textContent = ov.as_of ? `as of ${fmtDate(ov.as_of)}` : '';
  const tiles = (ov.tiles || []).slice(0, 4).map((t) => {
    const b = el('button', 'lift text-left bg-surface border border-border border-l-[3px] rounded-card p-3.5 min-h-[96px] flex flex-col gap-1 disabled:opacity-50',
      el('span', 'flex items-baseline gap-1', el('span', 'font-mono text-[26px] leading-none font-semibold', fmtNum(t.value)),
        t.of != null ? el('span', 'font-mono text-[12px] text-muted', `/${fmtNum(t.of)}`) : null),
      el('span', 'text-[12px] text-text mt-1', t.label),
      t.detail ? el('span', 'text-[11px] text-muted', t.detail) : null,
      t.question ? el('span', 'mt-auto pt-1 text-[11px] text-accent', 'Ask ›') : null);
    b.type = 'button';
    b.style.borderLeftColor = TONE_HEX[t.tone] || C.border;
    b.dataset.example = '1';
    if (t.question) {
      b.title = t.question;
      b.addEventListener('click', () => ask(t.question));
    }
    return b;
  });
  const areas = (ov.areas || []).filter((a) => a.wells > 0).map((a) => {
    const share = Math.max(0, Math.min(1, a.producing / a.wells));
    const green = el('span', 'h-full bg-success');
    green.style.width = `${share * 100}%`;
    const red = el('span', 'h-full bg-danger');
    red.style.width = `${(1 - share) * 100}%`;
    const question = `Which wells in ${a.area} are not producing right now, and why?`;
    const b = el('button', 'w-full grid grid-cols-[1fr_auto] sm:grid-cols-[150px_1fr_80px] items-center gap-x-3 gap-y-1.5 px-2 py-1.5 rounded-chip hover:bg-surface transition-colors text-left disabled:opacity-50',
      el('span', 'text-[12px] truncate', a.area),
      el('span', 'h-2 rounded-full overflow-hidden flex bg-surface-2 col-span-2 order-last sm:col-span-1 sm:order-none', green, red),
      el('span', 'font-mono text-[11px] text-muted text-right whitespace-nowrap', `${fmtNum(a.producing)}/${fmtNum(a.wells)}`, el('span', 'text-faint ml-1.5', '›')));
    b.type = 'button';
    b.title = question;
    b.dataset.example = '1';
    b.setAttribute('aria-label', `${a.area}: ${a.producing} of ${a.wells} wells producing. Ask why the others are not.`);
    b.addEventListener('click', () => ask(question));
    return b;
  });
  $('overview-body').replaceChildren(
    tiles.length ? el('div', 'grid grid-cols-2 md:grid-cols-4 gap-3', tiles) : '',
    areas.length ? el('div', 'mt-4 space-y-0.5 -mx-2', el('div', 'flex justify-end gap-3 text-[10px] font-mono text-faint mb-1',
      el('span', 'inline-flex items-center gap-1', el('span', 'w-2 h-2 rounded-full bg-success'), 'producing'),
      el('span', 'inline-flex items-center gap-1', el('span', 'w-2 h-2 rounded-full bg-danger'), 'not producing')), areas) : '');
}

async function loadOverview() {
  overviewController?.abort();
  const controller = new AbortController();
  overviewController = controller;
  const body = $('overview-body');
  $('overview').classList.remove('hidden');
  body.setAttribute('aria-busy', 'true');
  body.replaceChildren(overviewSkeleton());
  try {
    const ov = await api('/api/overview', {}, controller.signal);
    if (controller !== overviewController) return;
    body.removeAttribute('aria-busy');
    if (!ov?.tiles?.length && !ov?.areas?.length) { $('overview').classList.add('hidden'); return; }
    renderOverview(ov);
  } catch (err) {
    if (isAbort(err) || controller !== overviewController) return;
    body.removeAttribute('aria-busy');
    body.replaceChildren(retryBlock("Couldn't load the field overview.", () => loadOverview()));
  }
}

// ---------- memory drawer ----------

function statusTone(s) {
  const v = String(s || '').toLowerCase();
  if (/unconfirm|pending|contradict|disput/.test(v)) return 'warning';
  if (/retract|reject|undone|wrong/.test(v)) return 'danger';
  if (/confirm|active|applied|accepted/.test(v)) return 'success';
  return 'neutral';
}

function memItem(head, text, foot, tone) {
  const border = tone === 'warning' ? 'border-l-warning' : tone === 'success' ? 'border-l-success' : 'border-l-border';
  return el('div', `bg-surface-2 border border-border border-l-2 ${border} rounded-card p-3 space-y-2`,
    el('div', 'flex items-center justify-between gap-2 flex-wrap', head), text ? el('p', 'text-[12px] leading-relaxed', text) : null,
    foot ? el('div', 'flex items-center justify-between gap-2 flex-wrap pt-1 border-t border-border/60 text-[11px] text-muted', foot) : null);
}

function wellTag(id) {
  return el('span', 'font-mono text-[12px] font-semibold text-accent', id || 'All wells');
}

function memoryTabContent(tab, m) {
  const name = state.user?.name || 'you';
  if (tab === 'shared') {
    const items = [];
    for (const n of m.shared_notes || []) {
      items.push(memItem([wellTag(n.asset_id || (n.well_type ? `All ${n.well_type} wells` : null)), n.note_id ? chip(n.note_id) : null], `"${n.text}"`,
        [el('span', '', el('span', 'text-text', n.author || ''), ` · ${shortDate(n.created_at)}`), n.valid_from ? el('span', 'font-mono text-[10px]', `valid ${shortDate(n.valid_from)}–${n.valid_to ? shortDate(n.valid_to) : 'open'}`) : null]));
    }
    for (const c of m.claims || []) {
      items.push(memItem([wellTag(c.asset_id), el('span', 'flex items-center gap-1.5', c.claim_id ? chip(c.claim_id) : null, badge(c.status || 'unconfirmed', statusTone(c.status)))], `"${c.statement}"`,
        [el('span', '', el('span', 'text-text', c.author || ''), ` · ${shortDate(c.created_at)}`), c.claimed_state ? el('span', 'font-mono text-[10px]', `claims: ${humanize(c.claimed_state)}`) : null], statusTone(c.status)));
    }
    for (const r of m.rules || []) {
      items.push(memItem([el('span', 'flex items-center gap-2', el('span', 'font-mono text-[12px] font-semibold', `All ${r.well_type || ''} wells`), badge('team rule', 'neutral')), r.override_id ? chip(r.override_id) : null],
        r.statement ? `"${r.statement}"` : `${humanize(r.rule || '')}: ${r.value}${r.manual_value != null ? ` (manual: ${r.manual_value})` : ''}`,
        [el('span', '', el('span', 'text-text', r.author || ''), r.equipment_kind ? ` · ${humanize(r.equipment_kind)}` : ''), badge(r.status || 'active', statusTone(r.status || 'active'))]));
    }
    return { title: 'Shared knowledge', note: '(visible to everyone)', items, empty: 'Notes, statements and team rules that people share in chat appear here.' };
  }
  if (tab === 'private') {
    const items = (m.private_conversations || []).map((c) => {
      const b = el('button', 'w-full text-left', memItem([el('span', 'text-[12px] font-medium', c.title || 'New chat'), el('span', 'font-mono text-[10px] text-muted', relDate(c.started_on))], null,
        c.wells?.length ? [el('span', 'flex flex-wrap gap-1', c.wells.map((w) => el('span', 'font-mono text-[11px] text-accent', w)))] : null));
      b.type = 'button';
      b.addEventListener('click', () => { closeMemory(); openConversation(c.conversation_id); });
      return b;
    });
    return { title: 'My private memory', note: `(only ${name})`, items, empty: 'Your own conversations, and the wells they mention, appear here.' };
  }
  const items = (m.corrections || []).map((c) => memItem(
    [el('span', 'flex items-center gap-2', c.correction_id ? chip(c.correction_id) : null, el('span', 'font-mono text-[11px]', [c.target_id, c.field ? humanize(c.field) : null].filter(Boolean).join(' · '))), badge(c.status || 'applied', statusTone(c.status || 'applied'))],
    el('span', 'font-mono', el('span', 'line-through text-[#F0787B]', String(c.original_value ?? '—')), ' → ', el('span', 'text-success font-semibold', String(c.corrected_value ?? '—'))),
    [el('span', '', 'corrected by ', el('span', 'text-text', c.author || 'someone'), c.reason ? ` — ${c.reason}` : ''), el('span', 'font-mono text-[10px]', shortDate(c.created_at))], 'success'));
  return { title: 'Corrections log', note: '(applied for everyone)', items, empty: 'Corrections people make to readings or analyses in chat appear here.', extra: m.dataset_corrections ? `${m.dataset_corrections} corrections from the dataset are also applied.` : null };
}

const MEMORY_TABS = [['shared', 'Shared knowledge'], ['private', 'My private memory'], ['corrections', 'Corrections log'], ['playbook', 'Playbook']];

function drawerOpen() {
  return !$('memory-drawer').classList.contains('hidden');
}

function memorySkeleton() {
  return busyBox('space-y-3', 'Loading memory', Array.from({ length: 3 }, () => el('div', 'bg-surface-2 border border-border rounded-card p-3 space-y-2.5',
    el('div', 'flex justify-between', skel('h-3 w-24 !bg-border'), skel('h-3 w-16 !bg-border')),
    skel('h-3 w-[90%] !bg-border'), skel('h-3 w-[70%] !bg-border'),
    el('div', 'flex justify-between pt-1', skel('h-2.5 w-32 !bg-border'), skel('h-2.5 w-20 !bg-border')))));
}

function renderMemory() {
  const m = state.memory;
  const body = $('memory-body');
  const totals = m ? memoryTotals(m) : null;
  $('memory-tabs').replaceChildren(...MEMORY_TABS.map(([id, label]) => {
    const active = state.memoryTab === id;
    const n = id === 'playbook' ? (playbookState.data ? (playbookState.data.patterns || []).length : null) : totals?.[id];
    const count = n != null ? el('span', `px-1.5 rounded-chip bg-surface-2 text-[10px] font-mono border border-border ${active ? 'text-accent' : 'text-slate'}`, String(n))
      : skel('h-3.5 w-5');
    const b = el('button', `py-2.5 text-xs flex items-center gap-1.5 shrink-0 border-b-2 transition-colors ${active ? 'font-semibold text-accent border-accent' : 'font-medium text-muted hover:text-text border-transparent'}`, label, count);
    b.type = 'button';
    b.setAttribute('aria-selected', String(active));
    b.addEventListener('click', () => { state.memoryTab = id; renderMemory(); });
    return b;
  }));
  body.removeAttribute('aria-busy');
  if (state.memoryTab === 'playbook') {
    renderPlaybookTab(body);
    return;
  }
  if (state.memoryState === 'error') {
    body.replaceChildren(retryBlock(`Couldn't load memory. ${state.memoryError || ''}`.trim(), () => loadMemory()));
    return;
  }
  if (!m || state.memoryState === 'loading') {
    body.setAttribute('aria-busy', 'true');
    body.replaceChildren(memorySkeleton());
    return;
  }
  const content = memoryTabContent(state.memoryTab, m);
  const q = $('memory-search').value.trim().toLowerCase();
  const items = q ? content.items.filter((n) => n.textContent.toLowerCase().includes(q)) : content.items;
  const empty = q
    ? el('div', 'text-[12px] text-muted py-4', 'Nothing matches that search.')
    : el('div', 'py-6 text-center space-y-1', el('div', 'text-[13px] font-medium', 'Nothing here yet'), el('div', 'text-[12px] text-muted', content.empty));
  body.replaceChildren(
    el('div', 'flex items-center gap-1.5', el('span', 'text-xs font-semibold uppercase tracking-wider font-mono', content.title), el('span', 'text-[11px] text-muted', content.note)),
    content.extra ? el('div', 'text-[11px] font-mono text-muted', content.extra) : '',
    ...(items.length ? items : [empty]));
}

function renderPlaybookTab(body) {
  const intro = el('p', 'text-[12px] text-muted leading-relaxed', 'Well-Well learns which tool plans answer which kinds of question. Ratings and verified answers make a plan more trusted; downvotes mark it to avoid.');
  const head = el('div', 'flex items-center gap-1.5', el('span', 'text-xs font-semibold uppercase tracking-wider font-mono', 'Playbook'), el('span', 'text-[11px] text-muted', '(shared by everyone)'));
  if (playbookState.status === 'error') {
    body.replaceChildren(head, intro, retryBlock(`Couldn't load the playbook. ${playbookState.error}`, () => loadPlaybook()));
    return;
  }
  if (!playbookState.data) {
    body.setAttribute('aria-busy', 'true');
    body.replaceChildren(head, intro, memorySkeleton());
    return;
  }
  const q = $('memory-search').value.trim().toLowerCase();
  let items = playbookItems(playbookState.data);
  if (q) items = items.filter((n) => n.textContent.toLowerCase().includes(q));
  const off = playbookState.data.enabled === false ? el('div', 'text-[12px] text-warning', 'Learning is turned off on the server.') : '';
  const empty = q ? el('div', 'text-[12px] text-muted py-4', 'Nothing matches that search.')
    : el('div', 'py-6 text-center space-y-1', el('div', 'text-[13px] font-medium', 'Nothing here yet'), el('div', 'text-[12px] text-muted', 'Plans appear here after questions are answered and rated.'));
  body.replaceChildren(head, intro, off, ...(items.length ? items : [empty]));
}

function openMemory() {
  $('memory-scrim').classList.remove('hidden');
  $('memory-drawer').classList.remove('hidden');
  $('memory-drawer').classList.add('flex');
  state.memory = null;
  renderMemory();
  loadMemory();
  loadPlaybook();
}
function closeMemory() {
  $('memory-scrim').classList.add('hidden');
  $('memory-drawer').classList.add('hidden');
  $('memory-drawer').classList.remove('flex');
}

// ---------- mobile navigation drawer ----------

const narrow = () => window.matchMedia('(max-width: 899px)').matches;
let navReturnFocus = null;

function openNav() {
  if (!narrow()) return;
  navReturnFocus = document.activeElement;
  document.body.classList.add('nav-open');
  $('nav-scrim').classList.remove('hidden');
  $('main').inert = true;
  $('nav-open').setAttribute('aria-expanded', 'true');
  const first = $('sidebar').querySelector('button:not([disabled])');
  setTimeout(() => first?.focus(), 50);
}
function closeNav() {
  if (!document.body.classList.contains('nav-open')) return;
  document.body.classList.remove('nav-open');
  $('nav-scrim').classList.add('hidden');
  $('main').inert = false;
  $('nav-open').setAttribute('aria-expanded', 'false');
  navReturnFocus?.focus?.();
}
// Keep Tab inside the drawer while it is open.
function trapNavFocus(e) {
  if (e.key !== 'Tab' || !document.body.classList.contains('nav-open')) return;
  const items = [...$('sidebar').querySelectorAll('button:not([disabled]), [href], input, textarea, summary')].filter((n) => n.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

// ---------- model names ----------

const MODEL_NAMES = {
  'gemini-3.5-flash-lite': 'Gemini 3.5 Flash-Lite',
  'gemini-3.1-flash-lite': 'Gemini 3.1 Flash-Lite',
  'gemma-4-26b-a4b-it': 'Gemma 4 26B',
  'gemma-4-31b-it': 'Gemma 4 31B',
};
function modelName(id) {
  if (!id) return '';
  const key = String(id).replace(/^models\//, '');
  return MODEL_NAMES[key] || String(id);
}
function setModelChip(tone, word) {
  const name = modelName(state.boot?.model) || 'Model';
  $('model-dot').className = `w-1.5 h-1.5 rounded-full ${tone}`;
  $('model-text').textContent = `${name} · ${word}`;
}

// ---------- search mode ----------

const SEARCH_MODES = [
  ['hybrid', 'Smart', 'Meaning + exact words'],
  ['semantic', 'Semantic', 'Finds passages by meaning, using BGE-large (default)'],
  ['keyword', 'Exact words', 'Matches IDs and terms like SR-02 or §10'],
];
const SEARCH_KEY = 'ww.searchMode.v2';
const search = { mode: 'semantic', semanticAvailable: true, fromBootstrap: true };

function effectiveSearchMode() {
  return search.semanticAvailable ? search.mode : 'keyword';
}

function renderSearchModes() {
  const current = effectiveSearchMode();
  $('search-modes').replaceChildren(...SEARCH_MODES.map(([id, label, hint], i) => {
    const off = id !== 'keyword' && !search.semanticAvailable;
    const on = id === current;
    const b = el('button', `px-2 py-0.5 text-[11px] whitespace-nowrap transition-colors ${i ? 'border-l border-border' : ''} ${on ? 'bg-surface-2 text-accent' : 'text-muted hover:text-text'} disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-muted`, label);
    b.type = 'button';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(on));
    b.disabled = off;
    b.title = off ? 'Semantic search is off' : hint;
    b.addEventListener('click', () => {
      search.mode = id;
      store(SEARCH_KEY, id);
      renderSearchModes();
    });
    return b;
  }));
}

const LEARNING_KEY = 'ww.learning.v1';
let learningOn = true;
function renderLearning() {
  $('learning-modes').replaceChildren(...[[true, 'On'], [false, 'Off']].map(([val, label], i) => {
    const on = learningOn === val;
    const b = el('button', `px-2 py-0.5 text-[11px] whitespace-nowrap transition-colors ${i ? 'border-l border-border' : ''} ${on ? 'bg-surface-2 text-accent' : 'text-muted hover:text-text'}`, label);
    b.type = 'button';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(on));
    b.title = val ? 'Use plans learned from earlier rated answers' : 'Answer without learned plans';
    b.addEventListener('click', () => { learningOn = val; store(LEARNING_KEY, val ? 'on' : 'off'); renderLearning(); });
    return b;
  }));
}
function initLearning() {
  learningOn = recall(LEARNING_KEY) !== 'off';
  renderLearning();
}

function initSearch(cfg) {
  const modes = SEARCH_MODES.map(([id]) => id);
  const saved = recall(SEARCH_KEY);
  search.mode = modes.includes(saved) ? saved : modes.includes(cfg?.default) ? cfg.default : 'semantic';
  search.fromBootstrap = cfg?.semantic_available !== false;
  search.semanticAvailable = search.fromBootstrap;
  renderSearchModes();
}

// Bootstrap may say semantic search is off only because the vectors are still indexing.
function syncSearchAvailability(st) {
  const steps = Array.isArray(st.steps) ? st.steps : [];
  const vectors = steps.find((s) => s.key === 'vectors');
  const model = steps.find((s) => s.key === 'model');
  if (!vectors && !model) return;
  const ready = (!vectors || vectors.status === 'done') && (!model || model.status === 'done');
  if (ready !== search.semanticAvailable) {
    search.semanticAvailable = ready;
    renderSearchModes();
  }
}

// ---------- composer ----------

function autosize() {
  const q = $('question');
  q.style.height = 'auto';
  q.style.height = `${Math.min(q.scrollHeight, 160)}px`;
}

function wire() {
  if (window.matchMedia('(max-width: 520px)').matches) $('question').rows = 1;
  $('composer').addEventListener('submit', (e) => { e.preventDefault(); ask($('question').value); });
  $('question').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask($('question').value); }
  });
  $('question').addEventListener('input', autosize);
  $('new-chat').addEventListener('click', () => { newChat(); closeNav(); });
  $('nav-open').addEventListener('click', openNav);
  $('nav-scrim').addEventListener('click', closeNav);
  document.addEventListener('keydown', trapNavFocus);
  window.matchMedia('(max-width: 899px)').addEventListener('change', (e) => { if (!e.matches) closeNav(); });
  $('conversation-list').addEventListener('click', (e) => { if (e.target.closest('button')) closeNav(); });
  $('memory-open').addEventListener('click', closeNav);
  $('user-trigger').addEventListener('click', (e) => { e.stopPropagation(); toggleUserList(); });
  document.addEventListener('click', (e) => { if (!$('user-list').contains(e.target)) toggleUserList(false); });
  $('memory-open').addEventListener('click', openMemory);
  $('memory-close').addEventListener('click', closeMemory);
  $('memory-scrim').addEventListener('click', closeMemory);
  $('memory-search').addEventListener('input', renderMemory);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    closeNav();
    closeMemory();
    toggleUserList(false);
    recordPanel?.hide();
  });
}

function bootSkeleton(on) {
  $('user-trigger').setAttribute('aria-busy', String(on));
  $('user-trigger').disabled = on;
  $('new-chat').disabled = on;
  $('memory-open').disabled = on;
  if (on) {
    $('user-initials').replaceChildren(skel('w-7 h-7'));
    $('user-name').replaceChildren(skel('h-3 w-28 my-0.5'));
    $('user-role').replaceChildren(skel('h-2.5 w-40 mt-1'));
    conversationsState = 'loading';
    renderConversationList();
    $('memory-counts').replaceChildren(el('span', 'inline-flex gap-2 pt-0.5', skel('h-2.5 w-14'), skel('h-2.5 w-14'), skel('h-2.5 w-20')));
    if (!$('fleet-line').textContent) $('fleet-line').replaceChildren(skel('h-2.5 w-44'));
    $('empty-caption').replaceChildren(skel('h-3 w-48 mx-auto'));
    $('overview-body').replaceChildren(overviewSkeleton());
    const box = $('examples');
    box.className = 'w-full grid grid-cols-1 md:grid-cols-2 gap-3 text-left';
    box.setAttribute('aria-busy', 'true');
    box.replaceChildren(...Array.from({ length: 6 }, () => el('div', 'bg-surface border border-border rounded-card p-3.5 min-h-[72px] space-y-2',
      skel('h-3 w-[90%]'), skel('h-3 w-[55%]'))));
  } else {
    $('examples').removeAttribute('aria-busy');
    $('empty-caption').replaceChildren();
  }
}

function showBootError() {
  const retry = el('button', 'px-3 py-1.5 rounded-chip border border-accent text-accent text-[12px] font-medium hover:bg-accent/10 transition-colors', 'Retry');
  retry.type = 'button';
  retry.addEventListener('click', () => {
    $('boot-error').classList.add('hidden');
    $('boot-error').classList.remove('flex');
    $('empty-state').classList.remove('hidden');
    boot();
  });
  $('boot-error').replaceChildren(
    el('span', 'text-warning', svg(ICONS.warn, 'w-5 h-5')),
    el('div', 'text-[15px] font-medium', "Couldn't reach Well-Well. Check that the server is running."),
    retry);
  $('empty-state').classList.add('hidden');
  $('thread').classList.add('hidden');
  $('boot-error').classList.remove('hidden');
  $('boot-error').classList.add('flex');
  $('user-initials').textContent = '··';
  $('user-name').textContent = 'Not connected';
  $('user-role').textContent = '';
  $('conversation-list').replaceChildren();
  $('conversation-list').removeAttribute('aria-busy');
  $('memory-counts').textContent = '';
  $('fleet-line').textContent = '';
  setModelChip('bg-danger', 'offline');
}

// ---------- dataset loading ----------

const BLOCKING = new Set(['initialising', 'empty', 'loading', 'failed']);
const UNFINISHED = new Set(['pending', 'running']);
const data = { status: null, timer: null, dismissed: false, dismissible: false, starting: false, everReached: false, kind: null };

function renderFleet(stats, fleet) {
  const line = $('fleet-line');
  if (stats && stats.wells != null) {
    const parts = [`${fmtNum(stats.wells)} wells`, `${fmtNum(stats.areas ?? 0)} areas`];
    if (stats.inspections != null) parts.push(`${fmtNum(stats.inspections)} inspections`);
    if (stats.repairs != null) parts.push(`${fmtNum(stats.repairs)} repairs`);
    if (stats.regulator_events != null) parts.push(`${fmtNum(stats.regulator_events)} regulator events`);
    if (stats.first_inspection && stats.last_inspection) parts.push(`data ${fmtDate(stats.first_inspection)} – ${fmtDate(stats.last_inspection)}`);
    line.textContent = parts.join(' · ');
  } else if (fleet) {
    line.textContent = `${fleet.wells ?? '?'} wells · ${fleet.areas ?? '?'} areas · data to ${fmtDate(fleet.data_to)}`;
  }
}

function progressOf(st) {
  const run = st.run || {};
  const total = Number(run.wells_total) || 0;
  const done = Number(run.wells_done) || 0;
  let pct = typeof st.percent === 'number' ? st.percent : total ? (done / total) * 100 : null;
  if (pct != null) pct = Math.max(0, Math.min(100, Math.round(pct)));
  return { run, total, done, pct, name: run.dataset || '' };
}

// What the app should do with a status: block, run in the background, or nothing.
function classify(st) {
  const steps = Array.isArray(st.steps) ? st.steps : [];
  const blockingOpen = steps.some((s) => s.blocking && s.status !== 'done' && s.status !== 'skipped');
  if (BLOCKING.has(st.state) || blockingOpen) return 'blocking';
  if (st.state === 'updating') return 'updating';
  if (steps.some((s) => !s.blocking && UNFINISHED.has(s.status))) return 'indexing';
  return 'ready';
}

function indexingProgress(st) {
  const open = (st.steps || []).filter((s) => !s.blocking && UNFINISHED.has(s.status));
  const withPct = open.find((s) => typeof s.percent === 'number');
  return withPct ? Math.round(withPct.percent) : null;
}

function dialogButton(label, primary, onClick) {
  const b = el('button', primary
    ? 'px-3 py-1.5 rounded-chip bg-accent text-bg text-[12px] font-medium hover:bg-[#3A8BD6] transition-colors'
    : 'px-3 py-1.5 rounded-chip border border-border text-[12px] text-text hover:border-[#3A4452] transition-colors', label);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

const stepSeen = {};
function stepIcon(status, fresh) {
  const box = 'w-4 h-4 shrink-0 flex items-center justify-center mt-0.5';
  if (status === 'done') return el('span', `${box} text-success ${fresh ? 'tick-in' : ''}`, svg(ICONS.check, 'w-4 h-4'));
  if (status === 'failed') return el('span', `${box} text-[#F0787B]`, svg(ICONS.close, 'w-4 h-4'));
  if (status === 'running') return el('span', box, el('span', 'spin text-accent !w-3.5 !h-3.5'));
  if (status === 'skipped') return el('span', `${box} text-faint font-mono`, '–');
  return el('span', box, el('span', 'w-3 h-3 rounded-full border-2 border-faint inline-block'));
}

function renderSteps(steps) {
  const list = $('data-steps');
  list.classList.toggle('hidden', !steps.length);
  list.replaceChildren(...steps.map((s) => {
    const pct = typeof s.percent === 'number' ? Math.max(0, Math.min(100, Math.round(s.percent))) : null;
    const bar = s.status === 'running' && pct != null ? (() => {
      const fill = el('div', 'h-full bg-accent rounded-full transition-[width] duration-500');
      fill.style.width = `${pct}%`;
      const wrap = el('div', 'h-1 w-full bg-surface-2 rounded-full overflow-hidden mt-1.5', fill);
      wrap.setAttribute('role', 'progressbar');
      wrap.setAttribute('aria-valuemin', '0');
      wrap.setAttribute('aria-valuemax', '100');
      wrap.setAttribute('aria-valuenow', String(pct));
      wrap.setAttribute('aria-label', s.label || s.key);
      return wrap;
    })() : null;
    const tone = s.status === 'pending' ? 'text-muted' : s.status === 'failed' ? 'text-[#F0787B]' : 'text-text';
    const key = s.key || s.label;
    const fresh = stepSeen[key] !== undefined && stepSeen[key] !== s.status;
    stepSeen[key] = s.status;
    return el('li', 'flex items-start gap-2.5', stepIcon(s.status, fresh),
      el('div', 'min-w-0 flex-1',
        el('div', `text-[13px] ${tone}`, s.label || sentence(s.key || ''), s.blocking === false && s.status !== 'done' ? el('span', 'text-[11px] text-faint ml-1.5', 'optional') : null),
        s.detail ? el('div', `text-[12px] ${s.status === 'failed' ? 'text-[#F0787B]' : 'text-muted'} break-words`, s.detail) : null,
        bar));
  }));
}

function renderDataDialog(st) {
  const kind = classify(st);
  const steps = Array.isArray(st.steps) ? st.steps : [];
  const { total, done, pct, name, run } = progressOf(st);
  const dataset = name ? `the ${name} dataset` : 'the dataset';
  const help = $('data-help');
  const actions = $('data-actions');
  const bar = $('data-bar');
  help.classList.add('hidden');
  help.replaceChildren();
  actions.replaceChildren();
  $('data-dialog-title').lastChild.textContent = kind === 'indexing' ? 'Preparing search' : 'Loading dataset';
  let line;
  if (st.state === 'empty') line = 'No data loaded yet';
  else if (st.state === 'failed') line = `Loading ${dataset} failed.`;
  else if (total && (kind === 'blocking' || kind === 'updating')) line = `Loading ${dataset}: ${fmtNum(done)} of ${fmtNum(total)} wells${pct != null ? ` (${pct}%)` : ''}`;
  else if (kind === 'indexing') line = 'The data is ready. Meaning-based search is still being prepared; keyword search works now.';
  else line = st.message || 'Starting up…';
  $('data-line').textContent = line;
  // With a step list the per-step rows carry the detail, so the single stage line and bar step aside.
  const stage = steps.length || st.state === 'empty' ? '' : st.state === 'failed' ? (run.error || st.message || '') : (run.stage ? sentence(run.stage) : (st.message && st.message !== line ? st.message : ''));
  $('data-stage').textContent = stage;
  $('data-stage').classList.toggle('hidden', !stage);
  $('data-stage').classList.toggle('text-[#F0787B]', st.state === 'failed');
  const showBar = !steps.length && pct != null && st.state !== 'empty';
  $('data-bar-wrap').classList.toggle('hidden', !showBar);
  bar.classList.toggle('bg-danger', st.state === 'failed');
  bar.classList.toggle('bg-accent', st.state !== 'failed');
  bar.style.width = `${pct ?? 0}%`;
  if (showBar) $('data-bar-wrap').setAttribute('aria-valuenow', String(pct));
  renderSteps(steps);
  if (st.state === 'failed' || st.state === 'empty') {
    help.classList.remove('hidden');
    help.append('Run ', el('code', 'font-mono text-text', 'wellwell init'), st.state === 'failed' ? ' again.' : ' to load the sample dataset.');
  }
  data.dismissible = kind === 'updating' || kind === 'indexing';
  if (st.state === 'failed') actions.append(dialogButton('Retry', true, () => pollStatus()));
  if (data.dismissible) {
    actions.append(dialogButton(kind === 'indexing' ? 'Continue — keyword search until ready' : 'Continue with current data', true, () => {
      data.dismissed = true;
      closeDataDialog();
      renderIndicator(data.status);
    }));
  }
}

function openDataDialog() {
  const dlg = $('data-dialog');
  if (!dlg.open) {
    try { dlg.showModal(); } catch { dlg.setAttribute('open', ''); }
    (dlg.querySelector('button') || dlg).focus();
  }
}
function closeDataDialog() {
  const dlg = $('data-dialog');
  if (dlg.open) dlg.close();
}

function renderIndicator(st) {
  const ind = $('data-indicator');
  const kind = st ? classify(st) : 'ready';
  if (kind === 'updating' && data.dismissed) {
    const { pct, name } = progressOf(st);
    ind.textContent = `Loading ${name ? `${name} ` : ''}data${pct != null ? ` · ${pct}%` : '…'}`;
  } else if (kind === 'indexing') {
    const pct = indexingProgress(st);
    ind.textContent = `Search index${pct != null ? ` · ${pct}%` : ' · preparing'}`;
  } else {
    ind.classList.add('hidden');
    return;
  }
  ind.classList.remove('hidden');
}

function schedulePoll(ms) {
  clearTimeout(data.timer);
  data.timer = setTimeout(pollStatus, ms);
}

async function fetchStatus() {
  try { return await api('/api/status', {}); } catch { return null; }
}

async function pollStatus() {
  const st = await fetchStatus();
  if (!st) {
    if (!data.everReached && !state.boot) { closeDataDialog(); showBootError(); return; }
    schedulePoll(3000);
    return;
  }
  data.everReached = true;
  applyStatus(st);
}

function applyStatus(st) {
  // "empty" with wells in the stats means data is there but no load run was recorded; let people in.
  if (st.state === 'empty' && st.stats?.wells > 0 && !(st.steps || []).some((s) => s.blocking && s.status !== 'done')) st = { ...st, state: 'ready' };
  const wasKind = data.kind;
  const kind = classify(st);
  data.status = st;
  data.kind = kind;
  renderFleet(st.stats, state.boot?.fleet);
  syncSearchAvailability(st);
  if (kind === 'blocking') {
    data.dismissed = false;
    renderIndicator(null);
    renderDataDialog(st);
    openDataDialog();
    schedulePoll(2000);
    return;
  }
  if (kind !== 'ready') {
    renderDataDialog(st);
    if (kind === 'updating' && !data.dismissed) openDataDialog();
    renderIndicator(st);
    schedulePoll(kind === 'updating' ? 3000 : 2000);
    if (!state.boot) startApp();
    return;
  }
  clearTimeout(data.timer);
  closeDataDialog();
  data.dismissed = false;
  renderIndicator(null);
  if (!state.boot) startApp();
  else if (wasKind && wasKind !== 'ready') loadMemoryCounts();
}

async function startApp() {
  if (data.starting || state.boot) return;
  data.starting = true;
  let b;
  try {
    b = await api('/api/bootstrap', {});
  } catch {
    data.starting = false;
    showBootError();
    return;
  }
  data.starting = false;
  if (!b.users?.length) {
    // The data went away between the status check and bootstrap; wait for it again.
    applyStatus(b.status && typeof b.status === 'object' ? b.status : { state: 'initialising', message: 'Starting up…' });
    return;
  }
  bootSkeleton(false);
  state.boot = b;
  state.users = b.users;
  renderFleet(data.status?.stats, b.fleet);
  setModelChip('bg-success', 'online');
  $('model-chip').title = b.model || '';
  $('examples').replaceChildren();
  renderExamples(b.examples || []);
  initSearch(b.search);
  initLearning();
  loadOverview();
  if (data.status) syncSearchAvailability(data.status);
  setBusy(false);
  const params = new URLSearchParams(location.search);
  const wanted = params.get('user') || recall('wellwell.user');
  selectUser(state.users.some((u) => u.user_id === wanted) ? wanted : state.users[0]?.user_id);
  const conv = Number(params.get('conversation'));
  if (conv) openConversation(conv);
}

function boot() {
  state.boot = null;
  data.everReached = false;
  bootSkeleton(true);
  $('question').disabled = true;
  $('send').disabled = true;
  setModelChip('bg-faint', 'connecting');
  pollStatus();
}

$('data-dialog').addEventListener('cancel', (e) => {
  // Escape only closes the dialog when the app can be used underneath.
  if (data.dismissible && data.kind !== 'blocking') {
    data.dismissed = true;
    renderIndicator(data.status);
  } else {
    e.preventDefault();
  }
});
$('data-indicator').addEventListener('click', () => {
  data.dismissed = false;
  renderIndicator(null);
  if (data.status) renderDataDialog(data.status);
  openDataDialog();
});

wire();
boot();
