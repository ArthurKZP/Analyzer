"""Page « Mains de départ » : ce que rapporte chaque main, en tout, par position et à chaque décision préflop,
comparé au fold et à la théorie, puis d'où vient la perte d'une main et les coups à revoir (analyzer/handplay.py)."""
from __future__ import annotations

import json

from .. import handplay
from ..report import FORMAT_NAMES, html_page


STYLE = """<style>
:root { --gain: 27, 175, 122; --perte: 208, 59, 59; --vide: #ecebe6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --gain: 25, 158, 112; --perte: 230, 103, 103; --vide: #242423; } }
:root[data-theme="dark"] { --gain: 25, 158, 112; --perte: 230, 103, 103; --vide: #242423; }
.hp-filters { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; }
.hp-row { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.hp-row > span { font-size: 12px; color: var(--muted); min-width: 92px; }
.hp-row button, .hp-chips button { font: inherit; font-size: 13px; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); color: var(--ink-2); cursor: pointer; }
.hp-row button[aria-pressed="true"], .hp-chips button[aria-pressed="true"] { background: var(--ink); color: var(--page); border-color: var(--ink); }
.hp-chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 8px 0; }
.hp-sum { font-size: 14px; margin: 6px 0 10px; }
.hp-layout { display: grid; grid-template-columns: minmax(0, 11fr) minmax(0, 9fr); gap: 16px; align-items: start; }
.hp-prob { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; margin-bottom: 16px; }
.hp-two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; }
@media (max-width: 980px) { .hp-layout, .hp-prob, .hp-two { grid-template-columns: minmax(0, 1fr); } }
.hp-grid { display: grid; grid-template-columns: repeat(13, minmax(30px, 1fr)); gap: 2px; min-width: 430px; }
.hp-wrap { overflow-x: auto; }
.hp-c { position: relative; aspect-ratio: 1.2; border-radius: 3px; background: var(--vide); font-size: 11px; line-height: 1.1;
  overflow: hidden; cursor: pointer; border: 0; padding: 0; font: inherit; text-align: left; }
.hp-c.empty { cursor: default; }
.hp-c.sel { outline: 2px solid var(--ink); outline-offset: -1px; }
.hp-c .k { position: absolute; left: 3px; top: 2px; font-size: 11px; font-weight: 600; color: var(--ink); }
.hp-c .v { position: absolute; left: 3px; bottom: 2px; font-size: 10px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--ink); }
.hp-c .n { position: absolute; right: 3px; top: 2px; font-size: 9px; color: var(--ink-2); }
.hp-c.empty .k { color: var(--muted); font-weight: 400; }
.hp-legend { font-size: 12px; color: var(--ink-2); margin-top: 8px; }
table.hp-t td, table.hp-t th { white-space: nowrap; }
table.hp-t td.why { white-space: normal; min-width: 220px; font-size: 12px; color: var(--ink-2); }
table.hp-t tbody tr.pick { cursor: pointer; }
table.hp-t tbody tr.pick:hover td { background: var(--hi-bg); }
table.hp-t tbody tr.sel td { background: var(--lo-bg); }
.hp-good { color: var(--good); font-weight: 600; } .hp-bad { color: var(--alert); font-weight: 600; }
.hp-more { font: inherit; font-size: 12px; margin-top: 6px; border: 1px solid var(--border); background: var(--surface);
  border-radius: 6px; padding: 2px 10px; cursor: pointer; color: var(--ink-2); }
.hp-plist { list-style: none; padding: 0; margin: 0; }
.hp-plist li { padding: 6px 4px; border-top: 1px solid var(--border); cursor: pointer; font-size: 13px; }
.hp-plist li:first-child { border-top: 0; }
.hp-plist li:hover { background: var(--hi-bg); }
.hp-plist .d { display: block; font-size: 12px; color: var(--ink-2); }
.hp-detail { margin-bottom: 16px; border-color: var(--ink-2); }
.hp-detail h3 { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-top: 0; }
.hp-close { font: inherit; font-size: 13px; border: 1px solid var(--border); background: var(--surface); border-radius: 6px;
  padding: 1px 9px; cursor: pointer; color: var(--ink-2); }
.hp-diag { margin: 8px 0 12px; padding-left: 18px; font-size: 13px; }
.hp-diag li { margin: 3px 0; }
.hp-bar { display: inline-block; height: 9px; border-radius: 2px; vertical-align: middle; margin-left: 6px; }
.hp-detail h4 { margin: 16px 0 6px; font-size: 13px; }
.hp-review { list-style: none; padding: 0; margin: 0; font-size: 13px; }
.hp-review li { padding: 5px 0; border-top: 1px solid var(--border); }
.hp-review li:first-child { border-top: 0; }
.hp-review a { margin-left: 8px; }
.hp-review .d { color: var(--ink-2); font-size: 12px; }
</style>"""

SCRIPT = r"""
(function () {
  const DATA = JSON.parse(document.getElementById('hp-data').textContent);
  const RANKS = 'AKQJT98765432';
  const MIN = 8;  // mains minimum pour un verdict
  const fmts = Object.keys(DATA.formats);
  const st = { fmt: fmts[0], kind: '', pos: '', sit: 'all', measure: 'ev', more: false, sel: '', act: '', pot: '' };
  const $ = (id) => document.getElementById(id);
  const el = (tag, attrs, ...kids) => {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => { if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v); });
    kids.flat().forEach((c) => { if (c !== null && c !== undefined && c !== '') n.append(c instanceof Node ? c : String(c)); });
    return n;
  };
  const num = (x, d) => (Math.round(x * 10 ** d) / 10 ** d).toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d }).replace('-', '−');
  const signed = (x, d) => (x > 0 ? '+' : '') + num(x, d);
  const pct = (x) => Math.round(100 * x) + ' %';
  const handAt = (i, j) => (i === j ? RANKS[i] + RANKS[i] : i < j ? RANKS[i] + RANKS[j] + 's' : RANKS[j] + RANKS[i] + 'o');
  const SIT = Object.fromEntries(DATA.situations);
  const POT = Object.fromEntries(DATA.pots), END = Object.fromEntries(DATA.ends), FLOP = Object.fromEntries(DATA.flops);
  const FMT = DATA.names;
  const FOLD_TEXT = { open: 'le fold de la position (0 ou la blinde déjà posée)', vs_open: 'le fold (ta blinde, ou rien)',
    vs_3bet: 'le fold (ton open perdu)', vs_4bet: 'le fold (ton 3bet perdu)', vs_limp: 'le fold' };
  // groupes d'actions : agressif (relance, tapis), passif (call, check), fold
  const GROUP = { raise: 'agg', allin: 'agg', call: 'pas', check: 'pas', fold: 'fold' };
  const ACTS = { agg: ['raise', 'allin'], pas: ['call', 'check'], fold: ['fold'] };
  const WORD = { open: { agg: 'relance', pas: 'limp' }, vs_limp: { agg: 'relance', pas: 'check / limp' },
    vs_open: { agg: '3bet', pas: 'call' }, vs_3bet: { agg: '4bet', pas: 'call' }, vs_4bet: { agg: '5bet / tapis', pas: 'call' } };
  const word = (sit, g) => (g === 'fold' ? 'fold' : (WORD[sit] || {})[g] || g);
  const NEXT = DATA.next;  // la décision suivante quand on relance
  const FLOP_POTS = DATA.flop_pots;

  // Familles de mains : plus de mains par case, des verdicts plus sûrs.
  function family(h) {
    const r = (c) => RANKS.indexOf(c), hi = h[0], lo = h[1], s = h[2] === 's';
    if (h.length === 2) return r(hi) <= r('T') ? 'Paires hautes (TT+)' : r(hi) <= r('6') ? 'Paires moyennes (66-99)' : 'Petites paires (22-55)';
    if (hi === 'A') return s ? 'As assortis' : 'As dépareillés';
    if (r(lo) <= r('T')) return s ? 'Broadways assortis' : 'Broadways dépareillés';
    if (s && r(lo) - r(hi) <= 2) return 'Connecteurs assortis';
    return s ? 'Autres assorties' : 'Autres dépareillées';
  }
  const isCombo = (name) => /^[AKQJT2-9]{2}[so]?$/.test(name);
  const inSel = (h, name) => (isCombo(name) ? h === name : family(h) === name);

  // Les clés « position|situation|action|type » retenues par les filtres (et, au besoin, une liste d'actions).
  function keys(table, sit, acts) {
    return Object.keys(table).filter((key) => {
      const [pos, s, act, kind] = key.split('|');
      return (!st.pos || pos === st.pos) && s === sit && acts.includes(act) && (!st.kind || kind === st.kind);
    });
  }
  const zero = () => [0, 0, 0, 0, 0, 0, 0, 0];
  const addTo = (o, c) => { c.forEach((v, k) => { o[k] += v; }); return o; };
  const merge = (list) => list.reduce(addTo, zero());
  function cellsFor(acts, sit) {
    const out = {};
    const data = DATA.formats[st.fmt].cells;
    keys(data, sit || st.sit, acts).forEach((key) => {
      Object.entries(data[key]).forEach(([h, c]) => addTo(out[h] || (out[h] = zero()), c));
    });
    return out;
  }
  function stat(c) {
    if (!c || !c[0]) return null;
    const n = c[0], s = st.measure === 'ev' ? c[3] : c[1], s2 = st.measure === 'ev' ? c[4] : c[2];
    const mean = s / n, v = n > 1 ? Math.max((s2 - s * s / n) / (n - 1), 0) : 0;
    return { n, total: s, mean, se: Math.sqrt(v / n), fold: c[5] / n, theory: c[7] ? c[6] / c[7] : null,
      loss: c[7] ? c[6] / c[7] : null, analysed: c[7] };
  }
  const decision = () => st.sit !== 'all';
  const value = (x) => (decision() ? x.mean - x.fold : x.mean);  // gain par rapport au fold, ou résultat par main
  function verdict(x) {
    if (!x || x.n < MIN) return ['peu de mains', ''];
    const v = value(x), ci = 1.96 * x.se;
    if (v - ci > 0) return [decision() ? 'mieux que le fold' : 'gagnante', 'hp-good'];
    if (v + ci < 0) return [decision() ? 'pire que le fold' : 'perdante', 'hp-bad'];
    return ['pas tranché', ''];
  }
  function advice(x, fold) {
    if (!decision()) return '';
    const parts = [];
    if (x && x.theory !== null) {
      const t = Math.round(100 * x.theory);
      const bad = x.n >= MIN && value(x) + 1.96 * x.se < 0;
      if (t < 10) parts.push('la théorie ne joue presque jamais ainsi (' + t + ' %)' + (bad ? ' : à couper' : ''));
      else if (bad) parts.push('la théorie le joue (' + t + ' %) : la perte vient de la suite du coup (clique la main), ou de la variance');
      else parts.push('la théorie le joue ' + t + ' % du temps');
    }
    if (fold && fold.n && fold.theory !== null && fold.theory < 0.5) {
      parts.push('foldée ' + fold.n + ' fois alors que la théorie la joue ' + Math.round(100 * (1 - fold.theory)) + ' % du temps');
    }
    return parts.join(' ; ');
  }

  // ---------- état dans l'adresse (#pos=BTN&sit=open&main=K9s…) : les liens du leakfinding y mènent ----------
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    if (p.get('fmt') && DATA.formats[p.get('fmt')]) st.fmt = p.get('fmt');
    ['kind', 'pos', 'act', 'measure'].forEach((k) => { if (p.has(k)) st[k] = p.get(k); });
    if (p.get('sit') && (p.get('sit') === 'all' || SIT[p.get('sit')])) st.sit = p.get('sit');
    st.sel = p.get('main') || '';
    st.pot = '';
  }
  function writeHash() {
    const p = new URLSearchParams();
    if (fmts.length > 1) p.set('fmt', st.fmt);
    if (st.kind) p.set('kind', st.kind);
    if (st.pos) p.set('pos', st.pos);
    if (st.sit !== 'all') p.set('sit', st.sit);
    if (st.sel) p.set('main', st.sel);
    if (st.sel && st.act) p.set('act', st.act);
    const hash = p.toString();
    history.replaceState(null, '', hash ? '#' + hash : location.pathname + location.search);
  }
  function select(name, extra) {
    Object.assign(st, { sel: name, act: '', pot: '' }, extra || {});
    render();
    const box = $('hp-detail');
    if (box && !box.hidden) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function buttons(id, label, options, key) {
    const row = $(id);
    row.textContent = '';
    if (!options.length) { row.hidden = true; return; }
    row.hidden = false;
    row.append(el('span', {}, label));
    options.forEach(([v, text]) => {
      const b = el('button', { type: 'button', 'aria-pressed': String(st[key] === v) }, text);
      b.addEventListener('click', () => { st[key] = v; st.more = false; st.act = ''; st.pot = ''; render(); });
      row.append(b);
    });
  }

  function render() {
    const f = DATA.formats[st.fmt];
    buttons('hp-fmt', 'Format', fmts.length > 1 ? fmts.map((x) => [x, FMT[x] || x]) : [], 'fmt');
    buttons('hp-kind', 'Adversaires', [['', 'Tous'], ['reg', 'Réguliers'], ['rec', 'Récréatifs']], 'kind');
    if (st.pos && !f.positions.includes(st.pos)) st.pos = '';
    buttons('hp-pos', 'Position', [['', 'Toutes']].concat(f.positions.map((p) => [p, p])), 'pos');
    buttons('hp-sit', 'Décision', [['all', 'Toutes les mains']].concat(DATA.situations.filter(([k]) => k !== 'vs_limp' || st.fmt !== 'HU')), 'sit');
    buttons('hp-measure', 'Résultat', [['ev', 'EV all-in'], ['net', 'Réel']], 'measure');
    writeHash();

    const played = decision() ? ['raise', 'allin', 'call', 'check'] : ['all'];
    const cells = cellsFor(played), folds = decision() ? cellsFor(['fold']) : {};
    const all = stat(merge(Object.values(cells)));
    const foldAll = stat(merge(Object.values(folds)));
    const sum = $('hp-sum');
    sum.textContent = '';
    if (!all) {
      sum.append('Aucune main pour ces filtres.');
    } else if (decision()) {
      sum.append(el('b', {}, all.n + ' fois jouée'), ' (' + (foldAll ? foldAll.n : 0) + ' fois foldée) · '
        + signed(all.mean, 2) + ' bb par main jouée, contre ' + signed(all.fold, 2) + ' bb pour ' + FOLD_TEXT[st.sit]
        + ' : ', el('b', { class: value(all) >= 0 ? 'hp-good' : 'hp-bad' }, signed(100 * value(all), 0) + ' bb/100 par rapport au fold'),
        ' (± ' + num(196 * all.se, 0) + ').');
    } else {
      sum.append(el('b', {}, all.n + ' mains'), ' · ' + signed(all.total, 1) + ' bb · ',
        el('b', { class: all.mean >= 0 ? 'hp-good' : 'hp-bad' }, signed(100 * all.mean, 1) + ' bb/100'), ' (± ' + num(196 * all.se, 0) + ').');
      if (!st.pos && f.positions.length > 1) {  // et par position
        const saved = st.pos;
        const parts = f.positions.map((pos) => { st.pos = pos; const x = stat(merge(Object.values(cellsFor(['all'])))); return [pos, x]; });
        st.pos = saved;
        sum.append(el('br'), 'Par position : ', ...parts.filter(([, x]) => x).map(([pos, x], k) => [k ? ' · ' : '', el('b', {}, pos), ' '
          + signed(100 * x.mean, 1) + ' bb/100 (' + x.n + ' mains)']).flat());
      }
    }

    problems();
    detail();

    // grille 13 × 13
    const grid = $('hp-grid');
    grid.textContent = '';
    const scale = decision() ? 4 : 3;
    for (let i = 0; i < 13; i++) for (let j = 0; j < 13; j++) {
      const h = handAt(i, j), x = stat(cells[h]), fx = stat(folds[h]);
      const c = el(x || fx ? 'button' : 'div', { type: x || fx ? 'button' : null,
        class: 'hp-c' + (x ? '' : ' empty') + (st.sel && inSel(h, st.sel) ? ' sel' : '') }, el('span', { class: 'k' }, h));
      if (x) {
        const v = value(x), a = Math.min(Math.abs(v) / scale, 1) * (x.n >= MIN ? 0.85 : 0.35) + 0.08;
        c.style.background = 'rgba(var(' + (v >= 0 ? '--gain' : '--perte') + '), ' + a.toFixed(2) + ')';
        c.append(el('span', { class: 'v' }, signed(v, 1)), el('span', { class: 'n' }, x.n));
        c.title = h + ' : ' + x.n + ' fois, ' + signed(x.mean, 2) + ' bb par main (± ' + num(1.96 * x.se, 1) + ')'
          + (decision() ? ', fold : ' + signed(x.fold, 2) + ' bb → ' + signed(v, 2) + ' bb par rapport au fold' : '')
          + (fx ? '\nfoldée ' + fx.n + ' fois' : '') + (decision() && advice(x, fx) ? '\n' + advice(x, fx) : '')
          + '\nClique : d\'où vient le résultat';
      } else if (fx) {
        c.append(el('span', { class: 'n' }, 'f' + fx.n));
        c.title = h + ' : foldée ' + fx.n + ' fois' + (fx.theory !== null ? ' (la théorie folde ' + Math.round(100 * fx.theory) + ' %)' : '');
      }
      if (x || fx) c.addEventListener('click', () => select(h));
      grid.append(c);
    }
    $('hp-legend').textContent = decision()
      ? 'Couleur et nombre : bb gagnés par main jouée, par rapport au fold à ce moment (vert : mieux que le fold). Pâle : moins de '
        + MIN + ' mains. En haut à droite : mains jouées (fN : seulement foldée N fois). Clique une main pour voir d\'où vient son résultat.'
      : 'Couleur et nombre : bb gagnés par main (vert : gagnante). Pâle : moins de ' + MIN + ' mains. En haut à droite : mains. '
        + 'Clique une main pour voir d\'où vient son résultat.';

    // tableaux : par main, par famille
    const rows = Object.entries(cells).map(([h, c]) => [h, stat(c), stat(folds[h])]);
    Object.entries(folds).forEach(([h, c]) => { if (!cells[h]) rows.push([h, null, stat(c)]); });
    rows.sort((a, b) => impact(a) - impact(b));
    table('hp-hands', rows, st.more ? rows.length : 25, 'Main', false);
    const fams = {};
    Object.entries(cells).forEach(([h, c]) => { (fams[family(h)] = fams[family(h)] || [[], []])[0].push(c); });
    Object.entries(folds).forEach(([h, c]) => { (fams[family(h)] = fams[family(h)] || [[], []])[1].push(c); });
    const famRows = Object.entries(fams).map(([k, [p, fo]]) => [k, p.length ? stat(merge(p)) : null, fo.length ? stat(merge(fo)) : null]);
    famRows.sort((a, b) => impact(a) - impact(b));
    table('hp-fams', famRows, famRows.length, 'Famille', true);
    const more = $('hp-more');
    more.hidden = rows.length <= 25;
    more.textContent = st.more ? 'Moins de mains' : 'Toutes les mains (' + rows.length + ')';
  }
  const impact = ([, x]) => (x ? value(x) * x.n : 0);  // bb gagnés ou perdus en tout (par rapport au fold)

  // compact : le tableau des familles, à côté de la grille (sans fold, total ni théorie)
  function table(id, rows, limit, first, compact) {
    const box = $(id);
    box.textContent = '';
    const keep = compact ? (decision() ? [0, 1, 2, 4, 6] : [0, 1, 2, 4]) : null;
    // colonnes : [titre, classe] ; chaque ligne donne [texte, classe] par colonne
    const head = decision()
      ? [[first], ['Jouée', 'num'], ['bb / main', 'num'], ['Fold', 'num'], ['Écart', 'num'], ['Total', 'num'], ['Verdict'], ['Théorie']]
      : [[first], ['Mains', 'num'], ['bb / main', 'num'], ['Total', 'num'], ['Verdict']];
    const pick = (list) => (keep ? keep.map((k) => list[k]) : list);
    const t = el('table', { class: 'stats hp-t' }, el('thead', {}, el('tr', {}, pick(head).map(([h, c]) => el('th', { class: c }, h)))));
    const body = el('tbody');
    const ci = (x) => signed(x.mean, 2) + (x.n > 1 ? ' ± ' + num(1.96 * x.se, 1) : '');  // une seule main : pas d'intervalle
    rows.slice(0, limit).forEach(([name, x, fx]) => {
      const [word, cls] = verdict(x);
      const cells = decision()
        ? [[name], [x ? x.n + (fx ? ' (+' + fx.n + ' f)' : '') : '0 (' + fx.n + ' f)', 'num'], [x ? ci(x) : '–', 'num'],
          [x ? signed(x.fold, 2) : '–', 'num'], [x ? signed(value(x), 2) : '–', 'num'], [x ? signed(value(x) * x.n, 1) : '–', 'num'],
          [word, cls], [advice(x, fx), 'why']]
        : [[name], [x.n, 'num'], [ci(x), 'num'], [signed(x.total, 1), 'num'], [word, cls]];
      const tr = el('tr', { class: 'pick' + (st.sel === name ? ' sel' : ''), title: 'D\'où vient le résultat de ' + name },
        pick(cells).map(([v, c]) => el('td', { class: c || null }, v)));
      tr.addEventListener('click', () => select(name));
      body.append(tr);
    });
    t.append(body);
    box.append(t);
  }

  // ---------- les mains les plus problématiques : pire écart au fold, en tout ----------
  function problems() {
    const data = DATA.formats[st.fmt].cells;
    const groups = {};  // « position|situation|groupe » -> main -> cellule
    Object.entries(data).forEach(([key, combos]) => {
      const [pos, sit, act, kind] = key.split('|');
      if (sit === 'all' || act === 'fold' || (st.pos && pos !== st.pos) || (st.kind && kind !== st.kind)) return;
      if (decision() && sit !== st.sit) return;
      const g = pos + '|' + sit + '|' + GROUP[act];
      const box = groups[g] || (groups[g] = {});
      Object.entries(combos).forEach(([h, c]) => {
        addTo(box[h] || (box[h] = zero()), c);
        const fam = family(h);
        addTo(box[fam] || (box[fam] = zero()), c);
      });
    });
    const items = [];
    Object.entries(groups).forEach(([g, names]) => {
      const [pos, sit, grp] = g.split('|');
      Object.entries(names).forEach(([name, c]) => {
        const x = stat(c);
        if (!x || x.n < MIN) return;
        const gap = x.mean - x.fold;
        if (gap >= 0) return;
        items.push({ name, pos, sit, grp, x, gap, total: gap * x.n, solid: gap + 1.96 * x.se < 0 });
      });
    });
    items.sort((a, b) => a.total - b.total);
    const list = (id, rows) => {
      const box = $(id);
      box.textContent = '';
      if (!rows.length) { box.append(el('p', { class: 'muted' }, 'Rien de net : pas assez de mains, ou pas de perte par rapport au fold.')); return; }
      const ul = el('ul', { class: 'hp-plist' });
      rows.forEach((it) => {
        const li = el('li', { title: 'Voir d\'où vient la perte' },
          el('b', {}, it.name), ' · ' + word(it.sit, it.grp) + ' ' + SIT[it.sit].toLowerCase() + ' (' + it.pos + ')',
          el('span', { class: 'd' }, it.x.n + ' fois · ' + signed(it.gap, 2) + ' bb par main par rapport au fold · ',
            el('b', { class: 'hp-bad' }, signed(it.total, 1) + ' bb en tout'), ' · ' + (it.solid ? 'net (hors hasard)' : 'à surveiller')));
        li.addEventListener('click', () => select(it.name, { pos: it.pos, sit: it.sit, act: it.grp }));
        ul.append(li);
      });
      box.append(ul);
    };
    list('hp-prob-hands', items.filter((it) => isCombo(it.name)).slice(0, 6));
    list('hp-prob-fams', items.filter((it) => !isCombo(it.name)).slice(0, 6));
  }

  // ---------- d'où vient le résultat d'une main (ou d'une famille) : calculé par le serveur à la demande ----------
  const fetched = {};  // requête -> réponse ({ error } en cas d'échec)
  function query() {
    const p = new URLSearchParams({ fmt: st.fmt, main: st.sel });
    if (st.kind) p.set('kind', st.kind);
    if (st.pos) p.set('pos', st.pos);
    if (decision()) p.set('sit', st.sit);
    if (decision() && st.act) p.set('act', st.act);
    return p.toString();
  }
  function playedActs() {
    if (!decision()) return ['all'];
    return st.act && ACTS[st.act] ? ACTS[st.act] : ['raise', 'allin', 'call', 'check'];
  }
  function mixLine(sit, m) {  // [n, n théorie, agressif, passif, fold, et leur Σthéorie]
    const groups = ['agg', 'pas', 'fold'];
    return groups.map((g, k) => {
      if (!m[2 + k] && !(m[1] && m[5 + k] / m[1] >= 0.005)) return null;
      return word(sit, g) + ' ' + pct(m[2 + k] / m[0]) + (m[1] ? ' (théorie ' + pct(m[5 + k] / m[1]) + ')' : '');
    }).filter(Boolean).join(' · ');
  }

  function detail() {
    const box = $('hp-detail');
    box.textContent = '';
    if (!st.sel) { box.hidden = true; return; }
    const acts = playedActs();
    const cell = merge(Object.entries(cellsFor(acts)).filter(([h]) => inSel(h, st.sel)).map(([, c]) => c));
    const x = stat(cell);
    box.hidden = false;
    const close = el('button', { type: 'button', class: 'hp-close', 'aria-label': 'Fermer' }, '×');
    close.addEventListener('click', () => { st.sel = ''; st.act = ''; st.pot = ''; render(); });
    const where = (st.pos || 'toutes positions') + (decision() ? ' · ' + SIT[st.sit] + (st.act ? ' : ' + word(st.sit, st.act) : '') : ' · toutes les mains');
    box.append(el('h3', {}, el('span', {}, 'D\'où vient le résultat : ' + st.sel + ' · ' + where), close));

    // choix de l'action (relance, call…) quand il y en a plusieurs
    if (decision()) {
      const present = ['agg', 'pas'].filter((g) => {
        const c = merge(Object.entries(cellsFor(ACTS[g])).filter(([h]) => inSel(h, st.sel)).map(([, v]) => v));
        return c[0] > 0;
      });
      if (present.length > 1 || st.act) {
        const chips = el('div', { class: 'hp-chips' });
        [['', 'Toutes les actions']].concat(present.map((g) => [g, word(st.sit, g)])).forEach(([g, text]) => {
          const b = el('button', { type: 'button', 'aria-pressed': String(st.act === g) }, text);
          b.addEventListener('click', () => { st.act = g; st.pot = ''; render(); });
          chips.append(b);
        });
        box.append(chips);
      }
    }
    if (!x) {
      box.append(el('p', { class: 'muted' }, decision() ? 'Jamais jouée ainsi avec ces filtres (seulement foldée).' : 'Aucune main.'));
      return;
    }
    const base = decision() ? x.fold : 0;  // l'écart se mesure au fold (ou à zéro pour toutes les mains)
    const [vword, vcls] = verdict(x);
    box.append(el('p', {}, el('b', {}, x.n + ' fois'), ' · ' + signed(x.mean, 2) + ' bb par main (± ' + num(1.96 * x.se, 1) + ')'
      + (decision() ? ', contre ' + signed(x.fold, 2) + ' bb pour le fold : ' : ' : '),
      el('b', { class: vcls }, signed(100 * (x.mean - base), 0) + ' bb/100' + (decision() ? ' par rapport au fold' : '')), ' · ' + vword
      + (x.theory !== null && decision() ? ' · la théorie le joue ' + pct(x.theory) + ' du temps' : '') + '.'));

    if (!DATA.api) { box.append(el('p', { class: 'muted' }, 'Le détail s\'ouvre dans l\'application.')); return; }
    const q = query(), D = fetched[q];
    if (!D) {
      fetched[q] = { loading: true };
      fetch(DATA.api + '?' + q)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error('erreur ' + r.status))))
        .then((data) => { fetched[q] = data; }, (e) => { fetched[q] = { error: e.message }; })
        .then(() => { if (st.sel && query() === q) detail(); });
    }
    if (!D || D.loading || D.error) {
      box.append(el('p', { class: 'muted' }, D && D.error ? 'Détail indisponible (' + D.error + ').' : 'Calcul du détail…'));
      return;
    }
    draw(box, x, D);
  }

  function draw(box, x, D) {
    // les suites du coup : par type de pot
    const br = D.branches;
    const pots = DATA.pots.map(([p]) => [p, stat(br['p:' + p]), br['p:' + p]]).filter(([, s]) => s);
    // contribution : Σ(résultat − fold) de ce pot / toutes les fois → les contributions s'additionnent en l'écart total
    pots.forEach((row) => { const c = row[2]; row[3] = ((st.measure === 'ev' ? c[3] : c[1]) - (decision() ? c[5] : 0)) / x.n; });
    const flopPots = pots.filter(([p]) => DATA.ends_for.includes(p));
    const worstPot = [...pots].sort((a, b) => a[3] - b[3])[0];
    const worstFlop = [...flopPots].sort((a, b) => a[3] - b[3])[0];
    if (!st.pot || !pots.some(([p]) => p === st.pot)) st.pot = worstFlop ? worstFlop[0] : '';
    const sub = (prefix, labels) => labels.map(([k, label]) => [label, stat(br[prefix + ':' + st.pot + ':' + k])]).filter(([, s]) => s);

    // diagnostic en quelques phrases
    const diag = [];
    if (decision() && x.theory !== null && x.theory < 0.1) diag.push('La théorie ne la joue presque jamais ainsi (' + pct(x.theory) + ') : la couper est la correction la plus simple.');
    if (worstPot && worstPot[3] < 0) {
      diag.push('L\'écart vient surtout de « ' + POT[worstPot[0]] + ' » : ' + worstPot[1].n + ' fois sur ' + x.n + ', '
        + signed(worstPot[1].mean, 2) + ' bb par main, soit ' + signed(100 * worstPot[3], 0) + ' bb/100' + (decision() ? ' par rapport au fold' : '') + '.');
    }
    const after = D.mix.next;
    if (decision() && after && after[1] && after[0] >= 5) {
      const fold = after[4] / after[0], tFold = after[7] / after[1], nxt = SIT[NEXT[st.sit]];
      if (fold - tFold > 0.15) diag.push(nxt + ' ensuite, tu foldes ' + pct(fold) + ' contre ' + pct(tFold) + ' pour la théorie : tu paies ta relance pour l\'abandonner trop souvent.');
      else if (tFold - fold > 0.15) diag.push(nxt + ' ensuite, tu continues trop : fold ' + pct(fold) + ' contre ' + pct(tFold) + ' pour la théorie.');
    }
    if (st.pot) {
      const flops = sub('f', DATA.flops).sort((a, b) => a[1].total - b[1].total);
      const ends = sub('e', DATA.ends).sort((a, b) => a[1].total - b[1].total);
      if (flops.length && flops[0][1].total < 0) diag.push('Dans « ' + POT[st.pot] + ' », c\'est avec « ' + flops[0][0] + ' » au flop que tu perds le plus ('
        + flops[0][1].n + ' fois, ' + signed(flops[0][1].mean, 2) + ' bb par main).');
      if (ends.length && ends[0][1].total < 0) diag.push('Le coup finit le plus cher par « ' + ends[0][0] + ' » (' + ends[0][1].n + ' fois, ' + signed(ends[0][1].total, 1) + ' bb en tout).');
      const ps = stat(br['p:' + st.pot]), losing = pots.some(([p, , , contrib]) => p === st.pot && contrib < 0);
      if (!losing) { /* ce pot ne coûte rien : pas besoin du solveur */ } else if (ps && ps.analysed) diag.push('Le solveur a vu ' + ps.analysed + ' de ces coups : ' + num(ps.loss, 2) + ' bb d\'EV perdus en moyenne après le flop'
        + (ps.loss >= 0.5 ? ' — tes décisions postflop coûtent : revois-les.' : ' — peu d\'erreurs : la perte tient plutôt à la variance ou au préflop.'));
      else if (FLOP_POTS.includes(st.pot)) diag.push('Aucun de ces coups n\'est encore passé au solveur : ouvre les coups à revoir ci-dessous dans le solveur.');
    }
    if (x.n < 30) diag.push('Peu de mains (' + x.n + ') : la variance pèse lourd' + (isCombo(st.sel) ? ' ; regarde aussi sa famille (' + family(st.sel) + ').' : '.'));
    if (diag.length) box.append(el('ul', { class: 'hp-diag' }, diag.map((t) => el('li', {}, t))));

    // tableau des pots
    if (pots.length) {
      box.append(el('h4', {}, 'Selon la suite du coup'));
      const maxC = Math.max(...pots.map((r) => Math.abs(r[3])), 0.01);
      const t = el('table', { class: 'stats hp-t' }, el('thead', {}, el('tr', {}, el('th', {}, 'Suite'), el('th', { class: 'num' }, 'Fois'),
        el('th', { class: 'num' }, 'bb / main'), el('th', {}, decision() ? 'Part de l\'écart au fold (bb/100)' : 'Part du résultat (bb/100)'),
        el('th', { class: 'num' }, 'Solveur'))));
      const body = el('tbody');
      pots.sort((a, b) => a[3] - b[3]).forEach(([p, s, , contrib]) => {
        const bar = el('span', { class: 'hp-bar', style: 'width:' + Math.round(60 * Math.abs(contrib) / maxC) + 'px;background:rgba(var('
          + (contrib >= 0 ? '--gain' : '--perte') + '),0.8)' });
        const tr = el('tr', { class: (DATA.ends_for.includes(p) ? 'pick' : '') + (st.pot === p ? ' sel' : '') },
          el('td', {}, POT[p]), el('td', { class: 'num' }, s.n + ' (' + pct(s.n / x.n) + ')'), el('td', { class: 'num' }, signed(s.mean, 2)),
          el('td', {}, el('span', { class: contrib >= 0 ? 'hp-good' : 'hp-bad' }, signed(100 * contrib, 0)), bar),
          el('td', { class: 'num' }, s.analysed ? '−' + num(s.loss, 2) + ' bb (' + s.analysed + ')' : FLOP_POTS.includes(p) ? '–' : ''));
        if (DATA.ends_for.includes(p)) tr.addEventListener('click', () => { st.pot = p; detail(); });
        body.append(tr);
      });
      t.append(body);
      box.append(el('div', { class: 'scroll' }, t));
      box.append(el('p', { class: 'note' }, (decision() ? 'Part de l\'écart : ce que chaque suite ajoute ou retire par rapport au fold, sur toutes les fois où tu l\'as jouée ainsi ; '
        + 'leur somme fait l\'écart total. ' : '') + 'Solveur : EV perdue en moyenne après le flop sur les coups analysés (entre parenthèses). Clique un pot pour le détailler.'));
    }

    // dans le pot choisi : main au flop, fin du coup, position
    if (st.pot) {
      const mini = (title, rows) => {
        if (!rows.length) return el('div');
        const t = el('table', { class: 'stats hp-t' }, el('thead', {}, el('tr', {}, el('th', {}, title), el('th', { class: 'num' }, 'Fois'),
          el('th', { class: 'num' }, 'bb / main'), el('th', { class: 'num' }, 'Total'))));
        const body = el('tbody');
        rows.forEach(([label, s]) => body.append(el('tr', {}, el('td', {}, label), el('td', { class: 'num' }, s.n),
          el('td', { class: 'num' }, signed(s.mean, 2)), el('td', { class: 'num ' + (s.total < 0 ? 'hp-bad' : 'hp-good') }, signed(s.total, 1)))));
        t.append(body);
        return el('div', { class: 'scroll' }, t);
      };
      box.append(el('h4', {}, 'Dans « ' + POT[st.pot] + ' »'));
      const pos = sub('i', [['ip', 'En position'], ['oop', 'Hors de position']]);
      box.append(el('div', { class: 'hp-two' }, mini('Ta main au flop', sub('f', DATA.flops)), mini('Fin du coup', sub('e', DATA.ends))));
      if (pos.length > 1) box.append(mini('Position au flop', pos));
    }

    // tes choix face à la théorie, ici et à la décision suivante
    if (decision()) {
      const lines = [];
      if (D.mix.here) lines.push([SIT[st.sit] + ' (' + D.mix.here[0] + ' fois)', mixLine(st.sit, D.mix.here)]);
      if (after) lines.push([SIT[NEXT[st.sit]] + ' ensuite (' + after[0] + ' fois)', mixLine(NEXT[st.sit], after)]);
      if (lines.length) {
        box.append(el('h4', {}, 'Tes choix face à la théorie'));
        box.append(el('ul', { class: 'hp-diag' }, lines.map(([a, b]) => el('li', {}, el('b', {}, a), ' : ' + b))));
      }
    }

    // coups à revoir : les plus chers du pot choisi
    const rows = D.review.filter((r) => !st.pot || r[5] === st.pot)
      .sort((a, b) => (st.measure === 'ev' ? a[3] - b[3] : a[4] - b[4])).slice(0, 8);
    if (rows.length) {
      box.append(el('h4', {}, 'Coups à revoir' + (st.pot ? ' (' + POT[st.pot] + ')' : '') + ' : les plus chers'));
      const ul = el('ul', { class: 'hp-review' });
      rows.forEach(([h, id, date, ev, net, pot, end, flop, loss]) => {
        const what = [POT[pot], end ? END[end] : null, flop ? 'au flop : ' + FLOP[flop].toLowerCase() : null].filter(Boolean).join(' · ');
        const li = el('li', {}, el('b', {}, h), ' · ' + (date ? date + ' · ' : '') + 'EV ' + signed(ev, 1) + ' bb (réel ' + signed(net, 1) + ')'
          + (loss !== null ? ' · solveur : −' + num(loss, 2) + ' bb d\'EV perdue' : ''), el('span', { class: 'd' }, ' — ' + what + ' · ' + id));
        if (st.fmt === 'HU') li.append(el('a', { href: 'spots#hand=' + encodeURIComponent(id) }, 'Rejouer'));
        if (FLOP_POTS.includes(pot)) li.append(el('a', { href: '/explorateur/' + encodeURIComponent(id), target: '_blank', rel: 'noopener' }, 'Solveur ↗'));
        ul.append(li);
      });
      box.append(ul);
      box.append(el('p', { class: 'note' }, 'Les coups au plus mauvais résultat ' + (st.measure === 'ev' ? '(EV all-in)' : '(réel)')
        + ' : rejoue-les, puis ouvre-les dans le solveur pour voir où l\'EV se perd.'));
    }
  }

  $('hp-more').addEventListener('click', () => { st.more = !st.more; render(); });
  readHash();
  window.addEventListener('hashchange', () => { readHash(); render(); });
  render();
  if (st.sel) { const box = $('hp-detail'); if (box && !box.hidden) box.scrollIntoView({ block: 'start' }); }
})();
"""


def build_hands_page(formats: dict[str, dict], embed: bool = True, api: str = "") -> str:
    """formats : "HU" et "ring" (tables à plusieurs) -> handplay.aggregate(...) (heads-up d'abord) ; api : l'adresse du
    détail d'une main (Library.hands_detail), sans quoi la page n'a que la grille et les tableaux."""
    if not formats:
        body = '<p class="note">Aucune main avec tes cartes connues.</p>'
        return html_page("Mains de départ", body, embed)
    data = {"formats": formats, "names": FORMAT_NAMES, "situations": [list(s) for s in handplay.SITUATIONS],
            "pots": [list(p) for p in handplay.POTS], "ends": [list(e) for e in handplay.ENDS],
            "flops": [list(f) for f in handplay.FLOPS], "flop_pots": list(handplay.FLOP_POTS),
            "ends_for": list(handplay.REVIEW_POTS), "next": handplay.NEXT, "api": api}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    heading = "" if embed else "<h1>Mains de départ</h1>"
    body = f"""{STYLE}{heading}
<p class="note">Ce que rapporte chaque main de départ : en tout, par position, puis à chaque décision préflop. Le fold
d'une décision coûte ce que tu as déjà mis au pot : la blinde (−0,5 bb en SB, −1 bb en BB) ou ton open face au 3bet
(−2,5 bb après un open à 2,5 bb). Jouer une main est donc rentable si elle rapporte en moyenne plus que ce fold :
mieux que −100 bb/100 pour défendre la BB, que −250 bb/100 pour payer un 3bet. La théorie : la fréquence de la
solution préflop en heads-up, celle de tes charts aux tables à plusieurs. Le résultat d'une décision est celui de
toute la main qui suit ; « EV all-in » remplace le résultat des tapis payés avant la river par leur espérance, pour
ôter la chance. ± : l'intervalle à 95 % — sur quelques dizaines de mains, la variance domine ; les familles de mains
tranchent plus vite. Clique une main (ou une famille) pour voir d'où vient son résultat : la suite du coup (sans
flop, SRP, pot 3bet…), ta main au flop, la fin du coup, l'avis du solveur et les coups à revoir.</p>
<div class="hp-filters">
  <div class="hp-row" id="hp-fmt"></div><div class="hp-row" id="hp-kind"></div><div class="hp-row" id="hp-pos"></div>
  <div class="hp-row" id="hp-sit"></div><div class="hp-row" id="hp-measure"></div>
</div>
<div class="hp-sum" id="hp-sum"></div>
<div class="hp-prob">
  <div class="card"><h3 style="margin-top:0">Mains les plus problématiques</h3><div id="hp-prob-hands"></div></div>
  <div class="card"><h3 style="margin-top:0">Familles les plus problématiques</h3><div id="hp-prob-fams"></div></div>
</div>
<p class="note" style="margin:-8px 0 16px">Les plus grosses pertes par rapport au fold, en tout (fois × écart), à chaque décision
préflop ; « net » : le hasard l'explique mal (intervalle à 95 %). Clique pour voir d'où vient la perte.</p>
<div class="card hp-detail" id="hp-detail" hidden></div>
<div class="hp-layout">
  <div class="card"><div class="hp-wrap"><div class="hp-grid" id="hp-grid" aria-label="Résultat par main de départ"></div></div>
  <div class="hp-legend" id="hp-legend"></div></div>
  <div class="card"><h3 style="margin-top:0">Par famille de mains</h3><div class="scroll" id="hp-fams"></div></div>
</div>
<h2>Main par main</h2>
<p class="note">Des pertes les plus lourdes (par rapport au fold, en tout) aux gains.</p>
<div class="card scroll"><div id="hp-hands"></div><button type="button" class="hp-more" id="hp-more" hidden></button></div>
<script type="application/json" id="hp-data">{payload}</script>"""
    return html_page("Mains de départ", body, embed, script=SCRIPT)
