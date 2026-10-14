/* research-dump viewer
 * Data sources: demo (../sample-data) or a private GitHub data repository read with the user's token.
 * The token is kept in this browser only and is sent only to api.github.com.
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
  const STORAGE_KEY = 'research-dump.connection';

  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const $ = sel => document.querySelector(sel);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const today = () => new Date().toISOString().slice(0, 10);
  const newId = prefix => `${prefix}:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
  // File naming convention shared with pipeline/validate.py and sample-data
  const fileFor = (dir, id) => `${dir}/${String(id).replace(/[:/]/g, '_')}.json`;

  // ---------- state ----------
  const state = {
    source: null,             // 'demo' | 'github'
    config: null,
    papers: new Map(),
    authors: new Map(),
    claims: new Map(),
    relations: [],
    filters: {},              // axis -> Set of allowed values
    show: { extends: true, conflicts: true, 'same-author': false, pending: true },
    selectedClaim: null,
  };
  let cy = null;

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
    toastTimer = setTimeout(() => { t.className = 'toast'; }, isError ? 6000 : 2500);
  }

  // ---------- data loading ----------
  const demoBase = () => new URL('../sample-data/', location.href);
  async function demoJSON(p) {
    const r = await fetch(new URL(p, demoBase()));
    if (!r.ok) throw new Error(`${p}: ${r.status}`);
    return r.json();
  }

  async function loadDemo() {
    const index = await demoJSON('index.json');
    const config = await demoJSON('config.json');
    const read = (dir, files) => Promise.all(files.map(f => demoJSON(`${dir}/${f}`)));
    const [papers, authors, relations, claims] = await Promise.all([
      read('papers', index.papers), read('authors', index.authors),
      read('relations', index.relations), read('claims', index.claims),
    ]);
    return { config, papers, authors, relations, claims };
  }

  const gh = {
    conn: null,
    headers() { return { Authorization: `Bearer ${this.conn.token}`, 'X-GitHub-Api-Version': '2022-11-28' }; },
    url(path) { return `https://api.github.com/repos/${this.conn.repo}${path}`; },
    async call(method, path, body) {
      const r = await fetch(this.url(path), {
        method, headers: { ...this.headers(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (r.status === 401 || r.status === 403) throw new Error('토큰 권한을 확인해주세요 (Contents: Read and write).');
      if (r.status === 404) throw new Error('저장소나 branch를 찾을 수 없어요.');
      if (!r.ok) { const e = new Error(`GitHub 응답 ${r.status}`); e.status = r.status; throw e; }
      return r.json();
    },
    async raw(path) {
      const r = await fetch(this.url(`/contents/${path.split('/').map(encodeURIComponent).join('/')}?ref=${encodeURIComponent(this.conn.branch)}`),
        { headers: { ...this.headers(), Accept: 'application/vnd.github.raw+json' } });
      if (!r.ok) throw new Error(`${path}: ${r.status}`);
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

  async function loadGitHub(conn) {
    gh.conn = conn;
    const tree = await gh.call('GET', `/git/trees/${encodeURIComponent(conn.branch)}?recursive=1`);
    const paths = tree.tree.map(t => t.path);
    const targets = paths.filter(p => /^(papers|authors|relations|claims)\/[^/]+\.json$/.test(p));
    const results = {};
    let i = 0;
    const worker = async () => { while (i < targets.length) { const p = targets[i++]; results[p] = await gh.raw(p); } };
    await Promise.all(Array.from({ length: 8 }, worker));
    const pick = dir => targets.filter(p => p.startsWith(dir + '/')).map(p => results[p]);
    const config = paths.includes('config.json') ? await gh.raw('config.json') : null;
    return { config, papers: pick('papers'), authors: pick('authors'), relations: pick('relations'), claims: pick('claims') };
  }

  // ---------- saving ----------
  // items: [{dir, data}] ; updates local state first, then commits (github) or keeps in memory (demo)
  async function save(items, message) {
    for (const { dir, data } of items) {
      if (dir === 'papers') state.papers.set(data.id, data);
      else if (dir === 'authors') state.authors.set(data.id, data);
      else if (dir === 'claims') state.claims.set(data.id, data);
      else if (dir === 'relations') {
        const k = state.relations.findIndex(r => r.id === data.id);
        k >= 0 ? (state.relations[k] = data) : state.relations.push(data);
      }
    }
    if (state.source !== 'github') { toast('데모 모드라서 저장되지 않아요 (새로고침하면 사라져요).'); return true; }
    try {
      await gh.commit(items.map(({ dir, data }) => ({ path: fileFor(dir, data.id), data })), message);
      toast('저장했어요.');
      return true;
    } catch (err) {
      toast(`저장하지 못했어요: ${err.message}`, true);
      return false;
    }
  }

  function refresh() { renderSidebar(); renderGraph(); updateStatus(); }

  function ingest(data) {
    state.config = data.config || { scope: '', topics: [] };
    state.papers = new Map(data.papers.map(p => [p.id, p]));
    state.authors = new Map(data.authors.map(a => [a.id, a]));
    state.claims = new Map(data.claims.map(c => [c.id, c]));
    state.relations = data.relations;
    const topics = new Set(state.config.topics || []);
    for (const p of state.papers.values()) for (const t of p.tags?.topic || []) topics.add(t);
    AXES.topic.values = [...topics];
    state.filters = Object.fromEntries(Object.entries(AXES).map(([k, a]) => [k, new Set(a.values)]));
    state.selectedClaim = null;
  }

  function updateStatus() {
    $('#load-status').textContent = `논문 ${state.papers.size}편 · 연구 주제 ${state.claims.size}개 · 관계 ${state.relations.length}개`;
  }

  async function useSource(kind) {
    const status = $('#load-status');
    status.textContent = '불러오는 중...';
    try {
      const conn = store.get();
      const data = kind === 'github' && conn ? await loadGitHub(conn) : await loadDemo();
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

  // ---------- filtering ----------
  function claimLinks(claimId) {
    return state.relations.filter(r => r.layer === '0-1' && r.to === claimId && r.type !== 'unrelated' && r.status !== 'rejected');
  }

  function visiblePapers() {
    const linked = state.selectedClaim ? new Set(claimLinks(state.selectedClaim).map(r => r.from)) : null;
    return [...state.papers.values()].filter(p => {
      if (linked && !linked.has(p.id)) return false;
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
    // identical tag sets land on one point: fan them out, then push apart pairs that are too close
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
      const cls = ['paper', `m-${p.tags?.main_method || p.tags?.method?.[0] || 'other'}`, `s-${p.status}`];
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
      { selector: 'node.s-new', style: { 'border-color': css('--new'), 'border-width': 4 } },
      { selector: 'node.s-hold', style: { 'border-color': css('--hold'), 'border-width': 3, 'border-style': 'dashed', opacity: 0.75 } },
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
    empty.textContent = state.papers.size ? '조건에 맞는 논문이 없어요.' : '아직 논문이 없어요. 왼쪽에서 읽기 목록을 가져오거나, 수집이 돌기를 기다려주세요.';
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
    parts.push('<span><i class="ring" style="border-color:var(--new)"></i>new</span>');
    parts.push('<span><i class="ring" style="border-color:var(--hold);border-style:dashed"></i>보류</span>');
    if (state.selectedClaim) parts.push(...CLAIM_TYPES.map(t => `<span><i class="ring" style="border-color:var(--${t})"></i>${t}</span>`));
    $('#legend').innerHTML = parts.join('');
  }

  // ---------- sidebar ----------
  function renderSidebar() {
    // claims
    const claims = $('#claims');
    const items = [`<li data-id="" class="${state.selectedClaim ? '' : 'active'}">전체 논문</li>`];
    for (const c of state.claims.values()) {
      const n = claimLinks(c.id).length;
      items.push(`<li data-id="${esc(c.id)}" class="${state.selectedClaim === c.id ? 'active' : ''}">${esc(c.text)}<span class="status">${esc(c.status)} · v${esc(c.version)} · ${n}편</span></li>`);
    }
    claims.innerHTML = items.join('');
    claims.onclick = e => {
      const li = e.target.closest('li'); if (!li) return;
      state.selectedClaim = li.dataset.id || null;
      renderSidebar(); renderGraph();
      state.selectedClaim ? showClaim(state.selectedClaim) : showEmptyDetail();
    };

    // relation toggles
    const rel = [
      ['extends', 'extends', 'var(--extends)', ''],
      ['conflicts', 'conflicts', 'var(--conflicts)', 'dash'],
      ['same-author', 'same-author (저자 노드)', 'var(--author)', ''],
      ['pending', '승인 전 관계(new) 보기', 'var(--quiet)', 'dash'],
    ];
    $('#relation-toggles').innerHTML = rel.map(([k, label, color, dash]) =>
      `<label><input type="checkbox" data-k="${k}" ${state.show[k] ? 'checked' : ''}><i class="swatch ${dash}" style="border-color:${color}"></i>${label}</label>`).join('');
    $('#relation-toggles').onchange = e => { state.show[e.target.dataset.k] = e.target.checked; renderGraph(); };

    // tag filters
    const counts = {};
    for (const p of state.papers.values()) for (const [axis, def] of Object.entries(AXES)) {
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

  // ---------- detail panel ----------
  const field = (label, text) => `<div class="field"><b>${label}</b>${text ? `<p>${esc(text)}</p>` : '<p class="empty-text">아직 없음</p>'}</div>`;
  const nameOf = id => state.papers.get(id)?.title || state.claims.get(id)?.text || id;
  const options = (values, selected) => values.map(v => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(v)}</option>`).join('');

  function showEmptyDetail() {
    $('#detail').innerHTML = '<p class="hint">논문 노드를 누르거나, 왼쪽에서 연구 주제를 고르면 여기에 내용이 보여요.</p>';
  }

  function relCard(r, otherId) {
    const target = state.papers.has(otherId) ? `<a href="#" data-paper="${esc(otherId)}">${esc(nameOf(otherId))}</a>` : esc(nameOf(otherId));
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
      <div class="meta">${esc(p.venue || '')} ${esc(p.year || '')}${authors.length ? ` · ${esc(authors.join(', '))}` : ''}</div>
      <div class="chips"><span class="badge ${esc(p.status)}">${esc(p.status)}</span>${chips.map(c => `<span class="chip">${esc(c)}</span>`).join('')}</div>
      <div class="actions">
        <button type="button" data-action="paper-confirm" data-id="${esc(id)}" ${p.status === 'confirmed' ? 'disabled' : ''}>확인 완료</button>
        <button type="button" data-action="paper-hold" data-id="${esc(id)}" ${p.status === 'hold' ? 'disabled' : ''}>보류</button>
      </div>
      ${pv.screen_reason ? `<p class="meta">스크리닝: ${esc(pv.screen_reason)}</p>` : ''}
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

  // relation type choices depend on whether the target is a claim or a paper
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

  function claimForm(c) {
    return `
      <form class="edit" data-form="claim" data-id="${esc(c?.id || '')}">
        <label>Claim (한 줄)<textarea name="text" rows="2" required>${esc(c?.text)}</textarea></label>
        <label>상태<select name="status">${options(CLAIM_STATUS, c?.status || 'candidate')}</select></label>
        ${c ? '<label>바꾼 이유 (문장을 바꿀 때만)<input name="reason"></label>' : ''}
        <div class="actions"><button type="submit">${c ? '저장' : '만들기'}</button><button type="button" class="quiet" data-action="claim-cancel" data-id="${esc(c?.id || '')}">취소</button></div>
      </form>`;
  }

  function showClaim(id, mode = 'view') {
    const c = state.claims.get(id); if (!c) return;
    if (mode === 'edit') { $('#detail').innerHTML = `<h3>연구 주제 수정</h3><p class="meta">문장을 바꾸면 v${c.version + 1}이 되고, 지금 문장은 버전 기록에 남아요.</p>${claimForm(c)}`; return; }
    const links = state.relations.filter(r => r.layer === '0-1' && r.to === id && r.status !== 'rejected');
    const dup = links.filter(r => r.type === 'duplicate');
    $('#detail').innerHTML = `
      <h3>${esc(c.text)}</h3>
      <div class="meta">${esc(c.status)} · v${esc(c.version)} <button type="button" class="mini" data-action="claim-edit" data-id="${esc(id)}">수정</button></div>
      ${dup.length ? `<p class="rel duplicate"><b>duplicate ${dup.length}건</b>: 이미 이 주제를 다룬 논문이 있을 수 있어요.</p>` : ''}
      <h4>연결된 논문</h4>
      ${links.map(r => relCard(r, r.from)).join('') || '<p class="hint">아직 없어요. 논문을 눌러 "관계 직접 추가"로 연결할 수 있어요.</p>'}
      <h4>버전 기록</h4>
      ${(c.history || []).slice().reverse().map(h => `<div class="rel"><b>v${esc(h.version)}</b> ${esc(h.text)}<p class="meta">${esc(h.changed_at || '')} ${esc(h.reason || '')}</p></div>`).join('') || '<p class="hint">이전 버전 없음</p>'}
      <h4>내 실험</h4>
      ${(c.experiments || []).map(x => `<div class="rel"><b>${esc(x.title)}</b> <span class="meta">${esc(x.date || '')}</span>${field('설정', x.setup)}${field('결과', x.result)}</div>`).join('') || '<p class="hint">기록된 실험 없음</p>'}
      <details class="add-rel">
        <summary>실험 추가</summary>
        <form class="edit" data-form="experiment" data-id="${esc(id)}">
          <label>제목<input name="title" required></label>
          <label>날짜<input name="date" type="date" value="${today()}"></label>
          <label>설정<textarea name="setup" rows="3"></textarea></label>
          <label>결과<textarea name="result" rows="3"></textarea></label>
          <div class="actions"><button type="submit">추가</button></div>
        </form>
      </details>
    `;
  }

  // ---------- actions ----------
  async function setPaperStatus(id, status) {
    const p = structuredClone(state.papers.get(id));
    p.status = status;
    p.provenance = { ...(p.provenance || {}), ...(status === 'confirmed' ? { confirmed_at: today() } : {}) };
    await save([{ dir: 'papers', data: p }], `${status === 'confirmed' ? 'Confirm' : 'Hold'} ${id}`);
    refresh(); showPaper(id);
  }

  async function setRelationStatus(id, status) {
    const r = structuredClone(state.relations.find(x => x.id === id));
    r.status = status;
    await save([{ dir: 'relations', data: r }], `${status === 'approved' ? 'Approve' : 'Reject'} ${id}`);
    refresh();
    state.papers.has(r.from) && !state.selectedClaim ? showPaper(r.from) : showClaim(r.to);
  }

  $('#detail').addEventListener('click', e => {
    const link = e.target.closest('a[data-paper]');
    if (link) {
      e.preventDefault();
      const id = link.dataset.paper;
      showPaper(id);
      if (cy) { cy.$(':selected').unselect(); const n = cy.getElementById(id); if (n.nonempty()) { n.select(); cy.animate({ center: { eles: n } }, { duration: 250 }); } }
      return;
    }
    const b = e.target.closest('button[data-action]'); if (!b) return;
    const { action, id } = b.dataset;
    if (action === 'paper-confirm') setPaperStatus(id, 'confirmed');
    else if (action === 'paper-hold') setPaperStatus(id, 'hold');
    else if (action === 'summary-edit') showPaper(id, true);
    else if (action === 'paper-view') showPaper(id);
    else if (action === 'rel-approve') setRelationStatus(id, 'approved');
    else if (action === 'rel-reject') setRelationStatus(id, 'rejected');
    else if (action === 'claim-edit') showClaim(id, 'edit');
    else if (action === 'claim-cancel') id ? showClaim(id) : showEmptyDetail();
  });

  $('#detail').addEventListener('change', e => { if (e.target.name === 'to') syncRelationTypes(); });

  $('#detail').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target, id = f.dataset.id, kind = f.dataset.form;
    const btn = f.querySelector('button[type="submit"]'); if (btn) btn.disabled = true;
    try {
      if (kind === 'summary') {
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
        let c;
        if (id) {
          c = structuredClone(state.claims.get(id));
          if (text !== c.text) {
            c.history = [...(c.history || []), { version: c.version, text: c.text, changed_at: today(), reason: f.reason?.value.trim() || '' }];
            c.version += 1; c.text = text;
          }
          c.status = status;
        } else {
          c = { id: newId('claim'), text, status, version: 1, history: [], experiments: [] };
        }
        await save([{ dir: 'claims', data: c }], `${id ? 'Update' : 'Create'} claim ${c.id}`);
        state.selectedClaim = c.id;
        refresh(); showClaim(c.id);
      } else if (kind === 'experiment') {
        const c = structuredClone(state.claims.get(id));
        c.experiments = [...(c.experiments || []), { title: f.title.value.trim(), date: f.date.value, setup: f.setup.value.trim(), result: f.result.value.trim() }];
        await save([{ dir: 'claims', data: c }], `Add experiment to ${id}`);
        showClaim(id);
      }
    } finally {
      if (btn && document.contains(btn)) btn.disabled = false;
    }
  });

  $('#btn-new-claim').onclick = () => { $('#detail').innerHTML = `<h3>새 연구 주제</h3>${claimForm(null)}`; };

  // Import the public reading list (sample papers + authors only, never demo claims or relations)
  $('#btn-import').onclick = async () => {
    if (state.source !== 'github') return;
    const btn = $('#btn-import'); btn.disabled = true;
    try {
      const index = await demoJSON('index.json');
      const [papers, authors] = await Promise.all([
        Promise.all(index.papers.map(f => demoJSON(`papers/${f}`))),
        Promise.all(index.authors.map(f => demoJSON(`authors/${f}`))),
      ]);
      const items = [
        ...authors.filter(a => !state.authors.has(a.id)).map(data => ({ dir: 'authors', data })),
        ...papers.filter(p => !state.papers.has(p.id)).map(p => ({ dir: 'papers', data: { ...p, status: 'new', provenance: { summary_model: 'none', created_at: today() } } })),
      ];
      if (!items.length) { toast('이미 다 들어 있어요.'); return; }
      if (await save(items, `Import reading list (${items.length} files)`)) {
        for (const p of papers) for (const t of p.tags?.topic || []) if (!AXES.topic.values.includes(t)) { AXES.topic.values.push(t); state.filters.topic.add(t); }
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
  $('#btn-logout').onclick = () => { store.clear(); gh.conn = null; useSource('demo'); };

  let resizeTimer;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => cy && renderGraph(), 200); });

  useSource(store.get() ? 'github' : 'demo');
})();
