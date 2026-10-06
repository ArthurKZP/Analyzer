"""Page « Mains de départ » : ce que rapporte chaque main, en tout, par position et à chaque décision préflop,
comparé au fold et à la théorie (analyzer/handplay.py)."""
from __future__ import annotations

import json
from .. import handplay
from ..report import html_page

STYLE = """<style>
:root { --gain: 27, 175, 122; --perte: 208, 59, 59; --vide: #ecebe6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --gain: 25, 158, 112; --perte: 230, 103, 103; --vide: #242423; } }
:root[data-theme="dark"] { --gain: 25, 158, 112; --perte: 230, 103, 103; --vide: #242423; }
.hp-filters { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; }
.hp-row { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.hp-row > span { font-size: 12px; color: var(--muted); min-width: 92px; }
.hp-row button { font: inherit; font-size: 13px; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); color: var(--ink-2); cursor: pointer; }
.hp-row button[aria-pressed="true"] { background: var(--ink); color: var(--page); border-color: var(--ink); }
.hp-sum { font-size: 14px; margin: 6px 0 10px; }
.hp-layout { display: grid; grid-template-columns: minmax(0, 11fr) minmax(0, 9fr); gap: 16px; align-items: start; }
@media (max-width: 980px) { .hp-layout { grid-template-columns: minmax(0, 1fr); } }
.hp-grid { display: grid; grid-template-columns: repeat(13, minmax(30px, 1fr)); gap: 2px; min-width: 430px; }
.hp-wrap { overflow-x: auto; }
.hp-c { position: relative; aspect-ratio: 1.2; border-radius: 3px; background: var(--vide); font-size: 11px; line-height: 1.1;
  overflow: hidden; cursor: default; }
.hp-c .k { position: absolute; left: 3px; top: 2px; font-weight: 600; color: var(--ink); }
.hp-c .v { position: absolute; left: 3px; bottom: 2px; font-size: 10px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--ink); }
.hp-c .n { position: absolute; right: 3px; top: 2px; font-size: 9px; color: var(--ink-2); }
.hp-c.empty .k { color: var(--muted); font-weight: 400; }
.hp-legend { font-size: 12px; color: var(--ink-2); margin-top: 8px; }
.hp-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -2px; margin: 0 4px 0 10px; }
table.hp-t td, table.hp-t th { white-space: nowrap; }
table.hp-t td.why { white-space: normal; min-width: 220px; font-size: 12px; color: var(--ink-2); }
.hp-good { color: var(--good); font-weight: 600; } .hp-bad { color: var(--alert); font-weight: 600; }
.hp-more { font: inherit; font-size: 12px; margin-top: 6px; border: 1px solid var(--border); background: var(--surface);
  border-radius: 6px; padding: 2px 10px; cursor: pointer; color: var(--ink-2); }
</style>"""

SCRIPT = r"""
(function () {
  const DATA = JSON.parse(document.getElementById('hp-data').textContent);
  const RANKS = 'AKQJT98765432';
  const MIN = 8;  // mains minimum pour un verdict
  const fmts = Object.keys(DATA.formats);
  const st = { fmt: fmts[0], kind: '', pos: '', sit: 'all', measure: 'ev', more: false };
  const $ = (id) => document.getElementById(id);
  const el = (tag, attrs, ...kids) => {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => { if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v); });
    kids.flat().forEach((c) => { if (c !== null && c !== undefined && c !== '') n.append(c instanceof Node ? c : String(c)); });
    return n;
  };
  const num = (x, d) => (Math.round(x * 10 ** d) / 10 ** d).toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d }).replace('-', '−');
  const signed = (x, d) => (x > 0 ? '+' : '') + num(x, d);
  const handAt = (i, j) => (i === j ? RANKS[i] + RANKS[i] : i < j ? RANKS[i] + RANKS[j] + 's' : RANKS[j] + RANKS[i] + 'o');
  const SIT = Object.fromEntries(DATA.situations);
  const ACT = { raise: 'relance', allin: 'tapis', call: 'call', check: 'check', fold: 'fold' };
  const FOLD_TEXT = { open: 'le fold de la position (0 ou la blinde déjà posée)', vs_open: 'le fold (ta blinde, ou rien)',
    vs_3bet: 'le fold (ton open perdu)', vs_4bet: 'le fold (ton 3bet perdu)', vs_limp: 'le fold' };

  // Familles de mains : plus de mains par case, des verdicts plus sûrs.
  function family(h) {
    const r = (c) => RANKS.indexOf(c), hi = h[0], lo = h[1], s = h[2] === 's';
    if (h.length === 2) return r(hi) <= r('T') ? 'Paires hautes (TT+)' : r(hi) <= r('6') ? 'Paires moyennes (66-99)' : 'Petites paires (22-55)';
    if (hi === 'A') return s ? 'As assortis' : 'As dépareillés';
    if (r(lo) <= r('T')) return s ? 'Broadways assortis' : 'Broadways dépareillés';
    if (s && r(lo) - r(hi) <= 2) return 'Connecteurs assortis';
    return s ? 'Autres assorties' : 'Autres dépareillées';
  }

  function cellsFor(acts) {
    const out = {};
    const data = DATA.formats[st.fmt].cells;
    Object.entries(data).forEach(([key, combos]) => {
      const [pos, sit, act, kind] = key.split('|');
      if ((st.pos && pos !== st.pos) || sit !== st.sit || !acts.includes(act) || (st.kind && kind !== st.kind)) return;
      Object.entries(combos).forEach(([h, c]) => {
        const o = out[h] || (out[h] = [0, 0, 0, 0, 0, 0, 0, 0]);
        c.forEach((v, k) => { o[k] += v; });
      });
    });
    return out;
  }
  function stat(c) {
    if (!c || !c[0]) return null;
    const n = c[0], s = st.measure === 'ev' ? c[3] : c[1], s2 = st.measure === 'ev' ? c[4] : c[2];
    const mean = s / n, v = n > 1 ? Math.max((s2 - s * s / n) / (n - 1), 0) : 0;
    return { n, total: s, mean, se: Math.sqrt(v / n), fold: c[5] / n, theory: c[7] ? c[6] / c[7] : null };
  }
  const merge = (list) => list.reduce((o, c) => { c.forEach((v, k) => { o[k] += v; }); return o; }, [0, 0, 0, 0, 0, 0, 0, 0]);
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
      else if (bad) parts.push('la théorie le joue (' + t + ' %) : la perte vient de la suite du coup (voir Face au solveur), ou de la variance');
      else parts.push('la théorie le joue ' + t + ' % du temps');
    }
    if (fold && fold.n && fold.theory !== null && fold.theory < 0.5) {
      parts.push('foldée ' + fold.n + ' fois alors que la théorie la joue ' + Math.round(100 * (1 - fold.theory)) + ' % du temps');
    }
    return parts.join(' ; ');
  }

  function buttons(id, label, options, key) {
    const row = $(id);
    row.textContent = '';
    if (!options.length) { row.hidden = true; return; }
    row.hidden = false;
    row.append(el('span', {}, label));
    options.forEach(([v, text]) => {
      const b = el('button', { type: 'button', 'aria-pressed': String(st[key] === v) }, text);
      b.addEventListener('click', () => { st[key] = v; st.more = false; render(); });
      row.append(b);
    });
  }

  function render() {
    const f = DATA.formats[st.fmt];
    buttons('hp-fmt', 'Format', fmts.length > 1 ? fmts.map((x) => [x, x === 'HU' ? 'Heads-up' : x]) : [], 'fmt');
    buttons('hp-kind', 'Adversaires', st.fmt === 'HU' ? [['', 'Tous'], ['reg', 'Réguliers'], ['rec', 'Récréatifs']] : [], 'kind');
    if (st.pos && !f.positions.includes(st.pos)) st.pos = '';
    buttons('hp-pos', 'Position', [['', 'Toutes']].concat(f.positions.map((p) => [p, p])), 'pos');
    buttons('hp-sit', 'Décision', [['all', 'Toutes les mains']].concat(DATA.situations.filter(([k]) => k !== 'vs_limp' || st.fmt !== 'HU')), 'sit');
    buttons('hp-measure', 'Résultat', [['ev', 'EV all-in'], ['net', 'Réel']], 'measure');

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

    // grille 13 × 13
    const grid = $('hp-grid');
    grid.textContent = '';
    const scale = decision() ? 4 : 3;
    for (let i = 0; i < 13; i++) for (let j = 0; j < 13; j++) {
      const h = handAt(i, j), x = stat(cells[h]), fx = stat(folds[h]);
      const c = el('div', { class: 'hp-c' + (x ? '' : ' empty') }, el('span', { class: 'k' }, h));
      if (x) {
        const v = value(x), a = Math.min(Math.abs(v) / scale, 1) * (x.n >= MIN ? 0.85 : 0.35) + 0.08;
        c.style.background = 'rgba(var(' + (v >= 0 ? '--gain' : '--perte') + '), ' + a.toFixed(2) + ')';
        c.append(el('span', { class: 'v' }, signed(v, 1)), el('span', { class: 'n' }, x.n));
        c.title = h + ' : ' + x.n + ' fois, ' + signed(x.mean, 2) + ' bb par main (± ' + num(1.96 * x.se, 1) + ')'
          + (decision() ? ', fold : ' + signed(x.fold, 2) + ' bb → ' + signed(v, 2) + ' bb par rapport au fold' : '')
          + (fx ? '\nfoldée ' + fx.n + ' fois' : '') + (decision() && advice(x, fx) ? '\n' + advice(x, fx) : '');
      } else if (fx) {
        c.append(el('span', { class: 'n' }, 'f' + fx.n));
        c.title = h + ' : foldée ' + fx.n + ' fois' + (fx.theory !== null ? ' (la théorie folde ' + Math.round(100 * fx.theory) + ' %)' : '');
      }
      grid.append(c);
    }
    $('hp-legend').textContent = decision()
      ? 'Couleur et nombre : bb gagnés par main jouée, par rapport au fold à ce moment (vert : mieux que le fold). Pâle : moins de '
        + MIN + ' mains. En haut à droite : mains jouées (fN : seulement foldée N fois).'
      : 'Couleur et nombre : bb gagnés par main (vert : gagnante). Pâle : moins de ' + MIN + ' mains. En haut à droite : mains.';

    // tableaux : par main, par famille, par position
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
      body.append(el('tr', {}, pick(cells).map(([v, c]) => el('td', { class: c || null }, v))));
    });
    t.append(body);
    box.append(t);
  }

  $('hp-more').addEventListener('click', () => { st.more = !st.more; render(); });
  render();
})();
"""


def build_hands_page(formats: dict[str, dict], embed: bool = True) -> str:
    """formats : format de table -> handplay.aggregate(...) (heads-up d'abord)."""
    if not formats:
        body = '<p class="note">Aucune main avec tes cartes connues.</p>'
        return html_page("Mains de départ", body, embed)
    data = {"formats": formats, "situations": [list(s) for s in handplay.SITUATIONS]}
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
tranchent plus vite.</p>
<div class="hp-filters">
  <div class="hp-row" id="hp-fmt"></div><div class="hp-row" id="hp-kind"></div><div class="hp-row" id="hp-pos"></div>
  <div class="hp-row" id="hp-sit"></div><div class="hp-row" id="hp-measure"></div>
</div>
<div class="hp-sum" id="hp-sum"></div>
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
