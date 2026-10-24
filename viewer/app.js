/* research-dump web page
 * Data sources: demo (../sample-data) or a private GitHub data repository read with the user's token.
 * The token is kept in this browser only and is sent only to api.github.com.
 * Tabs: 홈 (graph), 논문 목록 (list), 연구 주제 (claims).
 */
(() => {
  'use strict';

  // ---------- constants ----------
  const AXES = {
    method: { label: '방법', multi: true, values: ['train', 'inference', 'eval', 'analysis', 'other'] },
    goal: { label: '목표', multi: true, values: ['harmlessness', 'honesty', 'helpfulness'] },
    modal: { label: 'Modal', multi: false, values: ['text', 'multimodal'] },
    setting: { label: 'Setting', multi: false, values: ['standalone', 'rag', 'agent'] },
    topic: { label: '세부 주제', multi: true, values: [] },
  };
  const AXIS_WEIGHT = { method: 1.0, goal: 1.0, modal: 1.3, setting: 1.3, topic: 1.6 };
  const CLAIM_TYPES = ['support', 'narrow', 'conflict', 'duplicate', 'motivates'];
  const L0_TYPES = ['extends', 'conflicts'];
  const L01_TYPES = [...CLAIM_TYPES, 'unrelated'];
  const CLAIM_STATUS = ['candidate', 'active', 'hold', 'retired'];
  const CLAIM_STATUS_KO = { candidate: '후보', active: '진행 중', hold: '보류', retired: '접음' };
  const EXP_STATUS = ['plan', 'running', 'done'];
  const EXP_STATUS_KO = { plan: '계획', running: '진행 중', done: '완료' };
  const STORAGE_KEY = 'research-dump.connection';
  const NEW_DAYS = 7, RECENT_DAYS = 28;
  const DIRS = ['papers', 'authors', 'claims', 'relations', 'requests'];

  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const $ = sel => document.querySelector(sel);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const today = () => new Date().toISOString().slice(0, 10);
  const daysAgo = n => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
  const newId = prefix => `${prefix}:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
  // File naming convention shared with pipeline/common.py
  const fileFor = (dir, id) => `${dir}/${String(id).replace(/[:/]/g, '_')}.json`;

  // ---------- state ----------
  const state = {
    source: null,             // 'demo' | 'github'
    tab: 'home',
    config: null,
    papers: new Map(),
    authors: new Map(),
    claims: new Map(),
    requests: new Map(),
    relations: [],
    excluded: [],
    keywords: {},
    filters: {},              // graph: axis -> Set of allowed values
    show: { extends: true, conflicts: true, 'same-author': false, pending: true },
    only: { all: false, hidden: false, industry: false, starred: false, nosummary: false },
    selectedClaim: null,
    list: { view: 'papers', sort: 'entered', liked: false, bookmarked: false, industry: false, starred: false,
      nosummary: false, hidden: false, venue: '', org: '', tags: new Set(), q: '', open: new Set() },
    topics: { status: '', origin: '', selected: null },
  };
  let cy = null;

  // ---------- paper helpers ----------
  const marksOf = p => ({ liked: false, bookmarked: false, hidden: false, ...(p.marks || {}),
    ...(p.status === 'confirmed' && !p.marks ? { liked: true } : {}), ...(p.status === 'hold' && !p.marks ? { bookmarked: true } : {}) });
  const enteredAt = p => p.entered_at || p.provenance?.created_at || '';
  const isNew = p => enteredAt(p) >= daysAgo(NEW_DAYS);
  const isStarred = p => String(p.meta?.popularity || '').startsWith('★');
  const venueOf = p => p.meta?.venue || p.venue || '';
  const tagList = p => { const t = p.tags || {}; return [...(t.method || []), ...(t.goal || []), t.modal, t.setting, ...(t.topic || [])].filter(Boolean); };

  // same as pipeline/migrate.py normalize_claim: 빈 곳 -> 근거, 첫 실험 -> planned experiment
  function normalizeClaim(c) {
    const x = { memo: '', ...c };
    if (x.gap) { x.basis = `${(x.basis || '').trim()}\n빈 곳: ${x.gap}`.trim(); }
    if (x.first_experiment) x.experiments = [{ title: '첫 실험', status: 'plan', setup: x.first_experiment, result: '' }, ...(x.experiments || [])];
    delete x.gap; delete x.first_experiment;
    x.experiments = (x.experiments || []).map(e => ({ status: (e.result || '').trim() ? 'done' : 'plan', ...e }));
    return x;
  }

  // ---------- storage (best effort) ----------
  const store = {
    get() { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch { return null; } },
    set(v) { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(v)); } catch { /* ignore */ } },
    clear() { try { localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ } },
  };

  // ---------- toast ----------
  let toastTimer;
  function toast(msg, isError = false) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = `toast show ${isError ? 'error' : ''}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = 'toast'; }, isError ? 6000 : 3000);
  }

  // ---------- data loading ----------
  const demoBase = () => new URL('../sample-data/', location.href);
  async function demoJSON(p) {
    const r = await fetch(new URL(p, demoBase()));
    if (!r.ok) throw new Error(`${p}: ${r.status}`);
    return r.json();
  }

  function fromEntries(entries) {
    const out = Object.fromEntries(DIRS.map(d => [d, []]));
    for (const [path, data] of entries) {
      const dir = path.split('/')[0];
      if (out[dir]) out[dir].push(data);
    }
    return out;
  }

  async function loadDemo() {
    const [bundle, config] = await Promise.all([demoJSON('bundle.json'), demoJSON('config.json')]);
    const data = fromEntries(Object.entries(bundle.entries).map(([k, e]) => [k, e.data]));
    return { config, ...data, excluded: bundle.excluded || [], keywords: bundle.keywords || {} };
  }

  const gh = {
    conn: null,
    headers() { return { Authorization: `Bearer ${this.conn.token}`, 'X-GitHub-Api-Version': '2022-11-28' }; },
    url(path) { return `https://api.github.com/repos/${this.conn.repo}${path}`; },
    async call(method, path, body) {
      const r = await fetch(this.url(path), {
        method, headers: { ...this.headers(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined, cache: 'no-store',
      });
      if (r.status === 401 || r.status === 403) throw new Error('토큰 권한을 확인해주세요 (Contents: Read and write).');
      if (r.status === 404) { const e = new Error('저장소나 branch를 찾을 수 없어요.'); e.status = 404; throw e; }
      if (!r.ok) { const e = new Error(`GitHub 응답 ${r.status}`); e.status = r.status; throw e; }
      return r.json();
    },
    async raw(path) {
      const r = await fetch(this.url(`/contents/${path.split('/').map(encodeURIComponent).join('/')}?ref=${encodeURIComponent(this.conn.branch)}`),
        { headers: { ...this.headers(), Accept: 'application/vnd.github.raw+json' }, cache: 'no-store' });
      if (!r.ok) { const e = new Error(`${path}: ${r.status}`); e.status = r.status; throw e; }
      return r.json();
    },
    // One commit for any number of files (Git Data API). Retries once if the branch moved.
    async commit(files, message) {
      const branch = encodeURIComponent(this.conn.branch);
      for (let attempt = 0; attempt < 2; attempt++) {
        const ref = await this.call('GET', `/git/ref/heads/${branch}`);
        const head = await this.call('GET', `/git/commits/${ref.object.sha}`);
        const tree = await this.call('POST', '/git/trees', {
          base_tree: head.tree.sha,
          tree: files.map(f => ({ path: f.path, mode: '100644', type: 'blob', content: JSON.stringify(f.data, null, 2) + '\n' })),
        });
        const c = await this.call('POST', '/git/commits', { message, tree: tree.sha, parents: [ref.object.sha] });
        try {
          await this.call('PATCH', `/git/refs/heads/${branch}`, { sha: c.sha });
          return;
        } catch (e) {
          if (e.status !== 422 || attempt === 1) throw e;
        }
      }
    },
  };

  // bundle.json holds every file with its git blob sha; only files that changed since are fetched one by one
  async function loadGitHub(conn, onProgress = () => {}) {
    gh.conn = conn;
    const tree = await gh.call('GET', `/git/trees/${encodeURIComponent(conn.branch)}?recursive=1`);
    const blobs = tree.tree.filter(t => t.type === 'blob');
    const paths = new Set(blobs.map(t => t.path));
    let bundle = null;
    if (paths.has('bundle.json')) { try { bundle = await gh.raw('bundle.json'); } catch { bundle = null; } }
    const targets = blobs.filter(t => /^(papers|authors|relations|claims|requests)\/[^/]+\.json$/.test(t.path));
    const entries = [];
    const toFetch = [];
    for (const t of targets) {
      const e = bundle?.entries?.[t.path];
      if (e && e.sha === t.sha) entries.push([t.path, e.data]); else toFetch.push(t.path);
    }
    let i = 0, done = 0;
    const worker = async () => {
      while (i < toFetch.length) {
        const p = toFetch[i++];
        entries.push([p, await gh.raw(p)]);
        onProgress(++done, toFetch.length);
      }
    };
    await Promise.all(Array.from({ length: 8 }, worker));
    const config = paths.has('config.json') ? await gh.raw('config.json') : null;
    const ids = new Set(entries.filter(([p]) => p.startsWith('papers/')).map(([, d]) => d.id));
    return { config, ...fromEntries(entries), excluded: (bundle?.excluded || []).filter(x => !ids.has(x.id)),
      keywords: bundle?.keywords || {}, fetched: toFetch.length, bundled: !!bundle };
  }

  // ---------- saving ----------
  // items: [{dir, data}] ; updates local state first, then commits (github) or keeps in memory (demo)
  async function save(items, message, { quiet = false } = {}) {
    for (const { dir, data } of items) {
      if (dir === 'papers') state.papers.set(data.id, data);
      else if (dir === 'authors') state.authors.set(data.id, data);
      else if (dir === 'claims') state.claims.set(data.id, data);
      else if (dir === 'requests') state.requests.set(data.id, data);
      else if (dir === 'relations') {
        const k = state.relations.findIndex(r => r.id === data.id);
        k >= 0 ? (state.relations[k] = data) : state.relations.push(data);
      }
    }
    if (state.source !== 'github') { toast('데모 모드라서 저장되지 않아요 (새로고침하면 사라져요).'); return true; }
    try {
      await gh.commit(items.map(({ dir, data }) => ({ path: fileFor(dir, data.id), data })), message);
      if (!quiet) toast('저장했어요.');
      return true;
    } catch (err) {
      toast(`저장하지 못했어요: ${err.message}`, true);
      return false;
    }
  }

  function refresh() {
    updateStatus();
    if (state.tab === 'home') { renderSidebar(); renderGraph(); }
    else if (state.tab === 'papers') renderList();
    else renderTopics();
  }

  function ingest(data) {
    state.config = data.config || { scope: '', topics: [] };
    state.papers = new Map(data.papers.map(p => [p.id, p]));
    state.authors = new Map(data.authors.map(a => [a.id, a]));
    state.claims = new Map(data.claims.map(c => [c.id, normalizeClaim(c)]));
    state.requests = new Map((data.requests || []).filter(r => r.status !== 'dropped').map(r => [r.id, r]));
    state.relations = data.relations;
    state.excluded = data.excluded || [];
    state.keywords = data.keywords || {};
    const topics = new Set(state.config.topics || []);
    for (const p of state.papers.values()) for (const t of p.tags?.topic || []) topics.add(t);
    AXES.topic.values = [...topics];
    state.filters = Object.fromEntries(Object.entries(AXES).map(([k, a]) => [k, new Set(a.values)]));
    state.selectedClaim = null;
  }

  function updateStatus() {
    const hidden = [...state.papers.values()].filter(p => marksOf(p).hidden).length;
    $('#load-status').textContent = `논문 ${state.papers.size}편${hidden ? ` (숨김 ${hidden})` : ''} · 연구 주제 ${state.claims.size}개 · 관계 ${state.relations.length}개`;
  }

  async function useSource(kind) {
    const status = $('#load-status');
    status.textContent = '불러오는 중...';
    try {
      const conn = store.get();
      const data = kind === 'github' && conn
        ? await loadGitHub(conn, (d, n) => { status.textContent = `바뀐 파일 불러오는 중... ${d}/${n}`; })
        : await loadDemo();
      state.source = kind === 'github' && conn ? 'github' : 'demo';
      ingest(data);
      $('#source-label').textContent = state.source === 'github' ? `저장소: ${conn.repo}` : '데모 데이터 (공개 논문만, 저장 안 됨)';
      $('#scope').textContent = state.config.scope ? `연구 스콥 · ${state.config.scope}` : '';
      $('#btn-import').hidden = state.source !== 'github';
      refresh();
      showEmptyDetail();
    } catch (err) {
      status.textContent = `불러오지 못했어요: ${err.message}`;
    }
  }

  // ---------- tabs ----------
  function setTab(tab, { push = true } = {}) {
    state.tab = tab;
    for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('on', b.dataset.tab === tab);
    $('#view-home').hidden = tab !== 'home';
    $('#view-papers').hidden = tab !== 'papers';
    $('#view-topics').hidden = tab !== 'topics';
    if (push) history.replaceState(null, '', tab === 'home' ? location.pathname : `#${tab}`);
    if (state.papers.size || state.source) refresh();
  }
  $('#tabs').onclick = e => { const b = e.target.closest('button[data-tab]'); if (b) setTab(b.dataset.tab); };

  // ---------- graph filtering ----------
  function claimLinks(claimId) {
    return state.relations.filter(r => r.layer === '0-1' && r.to === claimId && r.type !== 'unrelated' && r.status !== 'rejected');
  }

  // default graph: 좋아요 + 최근 4주 + ★ (unless 전체 논문 보기)
  const inDefaultGraph = p => marksOf(p).liked || enteredAt(p) >= daysAgo(RECENT_DAYS) || isStarred(p);

  function visiblePapers() {
    const linked = state.selectedClaim ? new Set(claimLinks(state.selectedClaim).map(r => r.from)) : null;
    return [...state.papers.values()].filter(p => {
      if (linked) return linked.has(p.id);
      if (marksOf(p).hidden && !state.only.hidden) return false;
      if (!state.only.all && !inDefaultGraph(p)) return false;
      if (state.only.industry && !p.meta?.industry) return false;
      if (state.only.starred && !isStarred(p)) return false;
      if (state.only.nosummary && p.summary?.claim) return false;
      for (const [axis, def] of Object.entries(AXES)) {
        const allowed = state.filters[axis];
        const v = p.tags?.[axis];
        const vals = def.multi ? (v || []) : (v ? [v] : []);
        if (vals.length === 0) continue;
        if (!vals.some(x => allowed.has(x))) return false;
      }
      return true;
    });
  }

  // ---------- layout: tag vectors -> 2D (PCA) -> spread ----------
  function tagVector(p) {
    const vec = [];
    for (const [axis, def] of Object.entries(AXES)) {
      const v = p.tags?.[axis];
      const vals = new Set(def.multi ? (v || []) : (v ? [v] : []));
      for (const val of def.values) vec.push(vals.has(val) ? AXIS_WEIGHT[axis] : 0);
    }
    return vec;
  }

  function pca2(rows) {
    const n = rows.length, d = rows[0]?.length || 0;
    if (n < 3 || d === 0) return rows.map((_, i) => [Math.cos(i * 2.4), Math.sin(i * 2.4)]);
    const mean = Array(d).fill(0);
    rows.forEach(r => r.forEach((x, j) => { mean[j] += x / n; }));
    const X = rows.map(r => r.map((x, j) => x - mean[j]));
    const C = Array.from({ length: d }, () => Array(d).fill(0));
    X.forEach(r => { for (let a = 0; a < d; a++) if (r[a]) for (let b = 0; b < d; b++) C[a][b] += r[a] * r[b]; });
    const comps = [];
    for (let k = 0; k < 2; k++) {
      let v = Array.from({ length: d }, (_, j) => Math.sin(j * 1.7 + k + 1));
      for (let it = 0; it < 100; it++) {
        let w = C.map(row => row.reduce((s, c, j) => s + c * v[j], 0));
        for (const u of comps) { const dot = w.reduce((s, x, j) => s + x * u[j], 0); w = w.map((x, j) => x - dot * u[j]); }
        const norm = Math.hypot(...w) || 1;
        v = w.map(x => x / norm);
      }
      comps.push(v);
    }
    return X.map(r => comps.map(u => r.reduce((s, x, j) => s + x * u[j], 0)));
  }

  function computePositions(papers, width, height) {
    if (!papers.length) return new Map();
    const pts = pca2(papers.map(tagVector));
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    const span = a => (Math.max(...a) - Math.min(...a)) || 1;
    const sx = (width * 0.8) / span(xs), sy = (height * 0.8) / span(ys);
    const pos = pts.map(([x, y]) => ({ x: (x - Math.min(...xs)) * sx + width * 0.1, y: (y - Math.min(...ys)) * sy + height * 0.1 }));
    const minGap = 70;
    pos.forEach((p, i) => { p.x += Math.cos(i * 2.39996) * (i % 7) * 4; p.y += Math.sin(i * 2.39996) * (i % 7) * 4; });
    for (let it = 0; it < 250; it++) {
      let moved = false;
      for (let i = 0; i < pos.length; i++) for (let j = i + 1; j < pos.length; j++) {
        const dx = pos[j].x - pos[i].x, dy = pos[j].y - pos[i].y, dist = Math.hypot(dx, dy) || 0.01;
        if (dist < minGap) {
          const push = (minGap - dist) / 2, ux = dx / dist, uy = dy / dist;
          pos[i].x -= ux * push; pos[i].y -= uy * push; pos[j].x += ux * push; pos[j].y += uy * push;
          moved = true;
        }
      }
      if (!moved) break;
    }
    return new Map(papers.map((p, i) => [p.id, pos[i]]));
  }

  // ---------- graph ----------
  const shortTitle = t => { const s = String(t).split(':')[0]; return s.length > 30 ? s.slice(0, 28) + '…' : s; };

  function buildElements() {
    const papers = visiblePapers();
    const box = $('#graph').getBoundingClientRect();
    const pos = computePositions(papers, Math.max(box.width, 600), Math.max(box.height, 450));
    const visible = new Set(papers.map(p => p.id));
    const els = [];
    const claimType = new Map();
    if (state.selectedClaim) for (const r of claimLinks(state.selectedClaim)) claimType.set(r.from, r.type);

    for (const p of papers) {
      const m = marksOf(p);
      const cls = ['paper', `m-${p.tags?.main_method || p.tags?.method?.[0] || 'other'}`];
      if (isNew(p)) cls.push('fresh');
      if (m.liked) cls.push('liked');
      if (m.hidden) cls.push('hidden-paper');
      const pop = p.meta?.popularity;
      if (pop === '★★') cls.push('pop-2'); else if (pop === '★') cls.push('pop-1');
      if (claimType.has(p.id)) cls.push(`c-${claimType.get(p.id)}`);
      els.push({ group: 'nodes', data: { id: p.id, label: shortTitle(p.title) }, position: pos.get(p.id), classes: cls.join(' ') });
    }

    for (const r of state.relations) {
      if (r.layer !== '0' || r.status === 'rejected') continue;
      if (!visible.has(r.from) || !visible.has(r.to)) continue;
      if (!state.show[r.type]) continue;
      if (r.status === 'new' && !state.show.pending) continue;
      els.push({ group: 'edges', data: { id: r.id, source: r.from, target: r.to, label: r.status === 'new' ? 'new' : '' }, classes: `${r.type} ${r.status === 'new' ? 'pending' : ''}` });
    }

    if (state.show['same-author']) {
      for (const a of state.authors.values()) {
        if (!a.watch) continue;
        const mine = papers.filter(p => (p.authors || []).includes(a.id));
        if (!mine.length) continue;
        const cx = mine.reduce((s, p) => s + pos.get(p.id).x, 0) / mine.length;
        const cyy = mine.reduce((s, p) => s + pos.get(p.id).y, 0) / mine.length;
        els.push({ group: 'nodes', data: { id: a.id, label: a.name }, position: { x: cx, y: cyy - 50 }, classes: 'author' });
        for (const p of mine) els.push({ group: 'edges', data: { id: `${a.id}->${p.id}`, source: p.id, target: a.id }, classes: 'same-author' });
      }
    }
    return els;
  }

  function starSvg(n) {
    const text = n === 2 ? '★★' : '★';
    const size = n === 2 ? 34 : 52;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">` +
      `<text x="50" y="${n === 2 ? 62 : 68}" font-size="${size}" text-anchor="middle" fill="#ffffff" font-family="sans-serif">${text}</text></svg>`;
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
  }

  function metaLine(p) {
    const m = p.meta || {};
    const parts = [];
    const venue = venueOf(p), year = m.year || p.year;
    if (venue) parts.push(venue === 'arXiv' || !year ? venue : `${venue} ${year}`);
    if (m.institutions?.length) parts.push(m.institutions.slice(0, 2).join(', ') + (m.institutions.length > 2 ? ' 외' : ''));
    const date = String(m.published || p.published || '').slice(0, 7);
    if (date.length === 7) parts.push(date.replace('-', '.'));
    if (m.citations != null) parts.push(`인용 ${m.citations}`);
    return parts.join(' · ');
  }

  function graphStyle() {
    const ink = css('--ink'), quiet = css('--quiet'), surface = css('--surface');
    const method = m => ({ selector: `node.m-${m}`, style: { 'background-color': css(`--m-${m}`) } });
    const ring = t => ({ selector: `node.c-${t}`, style: { 'border-color': css(`--${t}`), 'border-width': 5 } });
    return [
      { selector: 'node.paper', style: {
        width: 26, height: 26, label: 'data(label)', 'font-size': 10, color: ink,
        'text-valign': 'bottom', 'text-margin-y': 4, 'text-wrap': 'wrap', 'text-max-width': 110,
        'text-background-color': surface, 'text-background-opacity': 0.75, 'text-background-padding': 1,
        'border-width': 2, 'border-color': surface } },
      ...['train', 'inference', 'eval', 'analysis', 'other'].map(method),
      { selector: 'node.pop-1', style: { width: 34, height: 34, 'background-image': starSvg(1), 'background-fit': 'contain', 'background-clip': 'none' } },
      { selector: 'node.pop-2', style: { width: 42, height: 42, 'background-image': starSvg(2), 'background-fit': 'contain', 'background-clip': 'none' } },
      { selector: 'node.fresh', style: { 'border-color': css('--new'), 'border-width': 4 } },
      { selector: 'node.liked', style: { 'border-color': css('--liked'), 'border-width': 4 } },
      { selector: 'node.hidden-paper', style: { opacity: 0.35 } },
      ...CLAIM_TYPES.map(ring),
      { selector: 'node.author', style: {
        shape: 'diamond', width: 18, height: 18, 'background-color': css('--author'),
        label: 'data(label)', 'font-size': 10, color: quiet, 'text-valign': 'top', 'text-margin-y': -4 } },
      { selector: 'edge', style: { width: 1.5, 'curve-style': 'bezier', 'font-size': 9, color: quiet, label: 'data(label)' } },
      { selector: 'edge.extends', style: { 'line-color': css('--extends'), 'target-arrow-color': css('--extends'), 'target-arrow-shape': 'triangle' } },
      { selector: 'edge.conflicts', style: { 'line-color': css('--conflicts'), 'line-style': 'dashed' } },
      { selector: 'edge.same-author', style: { 'line-color': css('--author'), width: 0.8, opacity: 0.6 } },
      { selector: 'edge.pending', style: { opacity: 0.55 } },
      { selector: ':selected', style: { 'overlay-color': css('--accent'), 'overlay-opacity': 0.15, 'overlay-padding': 6 } },
    ];
  }

  function renderGraph() {
    const els = buildElements();
    const empty = $('#empty');
    empty.hidden = els.some(e => e.group === 'nodes');
    empty.textContent = state.papers.size
      ? (state.only.all ? '조건에 맞는 논문이 없어요.' : '조건에 맞는 논문이 없어요. 왼쪽 "보기"에서 "전체 논문 보기"를 켜보세요.')
      : '아직 논문이 없어요. 왼쪽에서 읽기 목록을 가져오거나, 수집이 돌기를 기다려주세요.';
    if (cy) cy.destroy();
    cy = cytoscape({
      container: $('#graph'), elements: els, style: graphStyle(), layout: { name: 'preset', fit: true, padding: 40 },
      wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 3,
    });
    cy.on('tap', 'node.paper', e => showPaper(e.target.id()));
    cy.on('tap', 'node.author', e => showAuthor(e.target.id()));
    cy.on('tap', e => { if (e.target === cy) (state.selectedClaim ? showClaim(state.selectedClaim) : showEmptyDetail()); });
    renderLegend();
  }

  function renderLegend() {
    const parts = ['train', 'inference', 'eval', 'analysis', 'other'].map(m => `<span><i class="dot" style="background:var(--m-${m})"></i>${m}</span>`);
    parts.push('<span><i class="ring" style="border-color:var(--liked)"></i>좋아요</span>');
    parts.push(`<span><i class="ring" style="border-color:var(--new)"></i>new (${NEW_DAYS}일 이내)</span>`);
    parts.push('<span>★ 인용 속도 상위 20%</span><span>★★ 상위 5%</span>');
    if (state.selectedClaim) parts.push(...CLAIM_TYPES.map(t => `<span><i class="ring" style="border-color:var(--${t})"></i>${t}</span>`));
    $('#legend').innerHTML = parts.join('');
  }

  // ---------- home sidebar ----------
  function claimLabel(c) {
    return `${esc(c.text)}<span class="status">${c.origin === 'ai' ? 'AI 추천 · ' : ''}${esc(CLAIM_STATUS_KO[c.status] || c.status)} · v${esc(c.version)} · ${claimLinks(c.id).length}편</span>`;
  }

  function renderSidebar() {
    const claims = $('#claims');
    const items = [`<li data-id="" class="${state.selectedClaim ? '' : 'active'}">전체 논문</li>`];
    for (const c of state.claims.values()) {
      if (c.status === 'retired') continue;
      items.push(`<li data-id="${esc(c.id)}" class="${state.selectedClaim === c.id ? 'active' : ''}">${claimLabel(c)}</li>`);
    }
    claims.innerHTML = items.join('');
    claims.onclick = e => {
      const li = e.target.closest('li'); if (!li) return;
      state.selectedClaim = li.dataset.id || null;
      renderSidebar(); renderGraph();
      state.selectedClaim ? showClaim(state.selectedClaim) : showEmptyDetail();
    };

    const rel = [
      ['extends', 'extends', 'var(--extends)', ''],
      ['conflicts', 'conflicts', 'var(--conflicts)', 'dash'],
      ['same-author', 'same-author (저자 노드)', 'var(--author)', ''],
      ['pending', '승인 전 관계(new) 보기', 'var(--quiet)', 'dash'],
    ];
    $('#relation-toggles').innerHTML = rel.map(([k, label, color, dash]) =>
      `<label><input type="checkbox" data-k="${k}" ${state.show[k] ? 'checked' : ''}><i class="swatch ${dash}" style="border-color:${color}"></i>${label}</label>`).join('');
    $('#relation-toggles').onchange = e => { state.show[e.target.dataset.k] = e.target.checked; renderGraph(); };

    const all = [...state.papers.values()];
    const nDefault = all.filter(p => inDefaultGraph(p) && !marksOf(p).hidden).length;
    const nHidden = all.filter(p => marksOf(p).hidden).length;
    const nInd = all.filter(p => p.meta?.industry).length;
    const nStar = all.filter(isStarred).length;
    const noSum = all.filter(p => !p.summary?.claim);
    const nNoSum = noSum.length, nNoAbs = noSum.filter(p => !String(p.abstract || '').trim()).length;
    $('#view-toggles').innerHTML = `
      <label><input type="checkbox" data-only="all" ${state.only.all ? 'checked' : ''}>전체 논문 보기<span class="count">${all.length}</span></label>
      <p class="hint">${state.only.all ? '모든 논문을 보여줘요.' : `기본: 좋아요 + 최근 4주 + ★ 논문 (${nDefault}편)`}</p>
      <label><input type="checkbox" data-only="hidden" ${state.only.hidden ? 'checked' : ''}>숨긴 논문도 보기<span class="count">${nHidden}</span></label>
      <label><input type="checkbox" data-only="industry" ${state.only.industry ? 'checked' : ''}>기업 논문만<span class="count">${nInd}</span></label>
      <label><input type="checkbox" data-only="starred" ${state.only.starred ? 'checked' : ''}>★ 이상만<span class="count">${nStar}</span></label>
      <label><input type="checkbox" data-only="nosummary" ${state.only.nosummary ? 'checked' : ''}>요약 없음만<span class="count">${nNoSum}</span></label>
      ${nNoSum ? `<p class="hint">요약 없음 ${nNoSum}편 중 초록 없음 ${nNoAbs}편 (초록이 없으면 자동 요약이 안 돼요)</p>` : ''}`;
    $('#view-toggles').onchange = e => { state.only[e.target.dataset.only] = e.target.checked; renderSidebar(); renderGraph(); };

    const counts = {};
    for (const p of all) for (const [axis, def] of Object.entries(AXES)) {
      const v = p.tags?.[axis]; const vals = def.multi ? (v || []) : (v ? [v] : []);
      for (const x of vals) counts[`${axis}:${x}`] = (counts[`${axis}:${x}`] || 0) + 1;
    }
    $('#tag-filters').innerHTML = Object.entries(AXES).map(([axis, def]) => `
      <details class="tag-group" open>
        <summary>${def.label}</summary>
        <button type="button" class="mini" data-axis="${axis}" data-all="1">전체</button><button type="button" class="mini" data-axis="${axis}" data-all="0">해제</button>
        ${def.values.map(v => `<label><input type="checkbox" data-axis="${axis}" value="${esc(v)}" ${state.filters[axis].has(v) ? 'checked' : ''}>${esc(v)}<span class="count">${counts[`${axis}:${v}`] || 0}</span></label>`).join('')}
      </details>`).join('');
    $('#tag-filters').onchange = e => {
      const { axis } = e.target.dataset; if (!axis) return;
      e.target.checked ? state.filters[axis].add(e.target.value) : state.filters[axis].delete(e.target.value);
      renderGraph();
    };
    $('#tag-filters').onclick = e => {
      const b = e.target.closest('button[data-axis]'); if (!b) return;
      state.filters[b.dataset.axis] = b.dataset.all === '1' ? new Set(AXES[b.dataset.axis].values) : new Set();
      renderSidebar(); renderGraph();
    };
  }

  // ---------- shared pieces ----------
  const field = (label, text) => `<div class="field"><b>${label}</b>${text ? `<p>${esc(text)}</p>` : '<p class="empty-text">아직 없음</p>'}</div>`;
  const nameOf = id => state.papers.get(id)?.title || state.claims.get(id)?.text || id;
  const options = (values, selected, labels = {}) => values.map(v => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(labels[v] || v)}</option>`).join('');

  function markButtons(p) {
    const m = marksOf(p);
    const b = (k, on, label) => `<button type="button" class="mark ${on ? 'on' : ''}" data-action="mark" data-mark="${k}" data-id="${esc(p.id)}" aria-pressed="${on}">${label}</button>`;
    return b('liked', m.liked, m.liked ? '♥ 좋아요' : '♡ 좋아요') + b('bookmarked', m.bookmarked, '북마크') + b('hidden', m.hidden, m.hidden ? '숨김 해제' : '숨김');
  }

  function requestButtons(p) {
    const pend = [...state.requests.values()].filter(r => r.paper === p.id);
    const busy = k => pend.some(r => r.kind === k && r.status !== 'failed');
    return `
      <button type="button" data-action="paper-resummarize" data-id="${esc(p.id)}" ${p.abstract ? '' : 'disabled title="초록이 없어서 요약할 수 없어요"'}>재요약</button>
      <button type="button" data-action="request" data-kind="relations" data-id="${esc(p.id)}" ${busy('relations') ? 'disabled' : ''}>${busy('relations') ? '관계 찾는 중...' : '관계 찾기'}</button>
      <button type="button" data-action="request" data-kind="topics" data-id="${esc(p.id)}" ${busy('topics') ? 'disabled' : ''}>${busy('topics') ? '추천 받는 중...' : '연구 주제 추천'}</button>`;
  }

  const REQUEST_LABEL = { topics: '연구 주제 추천', relations: '관계 찾기', claim_papers: '저장소에서 관련 논문 찾기', claim_web: '관련 논문 웹 검색', add_paper: '논문 추가' };
  const REQUEST_DONE = { topics: '연구 주제 추천 결과가 들어왔어요.', relations: '관계 찾기 결과가 들어왔어요.',
    claim_papers: '관련 논문 찾기 결과가 들어왔어요.', claim_web: '웹 추천 논문이 들어왔어요.', add_paper: '논문을 추가하고 요약과 관계 초안까지 만들었어요.' };

  function requestNoteList(filter) {
    return [...state.requests.values()].filter(filter).map(r => {
      const what = REQUEST_LABEL[r.kind] || r.kind;
      const target = r.kind === 'add_paper' ? ` "${esc(r.query)}"` : '';
      return r.status === 'failed'
        ? `<p class="rel duplicate">${what}${target} 실패: ${esc(r.error || '')} <button type="button" class="mini" data-action="request-retry" data-id="${esc(r.id)}">다시 요청</button> <button type="button" class="mini quiet" data-action="request-drop" data-id="${esc(r.id)}">지우기</button></p>`
        : `<p class="hint">${what}${target} 진행 중 (${esc(r.created_at)}). 보통 1~3분 걸려요.</p>`;
    }).join('');
  }
  const requestNotes = p => requestNoteList(r => r.paper === p.id && r.kind !== 'add_paper');

  const addPaperForm = (claimId = '') => `
    <form class="edit add-paper" data-form="add-paper" data-claim="${esc(claimId)}">
      <input name="query" required placeholder="arXiv 번호, arXiv 링크 또는 논문 제목">
      <button type="submit">추가하기</button>
    </form>`;

  const memoForm = (kind, id, memo, rows = 4) => `
    <form class="edit memo" data-form="memo" data-kind="${kind}" data-id="${esc(id)}">
      <textarea name="memo" rows="${rows}" placeholder="메모 (나만 보는 생각, 읽은 소감, 아이디어)">${esc(memo)}</textarea>
      <div class="actions"><button type="submit" class="mini">메모 저장</button></div>
    </form>`;

  // ---------- detail panel (home) ----------
  function showEmptyDetail() {
    $('#detail').innerHTML = '<p class="hint">논문 노드를 누르거나, 왼쪽에서 연구 주제를 고르면 여기에 내용이 보여요.</p>';
  }

  function relCard(r, otherId) {
    const target = state.papers.has(otherId) ? `<a href="#" data-paper="${esc(otherId)}">${esc(nameOf(otherId))}</a>`
      : state.claims.has(otherId) ? `<a href="#" data-claim="${esc(otherId)}">${esc(nameOf(otherId))}</a>` : esc(nameOf(otherId));
    const actions = r.status === 'new'
      ? `<div class="actions"><button type="button" data-action="rel-approve" data-id="${esc(r.id)}">승인</button><button type="button" class="quiet" data-action="rel-reject" data-id="${esc(r.id)}">거절</button></div>`
      : '';
    return `<div class="rel ${esc(r.type)}">
      <div><span class="type">${esc(r.type)}</span> <span class="badge ${esc(r.status)}">${esc(r.status)}</span></div>
      <div>${target}</div>
      <p class="meta">${esc(r.reason)}${r.model ? ` · ${esc(r.model)}` : ''}</p>
      ${actions}
    </div>`;
  }

  function paperHead(p) {
    const m = marksOf(p);
    return `<div class="meta-line">${isStarred(p) ? `<span class="star">${esc(p.meta.popularity)}</span> ` : ''}${esc(metaLine(p) || `${p.venue || ''} ${p.year || ''}`)}${isNew(p) ? ' <span class="chip new">new</span>' : ''}${p.meta?.industry ? ' <span class="chip">기업</span>' : ''}${m.bookmarked ? ' <span class="chip">북마크</span>' : ''}${m.hidden ? ' <span class="chip">숨김</span>' : ''}</div>`;
  }

  function showPaper(id, editing = false) {
    const p = state.papers.get(id); if (!p) return;
    const t = p.tags || {};
    const chips = [...(t.method || []).map(m => (m === t.main_method ? `★ ${m}` : m)), ...(t.goal || []), t.modal, t.setting, ...(t.topic || [])].filter(Boolean);
    const linked = (p.authors || []).map(a => state.authors.get(a)?.name).filter(Boolean);
    const names = p.author_names || [];
    const authors = linked.length ? linked : (names.length > 4 ? [...names.slice(0, 3), `외 ${names.length - 3}명`] : names);
    const pv = p.provenance || {};
    const out = state.relations.filter(r => r.from === id && r.type !== 'unrelated');
    const inc = state.relations.filter(r => r.to === id && r.layer === '0');
    const s = p.summary || {};
    const summary = editing ? `
      <form class="edit" data-form="summary" data-id="${esc(id)}">
        <label>주장<textarea name="claim" rows="3">${esc(s.claim)}</textarea></label>
        <label>조건<textarea name="condition" rows="3">${esc(s.condition)}</textarea></label>
        <label>빈 곳<textarea name="gap" rows="3">${esc(s.gap)}</textarea></label>
        <div class="actions"><button type="submit">저장</button><button type="button" class="quiet" data-action="paper-view" data-id="${esc(id)}">취소</button></div>
      </form>` : `
      ${pv.summary_check === 'incomplete' ? '<p class="rel duplicate">요약 일부가 비어 있어요. 확인해주세요.</p>' : ''}
      ${field('주장', s.claim)}${field('조건', s.condition)}${field('빈 곳', s.gap)}
      <p class="meta">요약: ${esc(pv.summary_model === 'none' || !pv.summary_model ? '아직 없음 (다음 실행 때 자동 요약)' : pv.summary_model)}${pv.summary_from === 'abstract' ? ' · 초록 기준' : ''} <button type="button" class="mini" data-action="summary-edit" data-id="${esc(id)}">직접 입력</button></p>`;
    const targets = [...state.papers.values()].filter(x => x.id !== id);
    $('#detail').innerHTML = `
      <h3>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}</h3>
      ${paperHead(p)}
      ${authors.length ? `<div class="meta">${esc(authors.join(', '))}</div>` : ''}
      <div class="chips">${chips.map(c => `<span class="chip">${esc(c)}</span>`).join('')}</div>
      <div class="actions wrap">${markButtons(p)}</div>
      <div class="actions wrap">${requestButtons(p)}</div>
      ${requestNotes(p)}
      ${pv.screen_reason ? `<p class="meta">스크리닝: ${esc(pv.screen_reason)}</p>` : ''}
      <h4>메모</h4>
      ${memoForm('paper', id, p.memo)}
      <h4>요약</h4>
      ${summary}
      ${p.abstract ? `<details class="abstract"><summary>초록 보기</summary><p>${esc(p.abstract)}</p></details>` : ''}
      <h4>관계</h4>
      ${[...out.map(r => relCard(r, r.to)), ...inc.map(r => relCard(r, r.from))].join('') || '<p class="hint">아직 관계가 없어요.</p>'}
      <details class="add-rel">
        <summary>관계 직접 추가</summary>
        <form class="edit" data-form="relation" data-id="${esc(id)}">
          <label>대상
            <select name="to" required>
              <optgroup label="Layer 1 · 연구 주제">${[...state.claims.values()].map(c => `<option value="${esc(c.id)}">${esc(c.text)}</option>`).join('')}</optgroup>
              <optgroup label="Layer 0 · 논문">${targets.map(x => `<option value="${esc(x.id)}">${esc(shortTitle(x.title))}</option>`).join('')}</optgroup>
            </select>
          </label>
          <label>관계<select name="type" required></select></label>
          <label>이유<textarea name="reason" rows="2" required></textarea></label>
          <div class="actions"><button type="submit">추가</button></div>
        </form>
      </details>
    `;
    syncRelationTypes();
  }

  function syncRelationTypes() {
    const f = $('#detail form[data-form="relation"]'); if (!f) return;
    const isClaim = state.claims.has(f.to.value);
    f.type.innerHTML = options(isClaim ? L01_TYPES : L0_TYPES);
  }

  function showAuthor(id) {
    const a = state.authors.get(id); if (!a) return;
    const mine = [...state.papers.values()].filter(p => (p.authors || []).includes(id));
    $('#detail').innerHTML = `<h3>${esc(a.name)}</h3><div class="meta">${esc(a.affiliation || '')}</div>
      <h4>논문 ${mine.length}편</h4>${mine.map(p => `<div class="rel"><a href="#" data-paper="${esc(p.id)}">${esc(p.title)}</a></div>`).join('')}`;
  }

  // open a paper on the 홈 tab and centre it in the graph
  function focusPaper(id) {
    if (state.tab !== 'home') setTab('home');
    const p = state.papers.get(id);
    if (p && !visiblePapers().some(x => x.id === id)) { state.only.all = true; if (marksOf(p).hidden) state.only.hidden = true; state.selectedClaim = null; renderSidebar(); renderGraph(); }
    showPaper(id);
    if (cy) { cy.$(':selected').unselect(); const n = cy.getElementById(id); if (n.nonempty()) { n.select(); cy.animate({ center: { eles: n } }, { duration: 250 }); } }
  }

  // ---------- claims (연구 주제) ----------
  const claimTarget = () => (state.tab === 'topics' ? $('#topic-detail') : $('#detail'));

  function claimForm(c) {
    return `
      <form class="edit" data-form="claim" data-id="${esc(c?.id || '')}">
        <label>연구 주제 (한 줄)<textarea name="text" rows="2" required>${esc(c?.text)}</textarea></label>
        <label>상태<select name="status">${options(CLAIM_STATUS, c?.status || 'candidate', CLAIM_STATUS_KO)}</select></label>
        <label>근거 (왜 될 것 같은지, 아직 아무도 안 한 것)<textarea name="basis" rows="4">${esc(c?.basis)}</textarea></label>
        ${c ? '' : `<label>메모<textarea name="memo" rows="3"></textarea></label>`}
        ${c ? '<label>바꾼 이유 (문장을 바꿀 때만)<input name="reason"></label>' : ''}
        <div class="actions"><button type="submit">${c ? '저장' : '만들기'}</button><button type="button" class="quiet" data-action="claim-cancel" data-id="${esc(c?.id || '')}">취소</button></div>
      </form>`;
  }

  function showClaim(id, mode = 'view') {
    const c = state.claims.get(id); if (!c) return;
    const box = claimTarget();
    if (mode === 'edit') { box.innerHTML = `<h3>연구 주제 수정</h3><p class="meta">문장을 바꾸면 v${c.version + 1}이 되고, 지금 문장은 버전 기록에 남아요.</p>${claimForm(c)}`; return; }
    const links = state.relations.filter(r => r.layer === '0-1' && r.to === id && r.status !== 'rejected');
    const dup = links.filter(r => r.type === 'duplicate');
    const busyKind = k => [...state.requests.values()].some(r => r.claim === id && r.kind === k && r.status !== 'failed');
    const busyClaim = busyKind('claim_papers'), busyWeb = busyKind('claim_web');
    box.innerHTML = `
      <h3>${esc(c.text)}</h3>
      <div class="meta">${c.origin === 'ai' ? `<span class="chip ai">AI 추천</span> ` : ''}${esc(CLAIM_STATUS_KO[c.status] || c.status)} · v${esc(c.version)}${c.created_at ? ` · ${esc(c.created_at)}` : ''}
        <button type="button" class="mini" data-action="claim-edit" data-id="${esc(id)}">수정</button></div>
      ${c.origin === 'ai' && c.status === 'candidate' ? `<div class="actions"><button type="button" data-action="claim-status" data-status="active" data-id="${esc(id)}">진행할래요</button><button type="button" class="quiet" data-action="claim-status" data-status="retired" data-id="${esc(id)}">접기</button></div>` : ''}
      ${dup.length ? `<p class="rel duplicate"><b>duplicate ${dup.length}건</b>: 이미 이 주제를 다룬 논문이 있을 수 있어요.</p>` : ''}
      <h4>메모</h4>
      ${memoForm('claim', id, c.memo)}
      <h4>근거</h4>
      ${c.basis ? `<p class="basis">${esc(c.basis)}</p>` : '<p class="hint">아직 없음. "수정"에서 적을 수 있어요.</p>'}
      ${c.model ? `<p class="meta">추천 모델: ${esc(c.model)}</p>` : ''}
      <h4>관련 논문 <span class="count">${links.length}편</span></h4>
      <div class="actions wrap">
        <button type="button" data-action="claim-papers" data-id="${esc(id)}" ${busyClaim ? 'disabled' : ''}>${busyClaim ? '찾는 중...' : '저장소에서 관련 논문 찾기'}</button>
        <button type="button" data-action="claim-web" data-id="${esc(id)}" ${busyWeb ? 'disabled' : ''}>${busyWeb ? '검색 중...' : '관련 논문 웹 검색'}</button>
      </div>
      ${requestNoteList(r => r.claim === id && r.kind !== 'claim_web')}
      ${links.map(r => relCard(r, r.from)).join('') || '<p class="hint">아직 없어요. "관련 논문 찾기"를 누르거나, 아래에서 논문을 추가해보세요.</p>'}
      <details class="add-rel"><summary>저장소에 없는 논문 추가</summary>
        <p class="hint">추가하면 저장 → 요약 → 이 연구 주제와 다른 논문들에 대한 관계 초안까지 만들어요.</p>
        ${addPaperForm(id)}
      </details>
      ${webSuggestions(c, id)}
      <h4>실험</h4>
      ${(c.experiments || []).map((x, i) => `<div class="rel exp">
        <div><b>${esc(x.title)}</b> <span class="meta">${esc(x.date || '')}</span>
          <select class="exp-status" data-claim="${esc(id)}" data-i="${i}">${options(EXP_STATUS, x.status || 'plan', EXP_STATUS_KO)}</select></div>
        ${x.setup ? field('설정', x.setup) : ''}${x.result ? field('결과', x.result) : ''}</div>`).join('') || '<p class="hint">기록된 실험 없음</p>'}
      <details class="add-rel">
        <summary>실험 추가</summary>
        <form class="edit" data-form="experiment" data-id="${esc(id)}">
          <label>제목<input name="title" required></label>
          <label>상태<select name="status">${options(EXP_STATUS, 'plan', EXP_STATUS_KO)}</select></label>
          <label>날짜<input name="date" type="date" value="${today()}"></label>
          <label>설정<textarea name="setup" rows="3"></textarea></label>
          <label>결과<textarea name="result" rows="3"></textarea></label>
          <div class="actions"><button type="submit">추가</button></div>
        </form>
      </details>
      <details class="add-rel"><summary>버전 기록 (${(c.history || []).length})</summary>
        ${(c.history || []).slice().reverse().map(h => `<div class="rel"><b>v${esc(h.version)}</b> ${esc(h.text)}<p class="meta">${esc(h.changed_at || '')} ${esc(h.reason || '')}</p></div>`).join('') || '<p class="hint">이전 버전 없음</p>'}
      </details>
    `;
  }

  // 관련 논문 웹 검색 results stored on the claim: show 10, sorted by the chosen order
  const WEB_SORTS = { venue: '좋은 학회', citations: '인용수', company: '탑 기업' };
  function webSuggestions(c, id) {
    const ws = c.web_suggestions;
    const notes = requestNoteList(r => r.claim === id && r.kind === 'claim_web');
    if (!ws) return notes ? `<h4>웹 추천 논문</h4>${notes}` : '';
    const sort = state.topics.webSort || 'venue';
    const cit = x => x.citations || 0;
    const items = [...(ws.items || [])].sort({
      venue: (a, b) => (b.top_venue - a.top_venue) || cit(b) - cit(a),
      citations: (a, b) => cit(b) - cit(a),
      company: (a, b) => ((b.top_orgs || []).length > 0) - ((a.top_orgs || []).length > 0) || cit(b) - cit(a),
    }[sort]).slice(0, 10);
    const adding = new Set([...state.requests.values()].filter(r => r.kind === 'add_paper' && r.status !== 'failed' && r.item).map(r => r.item.id));
    const row = x => {
      const inRepo = state.papers.has(x.id);
      const meta = [x.venue_short && x.venue_short !== 'arXiv' ? `${x.venue_short}${x.year ? ` ${x.year}` : ''}` : (x.year || ''),
        (x.institutions || []).slice(0, 2).join(', ') + ((x.institutions || []).length > 2 ? ' 외' : ''), `인용 ${cit(x)}`].filter(Boolean).join(' · ');
      return `<div class="rel web">
        <div><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>
          ${x.top_venue ? '<span class="chip">좋은 학회</span>' : ''}${(x.top_orgs || []).map(o => `<span class="chip">${esc(o)}</span>`).join('')}</div>
        <p class="meta">${esc(meta)}</p>
        ${x.reason ? `<p class="meta">${esc(x.reason)}</p>` : ''}
        <div class="actions">${inRepo ? '<span class="hint">저장소에 있음</span>'
          : adding.has(x.id) ? '<span class="hint">추가 중...</span>'
          : `<button type="button" class="mini" data-action="web-add" data-claim="${esc(id)}" data-id="${esc(x.id)}">추가하기</button>`}</div>
      </div>`;
    };
    return `<h4>웹 추천 논문 <span class="count">${(ws.items || []).length > 10 ? `${ws.items.length}편 중 10편` : `${(ws.items || []).length}편`} · ${esc(ws.searched_at || '')}</span></h4>
      ${notes}
      <div class="chipset">${Object.entries(WEB_SORTS).map(([k, v]) => `<button type="button" class="mini ${sort === k ? 'on' : ''}" data-action="web-sort" data-sort="${k}" data-id="${esc(id)}">${v} 순</button>`).join('')}</div>
      ${items.map(row).join('') || '<p class="hint">관련 있는 논문을 찾지 못했어요.</p>'}
      ${ws.queries?.length ? `<p class="meta">검색어: ${esc(ws.queries.join(' / '))}</p>` : ''}`;
  }

  function newClaimForm() {
    claimTarget().innerHTML = `<h3>새 연구 주제</h3>${claimForm(null)}`;
  }

  // ---------- 연구 주제 tab ----------
  function renderTopics() {
    const t = state.topics;
    const all = [...state.claims.values()];
    const count = f => all.filter(f).length;
    const chip = (key, val, label, n) => `<button type="button" class="mini ${t[key] === val ? 'on' : ''}" data-tf="${key}" data-v="${esc(val)}">${label}${n != null ? ` ${n}` : ''}</button>`;
    $('#topic-head').innerHTML = `
      <div class="row">
        <div class="chipset">${chip('status', '', '전체', all.length)}${CLAIM_STATUS.map(s => chip('status', s, CLAIM_STATUS_KO[s], count(c => c.status === s))).join('')}</div>
        <div class="chipset">${chip('origin', '', '모두')}${chip('origin', 'manual', '직접', count(c => c.origin !== 'ai'))}${chip('origin', 'ai', 'AI 추천', count(c => c.origin === 'ai'))}</div>
        <button type="button" data-action="claim-new" class="push-right">+ 새 연구 주제</button>
      </div>`;
    const rows = all.filter(c => (!t.status || c.status === t.status) && (!t.origin || (t.origin === 'ai' ? c.origin === 'ai' : c.origin !== 'ai')))
      .sort((a, b) => (CLAIM_STATUS.indexOf(a.status) - CLAIM_STATUS.indexOf(b.status)) || String(b.created_at || '').localeCompare(String(a.created_at || '')));
    $('#topic-list').innerHTML = rows.map(c => `
      <div class="row-card ${t.selected === c.id ? 'active' : ''}" data-claim-row="${esc(c.id)}">
        <div class="row-title">${c.origin === 'ai' ? '<span class="chip ai">AI 추천</span> ' : ''}${esc(c.text)}</div>
        <div class="meta">${esc(CLAIM_STATUS_KO[c.status] || c.status)} · v${esc(c.version)} · 연결 논문 ${claimLinks(c.id).length}편 · 실험 ${(c.experiments || []).length}개${c.created_at ? ` · ${esc(c.created_at)}` : ''}</div>
        ${c.basis ? `<div class="row-text clamp2">${esc(c.basis)}</div>` : ''}
        ${c.memo ? `<div class="memo-preview">${esc(c.memo)}</div>` : ''}
      </div>`).join('') || '<p class="hint">연구 주제가 없어요. "+ 새 연구 주제"로 만들거나, 논문에서 "연구 주제 추천"을 눌러보세요.</p>';
    if (t.selected && state.claims.has(t.selected)) showClaim(t.selected);
  }

  $('#topic-head').addEventListener('click', e => {
    const b = e.target.closest('button[data-tf]'); if (!b) return;
    state.topics[b.dataset.tf] = b.dataset.v; renderTopics();
  });
  $('#topic-list').addEventListener('click', e => {
    const row = e.target.closest('[data-claim-row]'); if (!row) return;
    state.topics.selected = row.dataset.claimRow; renderTopics();
  });

  // ---------- 논문 목록 tab ----------
  function listPapers() {
    const L = state.list;
    const q = L.q.trim().toLowerCase();
    const rows = [...state.papers.values()].filter(p => {
      const m = marksOf(p);
      if (m.hidden && !L.hidden) return false;
      if (L.liked && !m.liked) return false;
      if (L.bookmarked && !m.bookmarked) return false;
      if (L.industry && !p.meta?.industry) return false;
      if (L.starred && !isStarred(p)) return false;
      if (L.nosummary && p.summary?.claim) return false;
      if (L.venue && venueOf(p) !== L.venue) return false;
      if (L.org && !(p.meta?.institutions || []).includes(L.org)) return false;
      const tags = new Set(tagList(p));
      for (const t of L.tags) if (!tags.has(t)) return false;
      if (q && !`${p.title} ${(p.author_names || []).join(' ')} ${p.summary?.claim || ''} ${p.memo || ''}`.toLowerCase().includes(q)) return false;
      return true;
    });
    const cites = p => (p.meta?.citations ?? -1);
    const speed = p => (p.meta?.citations ? (p.meta.cites_per_month ?? 0) : -1); // no citations -> bottom
    const by = {
      entered: (a, b) => enteredAt(b).localeCompare(enteredAt(a)) || String(b.published || '').localeCompare(String(a.published || '')),
      citations: (a, b) => cites(b) - cites(a),
      popularity: (a, b) => speed(b) - speed(a) || cites(b) - cites(a),
    }[L.sort];
    return rows.sort(by);
  }

  function countBy(values) {
    const m = new Map();
    for (const v of values) if (v) m.set(v, (m.get(v) || 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }

  function keywordTable() {
    const k = state.keywords || {};
    const total = k.total || {}, last = k.last_run || {};
    const rows = Object.entries(total).sort((a, b) => (b[1].included || 0) - (a[1].included || 0));
    if (!rows.length) return '<p class="hint">아직 통계가 없어요. 다음 주간 실행부터 쌓여요.</p>';
    const pct = (a, b) => (b ? `${Math.round((a / b) * 100)}%` : '-');
    return `<table class="kw"><thead><tr><th>키워드</th><th title="${esc(k.last_run_date || '')}">이번 실행</th><th>누적</th><th>포함 비율</th></tr></thead><tbody>
      ${rows.map(([kw, v]) => `<tr><td>${esc(kw)}</td><td>${last[kw] ? `${last[kw].included}/${last[kw].collected}` : '-'}</td><td>${v.included}/${v.collected}</td><td>${pct(v.included, v.collected)}</td></tr>`).join('')}
    </tbody></table><p class="hint">포함/수집. 수집은 했지만 거의 포함되지 않는 키워드는 config.json에서 빼도 돼요.</p>`;
  }

  function renderListFilters() {
    const L = state.list;
    const all = [...state.papers.values()];
    const venues = countBy(all.map(venueOf));
    const orgs = countBy(all.flatMap(p => p.meta?.institutions || []));
    const tagCounts = countBy(all.flatMap(tagList));
    const chk = (k, label, n) => `<label><input type="checkbox" data-lf="${k}" ${L[k] ? 'checked' : ''}>${label}${n != null ? `<span class="count">${n}</span>` : ''}</label>`;
    $('#list-filters').innerHTML = `
      <section class="panel">
        <h2>보기</h2>
        <div class="seg">
          <button type="button" data-lv="papers" class="${L.view === 'papers' ? 'on' : ''}">논문 ${all.length}</button>
          <button type="button" data-lv="excluded" class="${L.view === 'excluded' ? 'on' : ''}">제외됨 ${state.excluded.length}</button>
        </div>
        ${L.view === 'excluded' ? '<p class="hint">스크리닝에서 최근 4주 동안 제외된 후보예요. 잘못 빠진 논문은 "포함시키기"로 넣을 수 있어요.</p>' : ''}
      </section>
      ${L.view === 'papers' ? `
      <section class="panel">
        <h2>정렬</h2>
        <select data-ls="sort">${options(['entered', 'citations', 'popularity'], L.sort, { entered: '들어온 날짜', citations: '인용수', popularity: '인기 (인용 속도)' })}</select>
      </section>
      <section class="panel toggles">
        <h2>표시</h2>
        ${chk('liked', '♥ 좋아요만', all.filter(p => marksOf(p).liked).length)}
        ${chk('bookmarked', '북마크만', all.filter(p => marksOf(p).bookmarked).length)}
        ${chk('starred', '★ 이상만', all.filter(isStarred).length)}
        ${chk('industry', '기업 논문만', all.filter(p => p.meta?.industry).length)}
        ${chk('nosummary', '요약 없음만', all.filter(p => !p.summary?.claim).length)}
        ${chk('hidden', '숨긴 논문도 보기', all.filter(p => marksOf(p).hidden).length)}
      </section>
      <section class="panel">
        <h2>학회</h2>
        <select data-ls="venue"><option value="">전체</option>${venues.map(([v, n]) => `<option value="${esc(v)}" ${L.venue === v ? 'selected' : ''}>${esc(v)} (${n})</option>`).join('')}</select>
      </section>
      <section class="panel">
        <h2>소속</h2>
        <select data-ls="org"><option value="">전체</option>${orgs.map(([v, n]) => `<option value="${esc(v)}" ${L.org === v ? 'selected' : ''}>${esc(v)} (${n})</option>`).join('')}</select>
      </section>
      <section class="panel">
        <h2>태그 <span class="hint">(누른 태그를 모두 가진 논문)</span></h2>
        <div class="chipset">${tagCounts.map(([t, n]) => `<button type="button" class="mini ${L.tags.has(t) ? 'on' : ''}" data-ltag="${esc(t)}">${esc(t)} ${n}</button>`).join('')}</div>
      </section>` : ''}
      <section class="panel">
        <details class="tag-group"><summary>키워드 통계</summary>${keywordTable()}</details>
      </section>`;
  }

  function paperRow(p) {
    const m = marksOf(p), s = p.summary || {};
    const open = state.list.open.has(p.id);
    return `<div class="row-card ${m.hidden ? 'dim' : ''}" data-row="${esc(p.id)}">
      <div class="row-title">
        <span class="heart ${m.liked ? 'on' : ''}" title="좋아요">${m.liked ? '♥' : '♡'}</span>
        ${isStarred(p) ? `<span class="star">${esc(p.meta.popularity)}</span>` : ''}
        ${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}
        ${isNew(p) ? '<span class="chip new">new</span>' : ''}${m.bookmarked ? '<span class="chip">북마크</span>' : ''}${m.hidden ? '<span class="chip">숨김</span>' : ''}
      </div>
      <div class="meta">${esc(metaLine(p))}${enteredAt(p) ? ` · 들어온 날 ${esc(enteredAt(p).slice(5).replace('-', '.'))}` : ''}</div>
      <div class="chips">${tagList(p).map(t => `<span class="chip">${esc(t)}</span>`).join('')}</div>
      ${s.claim ? `<div class="row-text">${esc(s.claim)}</div>` : '<div class="row-text empty-text">요약 없음</div>'}
      ${p.memo && !open ? `<div class="memo-preview">${esc(p.memo)}</div>` : ''}
      ${open ? `<div class="row-more">
        ${field('조건', s.condition)}${field('빈 곳', s.gap)}
        <div class="actions wrap">${markButtons(p)}</div>
        <div class="actions wrap">${requestButtons(p)}<button type="button" class="quiet" data-action="focus" data-id="${esc(p.id)}">그래프에서 보기</button></div>
        ${requestNotes(p)}
        <b class="label">메모</b>${memoForm('paper', p.id, p.memo, 5)}
      </div>` : ''}
      <button type="button" class="row-toggle quiet mini" data-action="row-toggle" data-id="${esc(p.id)}">${open ? '접기' : '펼치기'}</button>
    </div>`;
  }

  function excludedRow(x) {
    const date = String(x.published || '').slice(0, 7).replace('-', '.');
    return `<div class="row-card" data-ex="${esc(x.id)}">
      <div class="row-title">${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}</div>
      <div class="meta">${esc([x.venue, date, `제외 ${String(x.screened_at || '').slice(5).replace('-', '.')}`].filter(Boolean).join(' · '))}</div>
      <div class="row-text">제외 이유: ${esc(x.screen_reason || '-')}</div>
      ${x.abstract ? `<details class="abstract"><summary>초록 보기</summary><p>${esc(x.abstract)}</p></details>` : ''}
      <div class="actions"><button type="button" data-action="include" data-id="${esc(x.id)}">포함시키기</button></div>
    </div>`;
  }

  const PAGE = 100;
  let listLimit = PAGE;
  function renderList() {
    renderListFilters();
    const L = state.list;
    if (L.view === 'excluded') {
      const rows = state.excluded.filter(x => !state.papers.has(x.id));
      $('#list-head').innerHTML = `<div class="row"><b>제외됨 ${rows.length}편</b><input type="search" data-ls="q" placeholder="제목 검색" value="${esc(L.q)}"></div>`;
      const q = L.q.trim().toLowerCase();
      $('#paper-list').innerHTML = rows.filter(x => !q || String(x.title).toLowerCase().includes(q)).map(excludedRow).join('')
        || '<p class="hint">최근 4주 동안 제외된 후보가 없어요. (주간 실행이 bundle.json을 만든 뒤부터 보여요.)</p>';
      return;
    }
    const rows = listPapers();
    $('#list-head').innerHTML = `<div class="row"><b>${rows.length}편</b><input type="search" data-ls="q" placeholder="제목, 저자, 주장, 메모 검색" value="${esc(L.q)}"></div>
      <details class="add-rel list-add"><summary>논문 추가</summary>
        <p class="hint">추가하면 저장 → 요약 → 연구 주제들에 대한 관계 초안까지 만들어요.</p>${addPaperForm()}</details>
      ${requestNoteList(r => r.kind === 'add_paper' && !r.claim)}`;
    $('#paper-list').innerHTML = rows.slice(0, listLimit).map(paperRow).join('')
      + (rows.length > listLimit ? `<button type="button" class="mini-wide" data-action="more">더 보기 (${rows.length - listLimit}편 남음)</button>` : '')
      || '<p class="hint">조건에 맞는 논문이 없어요.</p>';
  }

  function rerenderRow(id) {
    const el = document.querySelector(`#paper-list [data-row="${CSS.escape(id)}"]`);
    const p = state.papers.get(id);
    if (el && p) el.outerHTML = paperRow(p);
  }

  $('#list-filters').addEventListener('change', e => {
    const L = state.list, t = e.target;
    if (t.dataset.lf) L[t.dataset.lf] = t.checked;
    else if (t.dataset.ls) L[t.dataset.ls] = t.value;
    listLimit = PAGE; renderList();
  });
  $('#list-filters').addEventListener('click', e => {
    const v = e.target.closest('button[data-lv]'), tg = e.target.closest('button[data-ltag]');
    if (v) { state.list.view = v.dataset.lv; state.list.q = ''; renderList(); }
    if (tg) { const s = state.list.tags; s.has(tg.dataset.ltag) ? s.delete(tg.dataset.ltag) : s.add(tg.dataset.ltag); listLimit = PAGE; renderList(); }
  });
  let searchTimer;
  $('#list-head').addEventListener('input', e => {
    if (e.target.dataset.ls !== 'q') return;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.list.q = e.target.value;
      const pos = e.target.selectionStart;
      renderList();
      const box = $('#list-head input[data-ls="q"]'); box.focus(); box.setSelectionRange(pos, pos);
    }, 250);
  });

  // ---------- re-summarize from the browser ----------
  // Same prompt as pipeline/summarize.py. Keep the two in sync.
  const SUMMARY_PROMPT = lang => `You summarize one research paper for a researcher looking for research gaps.
Use only the title and abstract given. Do not invent results that are not stated.
Write in ${lang}.

Fields:
- claim: what the paper shows or proposes, one or two sentences, with the main number if the abstract gives one.
- condition: the scope under which the claim holds: models, data, modality, setting, assumptions. Never leave this empty; if the abstract does not say, write what is missing.
- gap: what the paper does not cover or leaves open, one or two sentences. Start with "(저자)" if the abstract itself states this limitation, otherwise "(추론)".

Plain text only: no LaTeX, no markdown.
Reply with JSON only: {"claim": "...", "condition": "...", "gap": "..."}`;

  const llmKeyName = name => `research-dump.llmkey.${name}`;
  const llmKeys = {
    get(name) { try { return localStorage.getItem(llmKeyName(name)) || ''; } catch { return ''; } },
    set(name, v) { try { localStorage.setItem(llmKeyName(name), v); } catch { /* ignore */ } },
    clearAll() { try { Object.keys(localStorage).filter(k => k.startsWith('research-dump.llmkey.')).forEach(k => localStorage.removeItem(k)); } catch { /* ignore */ } },
  };

  const cleanText = t => String(t || '')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '')
    .replace(/\\[A-Za-z]+\*?\{([^{}]+)\}/g, '$1')
    .replace(/\\([A-Za-z]+)\*?(\{\})?/g, '$1')
    .replace(/\$/g, '').replace(/\{\}/g, '')
    .replace(/\s+/g, ' ').trim();

  function parseJsonReply(text) {
    const t = String(text).trim().replace(/^```(?:json)?\s*|\s*```$/g, '');
    const tries = [t];
    const i = t.indexOf('{'), j = t.lastIndexOf('}');
    if (i >= 0 && j > i) tries.push(t.slice(i, j + 1));
    for (const c of tries) for (const v of [c, c.replace(/,\s*([}\]])/g, '$1')]) { try { return JSON.parse(v); } catch { /* next */ } }
    throw new Error('요약 결과를 읽지 못했어요');
  }

  async function callLLM(cfg, system, user) {
    let key = llmKeys.get(cfg.key_name);
    if (!key) {
      key = (prompt(`${cfg.key_name} 값을 입력해주세요.\n이 브라우저에만 저장되고 ${new URL(cfg.base_url).host} 말고는 보내지 않아요. 로그아웃하면 지워져요.`) || '').trim();
      if (!key) throw new Error('키가 없어서 취소했어요');
      llmKeys.set(cfg.key_name, key);
    }
    const models = [cfg.model, cfg.fallback_model].filter(m => m && m !== 'SET-ME');
    if (!models.length) throw new Error('config.json의 llm.summary.model을 먼저 정해주세요');
    let last = '';
    for (const model of models) {
      const r = await fetch(cfg.base_url.replace(/\/$/, '') + '/chat/completions', {
        method: 'POST',
        headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, temperature: 0, response_format: { type: 'json_object' },
          messages: [{ role: 'system', content: system }, { role: 'user', content: user }] }),
      });
      if (r.status === 401 || r.status === 403) { llmKeys.set(cfg.key_name, ''); throw new Error('키가 맞지 않아요. 다시 누르면 새로 입력할 수 있어요'); }
      if ([429, 500, 502, 503, 504].includes(r.status)) { last = `${model}: ${r.status}`; continue; }
      if (!r.ok) throw new Error(`${model}: ${r.status}`);
      const data = await r.json();
      return { text: data.choices?.[0]?.message?.content || '', model: `${new URL(cfg.base_url).host}/${model}` };
    }
    throw new Error(`모델이 바빠요 (${last}). 잠시 뒤에 다시 눌러주세요`);
  }

  async function resummarize(id, btn) {
    const p0 = state.papers.get(id);
    const cfg = state.config?.llm?.summary;
    if (!p0?.abstract) { toast('초록이 없어서 요약할 수 없어요.', true); return; }
    if (!cfg) { toast('config.json에 llm.summary가 없어요.', true); return; }
    btn.disabled = true; btn.textContent = '요약 중...';
    try {
      const lang = state.config?.summary?.language || 'Korean, keeping technical terms in English';
      const reply = await callLLM(cfg, SUMMARY_PROMPT(lang), `Title: ${cleanText(p0.title)}\n\nAbstract: ${cleanText(p0.abstract).slice(0, 4000)}`);
      const s = parseJsonReply(reply.text);
      const summary = { claim: cleanText(s.claim), condition: cleanText(s.condition), gap: cleanText(s.gap) };
      const p = structuredClone(p0);
      p.summary = summary;
      p.provenance = { ...(p.provenance || {}), summary_model: reply.model, summary_from: 'abstract',
        summarized_at: today(), summary_check: Object.values(summary).every(Boolean) ? 'ok' : 'incomplete' };
      await save([{ dir: 'papers', data: p }], `Re-summarize ${id}`);
      afterPaperChange(id);
    } catch (err) {
      toast(`재요약하지 못했어요: ${err.message}`, true);
      if (document.contains(btn)) { btn.disabled = false; btn.textContent = '재요약'; }
    }
  }

  // ---------- requests processed by GitHub Actions (관계 찾기, 연구 주제 추천) ----------
  async function makeRequest(kind, paperId, extra = {}) {
    if (state.source !== 'github') { toast('이 기능은 내 저장소를 연결해야 쓸 수 있어요.', true); return false; }
    const r = { id: newId('req'), kind, ...(paperId ? { paper: paperId } : {}), ...extra, status: 'pending', created_at: today() };
    const ok = await save([{ dir: 'requests', data: r }], `Request ${kind} ${paperId || extra.claim || extra.query || ''}`, { quiet: true });
    if (!ok) { state.requests.delete(r.id); return false; }
    toast({
      topics: '연구 주제 추천을 요청했어요. 1~3분 뒤 연구 주제 탭에 후보가 생겨요.',
      relations: '관계 찾기를 요청했어요. 1~3분 뒤 이 논문의 관계에 후보가 생겨요.',
      claim_papers: '관련 논문 찾기를 요청했어요. 1~3분 뒤 관련 논문에 후보가 생겨요.',
      claim_web: '웹 검색을 요청했어요. 2~4분 뒤 웹 추천 논문이 보여요.',
      add_paper: '논문 추가를 요청했어요. 저장, 요약, 관계 초안까지 1~3분 걸려요.',
    }[kind]);
    afterRequestChange(r);
    watchRequest(r);
    return true;
  }

  function afterRequestChange(r) {
    if (r.paper && r.kind !== 'add_paper' && state.papers.has(r.paper)) afterPaperChange(r.paper);
    else if (r.claim && state.claims.has(r.claim)) showClaim(r.claim);
    else if (state.tab === 'papers') renderList();
  }

  // check every 30 s until the request file is gone (processed), then reload
  function watchRequest(r, tries = 20) {
    setTimeout(async () => {
      if (state.source !== 'github') return;
      try {
        const cur = await gh.raw(fileFor('requests', r.id));
        if (cur.status === 'failed') { state.requests.set(r.id, cur); toast(`요청이 실패했어요: ${cur.error || ''}`, true); afterRequestChange(cur); return; }
        if (tries > 1) watchRequest(r, tries - 1);
      } catch (e) {
        if (e.status === 404) {
          toast(REQUEST_DONE[r.kind] || '결과가 들어왔어요.');
          const keep = { tab: state.tab, claim: state.selectedClaim };
          await useSource('github');
          if (keep.tab === 'home') {
            if (keep.claim && state.claims.has(keep.claim)) { state.selectedClaim = keep.claim; renderSidebar(); renderGraph(); showClaim(keep.claim); }
            else if (r.paper && state.papers.has(r.paper)) showPaper(r.paper);
          }
        } else if (tries > 1) watchRequest(r, tries - 1);
      }
    }, 30000);
  }

  // ---------- actions ----------
  function afterPaperChange(id) {
    if (state.tab === 'home') { renderSidebar(); renderGraph(); showPaper(id); }
    else if (state.tab === 'papers') rerenderRow(id);
    updateStatus();
  }

  async function toggleMark(id, mark) {
    const p = structuredClone(state.papers.get(id));
    const m = marksOf(p);
    delete p.status;
    m[mark] = !m[mark];
    if (mark === 'liked') { if (m.liked) m.liked_at = today(); else delete m.liked_at; }
    p.marks = m;
    const label = { liked: '좋아요', bookmarked: '북마크', hidden: '숨김' }[mark];
    await save([{ dir: 'papers', data: p }], `${m[mark] ? '' : 'Undo '}${label}: ${id}`, { quiet: true });
    if (mark === 'hidden' && m.hidden) toast('숨겼어요. "숨긴 논문도 보기"를 켜면 다시 보여요.');
    if (state.tab === 'papers' && mark === 'hidden') { renderList(); updateStatus(); } else afterPaperChange(id);
  }

  async function setRelationStatus(id, status) {
    const r = structuredClone(state.relations.find(x => x.id === id));
    r.status = status;
    await save([{ dir: 'relations', data: r }], `${status === 'approved' ? 'Approve' : 'Reject'} ${id}`);
    refresh();
    if (state.tab === 'topics') showClaim(r.to);
    else state.papers.has(r.from) && !state.selectedClaim ? showPaper(r.from) : showClaim(r.to);
  }

  async function includeExcluded(id) {
    const x = state.excluded.find(e => e.id === id); if (!x) return;
    const p = {
      id: x.id, title: x.title, authors: [], author_names: x.authors || [], ids: x.ids || {}, published: x.published || '',
      year: x.year ?? null, venue: x.venue || '', url: x.url || '', abstract: x.abstract || '',
      tags: { method: ['other'], main_method: 'other', goal: [], modal: 'text', setting: 'standalone', topic: [] },
      summary: { claim: '', condition: '', gap: '' }, marks: { liked: false, bookmarked: false, hidden: false }, memo: '',
      entered_at: today(), source: 'include',
      provenance: { screen_model: x.screen_model || '', screen_reason: `사람이 포함시킴 (원래 제외 이유: ${x.screen_reason || '-'})`,
        found_via: x.sources || [], created_at: today(), needs_tags: true },
    };
    if (await save([{ dir: 'papers', data: p }], `Include ${id} (excluded by screening)`)) {
      toast('포함시켰어요. 태그와 요약은 다음 주간 실행 때 채워져요.');
      renderList();
    }
  }

  document.addEventListener('click', e => {
    const link = e.target.closest('a[data-paper]');
    if (link) { e.preventDefault(); focusPaper(link.dataset.paper); return; }
    const clink = e.target.closest('a[data-claim]');
    if (clink) { e.preventDefault(); setTab('topics'); state.topics.selected = clink.dataset.claim; renderTopics(); return; }
    const b = e.target.closest('button[data-action]'); if (!b) return;
    const { action, id } = b.dataset;
    if (action === 'mark') toggleMark(id, b.dataset.mark);
    else if (action === 'paper-resummarize') resummarize(id, b);
    else if (action === 'request') makeRequest(b.dataset.kind, id);
    else if (action === 'claim-papers') makeRequest('claim_papers', null, { claim: id });
    else if (action === 'claim-web') makeRequest('claim_web', null, { claim: id });
    else if (action === 'web-sort') { state.topics.webSort = b.dataset.sort; showClaim(id); }
    else if (action === 'web-add') {
      const item = state.claims.get(b.dataset.claim)?.web_suggestions?.items?.find(x => x.id === id);
      if (item) makeRequest('add_paper', null, { query: item.title, claim: b.dataset.claim, item });
    }
    else if (action === 'request-drop') { dropRequest(id); }
    else if (action === 'request-retry') {
      const r = { ...state.requests.get(id), status: 'pending', error: '', created_at: today() };
      save([{ dir: 'requests', data: r }], `Retry request ${id}`, { quiet: true }).then(ok => { if (ok) { afterRequestChange(r); watchRequest(r); } });
    }
    else if (action === 'summary-edit') showPaper(id, true);
    else if (action === 'paper-view') showPaper(id);
    else if (action === 'rel-approve') setRelationStatus(id, 'approved');
    else if (action === 'rel-reject') setRelationStatus(id, 'rejected');
    else if (action === 'claim-edit') showClaim(id, 'edit');
    else if (action === 'claim-new') newClaimForm();
    else if (action === 'claim-cancel') id ? showClaim(id) : (state.tab === 'topics' ? ($('#topic-detail').innerHTML = '') : showEmptyDetail());
    else if (action === 'claim-status') {
      const c = { ...structuredClone(state.claims.get(id)), status: b.dataset.status };
      save([{ dir: 'claims', data: c }], `Claim ${id}: ${c.status}`).then(() => { refresh(); showClaim(id); });
    }
    else if (action === 'row-toggle') { const o = state.list.open; o.has(id) ? o.delete(id) : o.add(id); rerenderRow(id); }
    else if (action === 'focus') focusPaper(id);
    else if (action === 'include') includeExcluded(id);
    else if (action === 'more') { listLimit += PAGE; renderList(); }
  });

  document.addEventListener('change', e => {
    if (e.target.closest('#detail') && e.target.name === 'to') syncRelationTypes();
    if (e.target.matches('select.exp-status')) {
      const { claim, i } = e.target.dataset;
      const c = structuredClone(state.claims.get(claim));
      c.experiments[+i].status = e.target.value;
      save([{ dir: 'claims', data: c }], `Experiment status: ${claim}`, { quiet: true }).then(() => state.tab === 'topics' && renderTopics());
    }
  });

  // a failed request is removed by writing it as dropped (the next request run deletes it)
  async function dropRequest(id) {
    const r = { ...state.requests.get(id), status: 'dropped' };
    if (await save([{ dir: 'requests', data: r }], `Drop request ${id}`, { quiet: true })) { state.requests.delete(id); afterRequestChange(r); }
  }

  document.addEventListener('submit', async e => {
    const f = e.target;
    if (!f.dataset.form) return;
    e.preventDefault();
    const id = f.dataset.id, kind = f.dataset.form;
    const btn = f.querySelector('button[type="submit"]'); if (btn) btn.disabled = true;
    try {
      if (kind === 'add-paper') {
        const query = f.query.value.trim();
        if (query && await makeRequest('add_paper', null, { query, ...(f.dataset.claim ? { claim: f.dataset.claim } : {}) })) f.query.value = '';
      } else if (kind === 'memo') {
        const memo = f.memo.value.replace(/\s+$/, '');
        if (f.dataset.kind === 'paper') {
          const p = structuredClone(state.papers.get(id)); p.memo = memo;
          if (await save([{ dir: 'papers', data: p }], `Memo: ${id}`)) {
            if (state.tab === 'papers') rerenderRow(id);
          }
        } else {
          const c = structuredClone(state.claims.get(id)); c.memo = memo;
          if (await save([{ dir: 'claims', data: c }], `Memo: ${id}`) && state.tab === 'topics') renderTopics();
        }
      } else if (kind === 'summary') {
        const p = structuredClone(state.papers.get(id));
        p.summary = { claim: f.claim.value.trim(), condition: f.condition.value.trim(), gap: f.gap.value.trim() };
        p.provenance = { ...(p.provenance || {}), summary_model: 'manual' };
        await save([{ dir: 'papers', data: p }], `Edit summary of ${id}`);
        showPaper(id);
      } else if (kind === 'relation') {
        const to = f.to.value, isClaim = state.claims.has(to);
        const r = { id: newId('rel'), from: id, to, layer: isClaim ? '0-1' : '0', type: f.type.value,
          reason: f.reason.value.trim(), status: 'approved', model: 'manual', created_at: today() };
        await save([{ dir: 'relations', data: r }], `Add ${r.type}: ${id} -> ${to}`);
        refresh(); showPaper(id);
      } else if (kind === 'claim') {
        const text = f.text.value.trim(), status = f.status.value;
        const extra = { basis: f.basis.value.trim() };
        let c;
        if (id) {
          c = structuredClone(state.claims.get(id));
          if (text !== c.text) {
            c.history = [...(c.history || []), { version: c.version, text: c.text, changed_at: today(), reason: f.reason?.value.trim() || '' }];
            c.version += 1; c.text = text;
          }
          Object.assign(c, { status, ...extra });
        } else {
          c = { id: newId('claim'), text, status, version: 1, origin: 'manual', created_at: today(), memo: f.memo?.value.trim() || '',
            ...extra, history: [], experiments: [] };
        }
        await save([{ dir: 'claims', data: c }], `${id ? 'Update' : 'Create'} claim ${c.id}`);
        if (state.tab === 'topics') { state.topics.selected = c.id; renderTopics(); }
        else { state.selectedClaim = c.id; refresh(); showClaim(c.id); }
      } else if (kind === 'experiment') {
        const c = structuredClone(state.claims.get(id));
        c.experiments = [...(c.experiments || []), { title: f.title.value.trim(), status: f.status.value, date: f.date.value, setup: f.setup.value.trim(), result: f.result.value.trim() }];
        await save([{ dir: 'claims', data: c }], `Add experiment to ${id}`);
        showClaim(id);
      }
    } finally {
      if (btn && document.contains(btn)) btn.disabled = false;
    }
  });

  $('#btn-new-claim').onclick = newClaimForm;

  // Import the public reading list (sample papers + authors only, never demo claims or relations)
  $('#btn-import').onclick = async () => {
    if (state.source !== 'github') return;
    const btn = $('#btn-import'); btn.disabled = true;
    try {
      const demo = await loadDemo();
      const items = [
        ...demo.authors.filter(a => !state.authors.has(a.id)).map(data => ({ dir: 'authors', data })),
        ...demo.papers.filter(p => !state.papers.has(p.id)).map(p => ({ dir: 'papers', data: { ...p,
          marks: { liked: false, bookmarked: false, hidden: false }, memo: '', entered_at: today(),
          provenance: { summary_model: 'none', created_at: today() } } })),
      ];
      if (!items.length) { toast('이미 다 들어 있어요.'); return; }
      if (await save(items, `Import reading list (${items.length} files)`)) {
        for (const p of demo.papers) for (const t of p.tags?.topic || []) if (!AXES.topic.values.includes(t)) { AXES.topic.values.push(t); state.filters.topic.add(t); }
        refresh();
      }
    } catch (err) {
      toast(`가져오지 못했어요: ${err.message}`, true);
    } finally { btn.disabled = false; }
  };

  // ---------- connection ----------
  const dialog = $('#connect-dialog');
  $('#btn-demo').onclick = () => useSource('demo');
  $('#btn-connect').onclick = () => {
    const conn = store.get();
    const f = $('#connect-form');
    if (conn) { f.repo.value = conn.repo; f.branch.value = conn.branch; }
    dialog.showModal();
  };
  $('#connect-cancel').onclick = () => dialog.close();
  $('#connect-form').onsubmit = e => {
    e.preventDefault();
    const f = e.target;
    const repo = f.repo.value.trim().replace(/^https:\/\/github\.com\//, '').replace(/\/$/, '');
    if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) { alert('owner/name 형식으로 입력해주세요.'); return; }
    store.set({ repo, branch: f.branch.value.trim() || 'main', token: f.token.value.trim() });
    f.token.value = '';
    dialog.close();
    useSource('github');
  };
  $('#btn-logout').onclick = () => { store.clear(); llmKeys.clearAll(); gh.conn = null; useSource('demo'); };

  // drag the line left of the detail panel to change its width (remembered in this browser)
  const WIDTH_KEY = 'research-dump.widths';
  const widths = (() => { try { return JSON.parse(localStorage.getItem(WIDTH_KEY) || '{}'); } catch { return {}; } })();
  for (const [k, v] of Object.entries(widths)) document.documentElement.style.setProperty(k, `${v}px`);
  for (const bar of document.querySelectorAll('.resizer')) {
    bar.addEventListener('pointerdown', e => {
      e.preventDefault();
      bar.setPointerCapture(e.pointerId);
      bar.classList.add('dragging'); document.body.classList.add('resizing');
      const move = ev => {
        const w = Math.round(Math.min(Math.max(window.innerWidth - ev.clientX, 280), window.innerWidth - 420));
        document.documentElement.style.setProperty(bar.dataset.var, `${w}px`);
        widths[bar.dataset.var] = w;
      };
      const up = () => {
        bar.removeEventListener('pointermove', move); bar.removeEventListener('pointerup', up);
        bar.classList.remove('dragging'); document.body.classList.remove('resizing');
        try { localStorage.setItem(WIDTH_KEY, JSON.stringify(widths)); } catch { /* ignore */ }
        if (cy && state.tab === 'home') { cy.resize(); cy.fit(undefined, 40); }
      };
      bar.addEventListener('pointermove', move); bar.addEventListener('pointerup', up);
    });
  }

  let resizeTimer;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => cy && state.tab === 'home' && renderGraph(), 200); });

  const startTab = ['papers', 'topics'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home';
  setTab(startTab, { push: false });
  useSource(store.get() ? 'github' : 'demo');
})();
