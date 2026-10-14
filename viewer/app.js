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
  const STORAGE_KEY = 'research-dump.connection';

  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const $ = sel => document.querySelector(sel);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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

  // ---------- data loading ----------
  async function loadDemo() {
    const base = new URL('../sample-data/', location.href);
    const getJSON = async p => {
      const r = await fetch(new URL(p, base));
      if (!r.ok) throw new Error(`${p}: ${r.status}`);
      return r.json();
    };
    const index = await getJSON('index.json');
    const config = await getJSON('config.json');
    const read = (dir, files) => Promise.all(files.map(f => getJSON(`${dir}/${f}`)));
    const [papers, authors, relations, claims] = await Promise.all([
      read('papers', index.papers), read('authors', index.authors),
      read('relations', index.relations), read('claims', index.claims),
    ]);
    return { config, papers, authors, relations, claims };
  }

  async function loadGitHub({ repo, branch, token }) {
    const api = `https://api.github.com/repos/${repo}`;
    const headers = { Authorization: `Bearer ${token}`, 'X-GitHub-Api-Version': '2022-11-28' };
    const tree = await fetch(`${api}/git/trees/${encodeURIComponent(branch)}?recursive=1`, { headers });
    if (tree.status === 401 || tree.status === 403) throw new Error('토큰 권한을 확인해주세요 (Contents: Read).');
    if (tree.status === 404) throw new Error('저장소나 branch를 찾을 수 없어요.');
    if (!tree.ok) throw new Error(`GitHub 응답 ${tree.status}`);
    const paths = (await tree.json()).tree.map(t => t.path);
    const want = p => /^(papers|authors|relations|claims)\/[^/]+\.json$/.test(p);
    const raw = { ...headers, Accept: 'application/vnd.github.raw+json' };
    const getJSON = async p => {
      const r = await fetch(`${api}/contents/${p.split('/').map(encodeURIComponent).join('/')}?ref=${encodeURIComponent(branch)}`, { headers: raw });
      if (!r.ok) throw new Error(`${p}: ${r.status}`);
      return r.json();
    };
    const targets = paths.filter(want);
    const results = {};
    let i = 0;
    const worker = async () => {
      while (i < targets.length) {
        const p = targets[i++];
        results[p] = await getJSON(p);
      }
    };
    await Promise.all(Array.from({ length: 8 }, worker));
    const pick = dir => targets.filter(p => p.startsWith(dir + '/')).map(p => results[p]);
    const config = paths.includes('config.json') ? await getJSON('config.json') : null;
    return { config, papers: pick('papers'), authors: pick('authors'), relations: pick('relations'), claims: pick('claims') };
  }

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

  async function useSource(kind) {
    const status = $('#load-status');
    status.textContent = '불러오는 중...';
    try {
      const conn = store.get();
      const data = kind === 'github' && conn ? await loadGitHub(conn) : await loadDemo();
      state.source = kind === 'github' && conn ? 'github' : 'demo';
      ingest(data);
      $('#source-label').textContent = state.source === 'github' ? `저장소: ${conn.repo}` : '데모 데이터 (공개 논문만)';
      $('#scope').textContent = state.config.scope ? `연구 스콥 · ${state.config.scope}` : '';
      status.textContent = `논문 ${state.papers.size}편 · 연구 주제 ${state.claims.size}개 · 관계 ${state.relations.length}개`;
      renderSidebar();
      renderGraph();
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
    $('#empty').hidden = els.some(e => e.group === 'nodes');
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

  function showEmptyDetail() {
    $('#detail').innerHTML = '<p class="hint">논문 노드를 누르거나, 왼쪽에서 연구 주제를 고르면 여기에 내용이 보여요.</p>';
  }

  function relCard(r, otherId) {
    const target = state.papers.has(otherId) ? `<a href="#" data-paper="${esc(otherId)}">${esc(nameOf(otherId))}</a>` : esc(nameOf(otherId));
    return `<div class="rel ${esc(r.type)}">
      <div><span class="type">${esc(r.type)}</span> <span class="badge ${esc(r.status)}">${esc(r.status)}</span></div>
      <div>${target}</div>
      <p class="meta">${esc(r.reason)}${r.model ? ` · ${esc(r.model)}` : ''}</p>
    </div>`;
  }

  function showPaper(id) {
    const p = state.papers.get(id); if (!p) return;
    const t = p.tags || {};
    const chips = [...(t.method || []).map(m => (m === t.main_method ? `★ ${m}` : m)), ...(t.goal || []), t.modal, t.setting, ...(t.topic || [])].filter(Boolean);
    const authors = (p.authors || []).map(a => state.authors.get(a)?.name).filter(Boolean);
    const out = state.relations.filter(r => r.from === id && r.type !== 'unrelated');
    const inc = state.relations.filter(r => r.to === id && r.layer === '0');
    $('#detail').innerHTML = `
      <h3>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}</h3>
      <div class="meta">${esc(p.venue || '')} ${esc(p.year || '')}${authors.length ? ` · ${esc(authors.join(', '))}` : ''}</div>
      <div class="chips"><span class="badge ${esc(p.status)}">${esc(p.status)}</span>${chips.map(c => `<span class="chip">${esc(c)}</span>`).join('')}</div>
      <h4>요약</h4>
      ${field('주장', p.summary?.claim)}${field('조건', p.summary?.condition)}${field('빈 곳', p.summary?.gap)}
      <p class="meta">요약: ${esc(p.provenance?.summary_model || '-')}</p>
      <div class="actions">
        <button type="button" disabled title="다음 단계에서 연결돼요">확인 완료</button>
        <button type="button" disabled title="다음 단계에서 연결돼요">보류</button>
      </div>
      <h4>관계</h4>
      ${[...out.map(r => relCard(r, r.to)), ...inc.map(r => relCard(r, r.from))].join('') || '<p class="hint">아직 관계가 없어요.</p>'}
    `;
  }

  function showAuthor(id) {
    const a = state.authors.get(id); if (!a) return;
    const mine = [...state.papers.values()].filter(p => (p.authors || []).includes(id));
    $('#detail').innerHTML = `<h3>${esc(a.name)}</h3><div class="meta">${esc(a.affiliation || '')}</div>
      <h4>논문 ${mine.length}편</h4>${mine.map(p => `<div class="rel"><a href="#" data-paper="${esc(p.id)}">${esc(p.title)}</a></div>`).join('')}`;
  }

  function showClaim(id) {
    const c = state.claims.get(id); if (!c) return;
    const links = state.relations.filter(r => r.layer === '0-1' && r.to === id && r.status !== 'rejected');
    const dup = links.filter(r => r.type === 'duplicate');
    $('#detail').innerHTML = `
      <h3>${esc(c.text)}</h3>
      <div class="meta">${esc(c.status)} · v${esc(c.version)}</div>
      ${dup.length ? `<p class="rel duplicate"><b>duplicate ${dup.length}건</b>: 이미 이 주제를 다룬 논문이 있을 수 있어요.</p>` : ''}
      <h4>연결된 논문</h4>
      ${links.map(r => relCard(r, r.from)).join('') || '<p class="hint">아직 없어요.</p>'}
      <h4>버전 기록</h4>
      ${(c.history || []).map(h => `<div class="rel"><b>v${esc(h.version)}</b> ${esc(h.text)}<p class="meta">${esc(h.changed_at || '')} ${esc(h.reason || '')}</p></div>`).join('') || '<p class="hint">이전 버전 없음</p>'}
      <h4>내 실험</h4>
      ${(c.experiments || []).map(x => `<div class="rel"><b>${esc(x.title)}</b> <span class="meta">${esc(x.date || '')}</span>${field('설정', x.setup)}${field('결과', x.result)}</div>`).join('') || '<p class="hint">기록된 실험 없음</p>'}
    `;
  }

  $('#detail').addEventListener('click', e => {
    const a = e.target.closest('a[data-paper]'); if (!a) return;
    e.preventDefault();
    const id = a.dataset.paper;
    showPaper(id);
    if (cy) { cy.$(':selected').unselect(); const n = cy.getElementById(id); if (n.nonempty()) { n.select(); cy.animate({ center: { eles: n } }, { duration: 250 }); } }
  });

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
  $('#btn-logout').onclick = () => { store.clear(); useSource('demo'); };

  let resizeTimer;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => cy && renderGraph(), 200); });

  useSource(store.get() ? 'github' : 'demo');
})();
