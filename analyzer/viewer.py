"""Visualiseur de spots : page HTML autonome pour filtrer et rejouer les mains."""
from __future__ import annotations

import json
import re
from html import escape
from typing import Optional

from .models import Hand
from .report import EMBED_SCRIPT
from .spots import line_options, spot_records


def _json_for_script(data) -> str:
    """JSON sûr à placer dans une balise <script> (pas de « </script> » possible)."""
    return (json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def build_viewer(hands: list[Hand], hero: str, villain: Optional[str] = None, report_href: str = "",
                 embed: bool = False, solver: bool = False) -> str:
    """villain=None : toutes tes mains, contre tous tes adversaires.

    solver=True (application) : le replayer propose de résoudre le coup avec GTOpen.
    """
    records = spot_records(hands, hero, villain)
    title = f"Spots — {villain}" if villain else "Mes spots — tous adversaires"
    data = {
        "hero": hero,
        "villain": villain or "",
        "records": records,
        "lines": line_options(records),
        "solver": solver,
    }
    values = {
        "TITLE": escape(title),
        "BACK": f'<a href="{escape(report_href)}">Rapport complet</a>' if report_href else "",
        "DATA": _json_for_script(data),
        "SCRIPT": SCRIPT + (EMBED_SCRIPT if embed else ""),
        "HEADING": "" if embed else f"<h1>{escape(title)}</h1>",
    }
    # Substitution en une seule passe : le contenu inséré n'est jamais relu.
    return re.sub(r"__(TITLE|BACK|DATA|SCRIPT|HEADING)__", lambda m: values[m.group(1)], TEMPLATE)


TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --accent: #2a78d6; --accent-bg: rgba(42,120,214,0.10); --felt: #eef2ee; --felt-edge: #d5ddd5;
  --win: #006300; --loss: #d03b3b;
  --sc: #0b0b0b; --sh: #d03b3b; --sd: #2a78d6; --sclub: #008300;
  --hero: #2a78d6; --villain: #eb6834;
  --g-fold: #2a78d6; --g-pass: #1baf7a; --g-bet1: #f2a65a; --g-bet2: #eb6834; --g-bet3: #c4441c; --g-bet4: #8f2d14; --g-allin: #4a3aa7; --g-empty: #ecebe6;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --accent: #3987e5; --accent-bg: rgba(57,135,229,0.16); --felt: #18211b; --felt-edge: #2a352d;
    --win: #0ca30c; --loss: #e66767;
    --sc: #ffffff; --sh: #e66767; --sd: #6da7ec; --sclub: #0ca30c;
    --hero: #3987e5; --villain: #d95926;
    --g-fold: #3987e5; --g-pass: #199e70; --g-bet1: #e8954a; --g-bet2: #d95926; --g-bet3: #b8401b; --g-bet4: #8a2c13; --g-allin: #9085e9; --g-empty: #242423;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --accent: #3987e5; --accent-bg: rgba(57,135,229,0.16); --felt: #18211b; --felt-edge: #2a352d;
  --win: #0ca30c; --loss: #e66767;
  --sc: #ffffff; --sh: #e66767; --sd: #6da7ec; --sclub: #0ca30c;
  --hero: #3987e5; --villain: #d95926;
  --g-fold: #3987e5; --g-pass: #199e70; --g-bet1: #e8954a; --g-bet2: #d95926; --g-bet3: #b8401b; --g-bet4: #8a2c13; --g-allin: #9085e9; --g-empty: #242423;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1280px; margin: 0 auto; padding: 20px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 2px; }
a { color: var(--accent); }
.meta, .muted { color: var(--muted); }
button, select, input { font: inherit; color: inherit; }
.presets { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 12px; }
.presets button { background: var(--surface); border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px; cursor: pointer; }
.presets button:hover { border-color: var(--accent); }
.presets button[aria-pressed="true"] { background: var(--accent-bg); border-color: var(--accent); font-weight: 600; }
.filters { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.filters label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--ink-2); min-width: 0; }
.filters select, .filters input { background: var(--page); border: 1px solid var(--border); border-radius: 6px; padding: 5px 6px; font-size: 13px; min-width: 0; width: 100%; }
.filters .wide { grid-column: span 2; }
.filters .reset { align-self: end; background: none; border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; cursor: pointer; }
.summary { margin: 12px 0; font-size: 13px; color: var(--ink-2); }
.summary b { color: var(--ink); }
.summary .freq { display: block; margin-top: 2px; }
.layout { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); gap: 16px; align-items: start; }
.list { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.row { display: grid; grid-template-columns: auto 1fr auto; gap: 2px 10px; text-align: left; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; cursor: pointer; width: 100%; }
.row:hover { border-color: var(--axis); }
.row[aria-current="true"] { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
.row .when { font-size: 12px; color: var(--muted); white-space: nowrap; }
.row .cards-line { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; min-width: 0; }
.row .net { font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; text-align: right; }
.row .sub { grid-column: 1 / -1; font-size: 12px; color: var(--ink-2); }
.net.pos { color: var(--win); } .net.neg { color: var(--loss); }
.badge { display: inline-block; font-size: 11px; border: 1px solid var(--border); border-radius: 4px; padding: 0 5px; color: var(--ink-2); white-space: nowrap; }
.more { background: var(--surface); border: 1px dashed var(--axis); border-radius: 8px; padding: 8px; cursor: pointer; }
.pc { display: inline-block; font-weight: 600; font-family: ui-monospace, monospace; padding: 0 3px; margin-right: 2px; border-radius: 3px; background: var(--page); border: 1px solid var(--border); font-size: 12px; line-height: 1.5; }
.pc.ss { color: var(--sc); } .pc.sh { color: var(--sh); } .pc.sd { color: var(--sd); } .pc.sc { color: var(--sclub); }
.pc.back { color: var(--muted); background: repeating-linear-gradient(45deg, var(--grid) 0 3px, var(--surface) 3px 6px); }
.cards { white-space: nowrap; }
.player { position: sticky; top: 12px; max-height: calc(100vh - 24px); overflow-y: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px; min-width: 0; }
.player-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.player-head h2 { font-size: 15px; margin: 0; }
.close { display: none; background: none; border: 1px solid var(--border); border-radius: 6px; padding: 2px 10px; cursor: pointer; }
.table { background: var(--felt); border: 1px solid var(--felt-edge); border-radius: 80px; padding: 14px 18px; margin: 12px 0; display: grid; gap: 10px; }
.seat { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 4px 10px; }
.seat .who { font-weight: 600; }
.seat .who i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: 0; }
.seat .stack { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.seat .hole .pc { font-size: 16px; padding: 1px 5px; }
.seat .bet { grid-column: 1 / -1; font-size: 12px; font-variant-numeric: tabular-nums; min-height: 18px; }
.seat .bet span { background: var(--surface); border: 1px solid var(--border); border-radius: 999px; padding: 0 8px; }
.center { text-align: center; padding: 6px 0; border-top: 1px dashed var(--felt-edge); border-bottom: 1px dashed var(--felt-edge); }
.board .pc { font-size: 18px; padding: 2px 6px; margin: 0 2px; }
.board .slot { display: inline-block; width: 30px; height: 30px; border: 1px dashed var(--felt-edge); border-radius: 4px; margin: 0 2px; vertical-align: middle; }
.pot { margin-top: 4px; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.now { font-size: 14px; font-weight: 600; min-height: 22px; }
.info { font-size: 12px; color: var(--ink-2); min-height: 18px; margin-top: 2px; }
.controls { display: flex; align-items: center; gap: 6px; margin: 10px 0; flex-wrap: wrap; }
.controls button { background: var(--page); border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; cursor: pointer; min-width: 40px; }
.controls button:disabled { opacity: 0.4; cursor: default; }
.controls .pos { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; margin: 0 4px; }
.controls label { margin-left: auto; font-size: 12px; color: var(--ink-2); display: flex; align-items: center; gap: 4px; }
.log { border-top: 1px solid var(--grid); padding-top: 8px; font-size: 13px; }
.log h3 { font-size: 12px; margin: 8px 0 4px; color: var(--muted); font-weight: 600; display: flex; gap: 8px; align-items: center; }
.log button { display: block; width: 100%; text-align: left; background: none; border: 0; border-radius: 4px; padding: 2px 6px; cursor: pointer; }
.log button:hover { background: var(--accent-bg); }
.log button[aria-current="true"] { background: var(--accent-bg); font-weight: 600; }
.log .t { color: var(--muted); font-size: 11px; }
.tag-h { color: var(--hero); font-weight: 700; } .tag-v { color: var(--villain); font-weight: 700; }
.foot { margin-top: 10px; font-size: 12px; color: var(--muted); display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.gto { border-top: 1px solid var(--grid); margin-top: 10px; padding-top: 8px; font-size: 13px; }
.gto h3 { font-size: 12px; margin: 4px 0 6px; color: var(--muted); font-weight: 600; }
.gto p { margin: 4px 0; }
.gto .muted { font-size: 12px; }
.gto .go, .gto .stop { border-radius: 6px; padding: 5px 12px; cursor: pointer; margin-top: 4px; }
.gto .go { background: var(--accent); color: #fff; border: 1px solid var(--accent); }
.gto .stop { background: var(--page); border: 1px solid var(--border); }
.gto code { font-family: ui-monospace, monospace; background: var(--page); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; font-size: 12px; user-select: all; }
.gto .err { color: var(--loss); }
.gto-bar { height: 6px; background: var(--grid); border-radius: 3px; overflow: hidden; margin: 6px 0; }
.gto-bar span { display: block; height: 100%; background: var(--accent); transition: width 0.4s; }
.gto-list button { display: grid; grid-template-columns: 44px 30px 1fr auto; gap: 6px; width: 100%; text-align: left; background: none; border: 0; border-radius: 4px; padding: 3px 6px; cursor: pointer; font-size: 12px; }
.gto-list button:hover { background: var(--accent-bg); }
.gto-list button[aria-current="true"] { background: var(--accent-bg); font-weight: 600; }
.gto-list .st { color: var(--muted); }
.v-main { color: var(--win); } .v-mixed { color: var(--ink-2); } .v-dev { color: var(--loss); font-weight: 700; }
.gto-detail { margin-top: 8px; padding: 10px; background: var(--page); border: 1px solid var(--border); border-radius: 8px; }
.gto-detail .title { font-weight: 600; margin-bottom: 4px; }
.gto table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; margin: 6px 0; }
.gto th, .gto td { padding: 3px 6px; border-bottom: 1px solid var(--grid); text-align: right; white-space: nowrap; }
.gto th:first-child, .gto td:first-child { text-align: left; white-space: normal; }
.gto th { font-size: 11px; color: var(--muted); font-weight: 600; }
.gto tr.chosen td { font-weight: 700; }
.gto .sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
.g-grid { display: grid; grid-template-columns: repeat(13, minmax(0, 1fr)); gap: 1px; margin-top: 8px; }
.g-c { position: relative; aspect-ratio: 1.25; background-color: var(--g-empty); border-radius: 2px; font-size: 9px; line-height: 1; overflow: hidden; }
.g-c span { position: absolute; left: 2px; top: 2px; color: #fff; font-weight: 600; text-shadow: 0 0 2px rgba(0,0,0,0.85); }
.g-c.out span { color: var(--muted); font-weight: 400; text-shadow: none; }
.g-c.low span { color: var(--ink-2); text-shadow: none; }
.g-c.me { box-shadow: inset 0 0 0 2px var(--ink); }
.g-note { font-size: 11px; color: var(--muted); margin-top: 4px; }
.empty { padding: 24px; text-align: center; color: var(--muted); background: var(--surface); border: 1px dashed var(--axis); border-radius: 10px; }
@media (max-width: 860px) {
  .layout { grid-template-columns: 1fr; }
  .player { position: fixed; inset: 0; max-height: none; border-radius: 0; overflow-y: auto; z-index: 10; padding: 14px 16px 32px; }
  .player[hidden] { display: none; }
  .close { display: inline-block; }
  .table { border-radius: 24px; }
  .filters { grid-template-columns: 1fr 1fr; padding: 10px; }
  .filters .wide { grid-column: 1 / -1; }
  .presets { flex-wrap: nowrap; overflow-x: auto; padding-bottom: 4px; }
  .presets button { white-space: nowrap; }
}
</style>
</head>
<body>
<main>
  __HEADING__
  <div class="meta"><span id="meta"></span> __BACK__</div>
  <nav class="presets" id="presets" aria-label="Spots prédéfinis"></nav>
  <section class="filters" id="filters" aria-label="Filtres"></section>
  <div class="summary" id="summary" aria-live="polite"></div>
  <div class="layout">
    <section class="list" id="list" aria-label="Mains"></section>
    <section class="player" id="player" hidden aria-label="Replayer">
      <div class="player-head"><h2 id="p-title"></h2><button class="close" id="p-close" type="button">Fermer</button></div>
      <div class="table">
        <div class="seat" id="seat-V"></div>
        <div class="center"><div class="board" id="board"></div><div class="pot" id="pot"></div></div>
        <div class="seat" id="seat-H"></div>
      </div>
      <div class="now" id="now"></div>
      <div class="info" id="info"></div>
      <div class="controls">
        <button type="button" id="b-first" aria-label="Début">⏮</button>
        <button type="button" id="b-prev" aria-label="Action précédente">◀</button>
        <span class="pos" id="p-pos"></span>
        <button type="button" id="b-next" aria-label="Action suivante">▶</button>
        <button type="button" id="b-last" aria-label="Fin">⏭</button>
        <label><input type="checkbox" id="reveal"> Montrer sa main</label>
      </div>
      <div class="log" id="log"></div>
      <div class="gto" id="gto" hidden></div>
      <div class="foot"><span id="p-id"></span><a id="p-link" href="#">Lien vers ce coup</a></div>
    </section>
  </div>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>__SCRIPT__</script>
</body>
</html>
"""

SCRIPT = r"""
(function () {
  const DATA = JSON.parse(document.getElementById('data').textContent);
  const R = DATA.records;
  const SUITS = { s: '♠', h: '♥', d: '♦', c: '♣' };
  const STREETS = { p: 'Préflop', f: 'Flop', t: 'Turn', r: 'River' };
  const BOARD_N = { p: 0, f: 3, t: 4, r: 5 };
  const REACH = { f: 1, t: 2, r: 3 };
  const WHO = { H: 'Toi', V: 'Lui' };
  const ACTS = [['', '—'], ['bet', 'Mise'], ['raise', 'Relance'], ['call', 'Call'], ['fold', 'Fold'],
    ['xx', 'Check (sans mise en face)'], ['xc', 'Check-call'], ['xr', 'Check-raise'], ['xf', 'Check-fold']];
  const ACT_LABEL = Object.fromEntries(ACTS);
  const PT_LABEL = { walk: 'walk', limp: 'limpé', srp: 'SRP', '3bp': 'pot 3bet', '4bp': 'pot 4bet+' };
  const END_LABEL = { sd: 'abattage', hfp: 'tu folds préflop', hff: 'tu folds au flop', hft: 'tu folds au turn',
    hfr: 'tu folds à la river', vfp: 'il folde préflop', vff: 'il folde au flop', vft: 'il folde au turn', vfr: 'il folde à la river' };

  const FILTERS = [
    { id: 'pot', label: 'Pot', options: [['', 'Tous'], ['srp', 'Relancé (SRP)'], ['3bp', '3bet'], ['4bp', '4bet et +'], ['limp', 'Limpé'], ['walk', 'Walk (fold d\'entrée)']] },
    { id: 'pfa', label: 'Agresseur préflop', options: [['', 'Tous'], ['H', 'Toi'], ['V', 'Lui']] },
    { id: 'pos', label: 'Ta position', options: [['', 'Toutes'], ['BTN', 'Bouton (SB)'], ['BB', 'Big blind']] },
    { id: 'reach', label: 'Street atteinte', options: [['', 'Toutes'], ['1', 'Flop'], ['2', 'Turn'], ['3', 'River']] },
    { id: 'st', label: 'Street des filtres d\'action', options: [['f', 'Flop'], ['t', 'Turn'], ['r', 'River']] },
    { id: 'cb', label: 'C-bet de l\'agresseur', options: [['', '—'], ['any', 'Occasion de c-bet'], ['1', 'C-bet fait'], ['0', 'C-bet checké']] },
    { id: 'h', label: 'Tes actions', options: ACTS },
    { id: 'v', label: 'Ses actions', options: ACTS },
    { id: 'l', label: 'Ligne (comme dans le rapport)', wide: true, options: null },
    { id: 'end', label: 'Fin du coup', options: [['', 'Toutes'], ['sd', 'Abattage'], ['hf', 'Tu folds'], ['hfp', 'Tu folds préflop'],
      ['hff', 'Tu folds au flop'], ['hft', 'Tu folds au turn'], ['hfr', 'Tu folds à la river'], ['vf', 'Il folde'], ['ai', 'All-in']] },
    { id: 'known', label: 'Sa main', options: [['', 'Connue ou non'], ['1', 'Connue (abattage)']] },
    { id: 'res', label: 'Résultat', options: [['', 'Tous'], ['w', 'Gagné'], ['l', 'Perdu']] },
    { id: 'sort', label: 'Tri', options: [['date', 'Plus récentes'], ['old', 'Plus anciennes'], ['pot', 'Plus gros pots'], ['loss', 'Plus grosses pertes'], ['win', 'Plus gros gains']] },
    { id: 'q', label: 'Recherche (AKo, 99, Ah, n° de main)', input: true },
  ];
  const OPPONENTS = [...new Set(R.map((r) => r.opp))].sort((a, b) => a.localeCompare(b));
  if (OPPONENTS.length > 1) {
    FILTERS.unshift({ id: 'opp', label: 'Adversaire', options: [['', 'Tous (' + OPPONENTS.length + ')']].concat(OPPONENTS.map((o) => [o, o])) });
  }
  const DEFAULTS = { st: 'f', sort: 'date' };
  const PRESETS = [
    ['C-bet flop en SRP — toi', { pot: 'srp', pfa: 'H', cb: 'any' }],
    ['C-bet flop en SRP — lui', { pot: 'srp', pfa: 'V', cb: 'any' }],
    ['Pots 3bet au flop — toi en BB', { pot: '3bp', pos: 'BB', reach: '1' }],
    ['Pots 3bet au flop — toi au bouton', { pot: '3bp', pos: 'BTN', reach: '1' }],
    ['Ses check-raises flop', { v: 'xr' }],
    ['Ses barrels turn', { pfa: 'V', st: 't', cb: '1' }],
    ['Ses mises river', { st: 'r', v: 'bet' }],
    ['Tes folds à la river', { end: 'hfr' }],
    ['All-in', { end: 'ai' }],
    ['Tes plus grosses pertes', { res: 'l', sort: 'loss' }],
  ];

  let F = {};
  let visible = [];
  let shown = 0;
  let current = null;
  let steps = [];
  let stepIndex = 0;

  // ---------- utilitaires ----------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : document.createTextNode(c));
    return node;
  }
  const num = (x, d = 1) => (Math.round(x * 10 ** d) / 10 ** d).toLocaleString('fr-FR', { maximumFractionDigits: d });
  const signed = (x) => (x > 0 ? '+' : x < 0 ? '−' : '') + num(Math.abs(x));
  function card(c) {
    return el('span', { class: 'pc s' + c[1].toLowerCase() }, c[0].toUpperCase() + (SUITS[c[1].toLowerCase()] || c[1]));
  }
  function cards(list, hidden) {
    if (!list || !list.length) return el('span', { class: 'cards' }, hidden ? [el('span', { class: 'pc back' }, '??'), el('span', { class: 'pc back' }, '??')] : []);
    return el('span', { class: 'cards' }, list.map(card));
  }

  // ---------- filtres ----------
  function buildFilters() {
    const box = document.getElementById('filters');
    for (const f of FILTERS) {
      let control;
      if (f.input) {
        control = el('input', { id: 'f-' + f.id, type: 'search', placeholder: 'ex. AKo' });
        control.addEventListener('input', () => { F.q = control.value.trim(); apply(); });
      } else {
        control = el('select', { id: 'f-' + f.id });
        if (f.id === 'l') {
          control.append(el('option', { value: '' }, 'Toutes'));
          const groups = {};
          for (const [tag, n] of DATA.lines) {
            const [who, street, label, size] = tag.split('|');
            const key = (who === 'V' ? 'Ses lignes' : 'Tes lignes') + ' — ' + street;
            groups[key] = groups[key] || el('optgroup', { label: key });
            groups[key].append(el('option', { value: tag }, label + (size ? ' · ' + size : '') + ' (' + n + ')'));
          }
          Object.values(groups).forEach((g) => control.append(g));
        } else {
          for (const [v, text] of f.options) control.append(el('option', { value: v }, text));
        }
        control.addEventListener('change', () => { F[f.id] = control.value; apply(); });
      }
      box.append(el('label', { class: f.wide ? 'wide' : null }, f.label, control));
    }
    box.append(el('button', { class: 'reset', type: 'button', onclick: () => { setFilters({}); } }, 'Tout effacer'));
    const presets = document.getElementById('presets');
    PRESETS.forEach(([name, values], i) => {
      presets.append(el('button', { type: 'button', 'data-i': i, onclick: () => setFilters(values) }, name));
    });
  }

  function setFilters(values) {
    F = Object.assign({}, DEFAULTS, values);
    for (const f of FILTERS) {
      const control = document.getElementById('f-' + f.id);
      if (!control) continue;
      control.value = F[f.id] || (f.input ? '' : (DEFAULTS[f.id] || ''));
    }
    apply();
  }

  function matches(r) {
    const st = F.st || 'f';
    if (F.opp && r.opp !== F.opp) return false;
    if (F.pot && r.pt !== F.pot) return false;
    if (F.pfa && r.pfa !== F.pfa) return false;
    if (F.pos && r.hp !== F.pos) return false;
    if (F.reach && r.reach < +F.reach) return false;
    if (F.cb) {
      const c = r.cb[st];
      if (c === undefined) return false;
      if (F.cb !== 'any' && String(c) !== F.cb) return false;
    }
    if (F.h && !(r.sa[st] && r.sa[st].H.includes(F.h))) return false;
    if (F.v && !(r.sa[st] && r.sa[st].V.includes(F.v))) return false;
    if (F.l && !r.tags.includes(F.l)) return false;
    if (F.end) {
      if (F.end === 'ai') { if (!r.ai) return false; }
      else if (F.end === 'hf' || F.end === 'vf') { if (!r.end.startsWith(F.end)) return false; }
      else if (r.end !== F.end) return false;
    }
    if (F.known && !r.vc.length) return false;
    if (F.res === 'w' && !(r.net > 0)) return false;
    if (F.res === 'l' && !(r.net < 0)) return false;
    if (F.q) {
      const q = F.q.toLowerCase();
      const hay = [r.id, r.hn, r.vn, r.hc.join(''), r.vc.join('')].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  function normalized(values) {
    const full = Object.assign({}, DEFAULTS, values);
    return Object.keys(full).filter((k) => full[k] && full[k] !== DEFAULTS[k]).sort().map((k) => k + '=' + full[k]).join('&');
  }

  const SORTS = {
    date: (a, b) => b.ts.localeCompare(a.ts),
    old: (a, b) => a.ts.localeCompare(b.ts),
    pot: (a, b) => b.tot - a.tot,
    loss: (a, b) => a.net - b.net,
    win: (a, b) => b.net - a.net,
  };

  function apply() {
    visible = R.filter(matches).sort(SORTS[F.sort] || SORTS.date);
    shown = 0;
    document.getElementById('list').textContent = '';
    renderSummary();
    renderMore();
    const active = normalized(F);
    document.querySelectorAll('#presets button').forEach((b) => {
      b.setAttribute('aria-pressed', normalized(PRESETS[+b.dataset.i][1]) === active ? 'true' : 'false');
    });
    writeHash();
  }

  function renderSummary() {
    const box = document.getElementById('summary');
    box.textContent = '';
    const n = visible.length;
    const total = visible.reduce((s, r) => s + r.net, 0);
    box.append(el('b', {}, n + ' main' + (n > 1 ? 's' : '')));
    if (n) {
      box.append(' · ton résultat ', el('b', {}, signed(total) + ' bb'), ' (' + signed(total / n) + ' bb par main)');
      const sd = visible.filter((r) => r.end === 'sd').length;
      const hf = visible.filter((r) => r.end.startsWith('hf')).length;
      const vf = visible.filter((r) => r.end.startsWith('vf')).length;
      box.append(' · abattage ' + sd + ' · tu folds ' + hf + ' · il folde ' + vf);
      const st = F.l ? { flop: 'f', turn: 't', river: 'r' }[F.l.split('|')[1]] : (F.st || 'f');
      const reached = visible.filter((r) => r.reach >= REACH[st] && r.sa[st] && (r.sa[st].H.length || r.sa[st].V.length));
      if (reached.length) {
        const freq = (who) => {
          const counts = {};
          reached.forEach((r) => r.sa[st][who].forEach((c) => { counts[c] = (counts[c] || 0) + 1; }));
          return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 4)
            .map(([c, k]) => ACT_LABEL[c].toLowerCase() + ' ' + Math.round(100 * k / reached.length) + ' %').join(', ');
        };
        box.append(el('span', { class: 'freq' }, STREETS[st] + ' (' + reached.length + ' mains jouées) — toi : ' + (freq('H') || '—') + ' · lui : ' + (freq('V') || '—')));
      }
    }
  }

  function renderMore() {
    const list = document.getElementById('list');
    const old = list.querySelector('.more');
    if (old) old.remove();
    if (!visible.length) {
      list.append(el('div', { class: 'empty' }, 'Aucune main pour ce spot.'));
      return;
    }
    const next = visible.slice(shown, shown + 150);
    next.forEach((r) => list.append(row(r)));
    shown += next.length;
    if (shown < visible.length) {
      list.append(el('button', { class: 'more', type: 'button', onclick: renderMore }, 'Afficher plus (' + (visible.length - shown) + ' restantes)'));
    }
    markCurrent();
  }

  function row(r) {
    const vpos = r.hp === 'BTN' ? 'BB' : 'BTN';
    const board = r.b.length ? cards(r.b) : el('span', { class: 'muted' }, 'pas de flop');
    const net = el('span', { class: 'net ' + (r.net > 0 ? 'pos' : r.net < 0 ? 'neg' : '') }, signed(r.net) + ' bb');
    const who = OPPONENTS.length > 1 ? r.opp : 'Lui';
    const sub = 'Toi (' + r.hp + ') : ' + r.hl + ' · ' + who + ' (' + vpos + ') : ' + r.vl + ' · ' + (END_LABEL[r.end] || '');
    return el('button', { class: 'row', type: 'button', 'data-id': r.id, onclick: () => open(r) },
      el('span', { class: 'when' }, r.d),
      el('span', { class: 'cards-line' }, cards(r.hc), el('span', { class: 'muted' }, 'vs'), cards(r.vc, true), board,
        el('span', { class: 'badge' }, PT_LABEL[r.pt] || r.pt)),
      net,
      el('span', { class: 'sub' }, sub));
  }

  function markCurrent() {
    document.querySelectorAll('.row').forEach((b) => b.setAttribute('aria-current', current && b.dataset.id === current.id ? 'true' : 'false'));
  }

  // ---------- replayer ----------
  function actionText(r, a, lastTo) {
    const who = WHO[a.p];
    let text;
    if (a.k === 'bet') text = who + ' mise ' + num(a.a) + ' bb (' + Math.round(100 * a.a / a.pot) + ' % du pot)';
    else if (a.k === 'raise') text = who + ' relance à ' + num(a.to) + ' bb' + (lastTo ? ' (×' + num(a.to / lastTo) + ')' : '');
    else if (a.k === 'call') text = who + ' paie ' + num(a.a) + ' bb';
    else if (a.k === 'check') text = who + ' checke';
    else if (a.k === 'fold') text = who + ' folde';
    else text = who + ' ' + a.k;
    if (a.ai) text += ' — tapis';
    return text;
  }

  function buildSteps(r) {
    const S = [];
    const stack = { H: r.hs, V: r.vs };
    let pot = 0, bets = { H: 0, V: 0 }, street = 'p', boardN = 0, lastTo = 0;
    const snap = (extra) => S.push(Object.assign({ stack: { ...stack }, pot, bets: { ...bets }, boardN, street }, extra));
    r.x.forEach((a, i) => {
      if (a.k === 'post_sb' || a.k === 'post_bb') {
        stack[a.p] -= a.a; pot += a.a; bets[a.p] = a.to; lastTo = Math.max(lastTo, a.to);
        return;
      }
      if (!S.length) snap({ kind: 'start', text: 'Blindes postées — ' + WHO[r.hp === 'BTN' ? 'H' : 'V'] + ' parle en premier' });
      if (a.s !== street) {
        street = a.s; bets = { H: 0, V: 0 }; boardN = BOARD_N[street]; lastTo = 0;
        snap({ kind: 'street', text: STREETS[street] + ' — pot ' + num(pot) + ' bb' });
      }
      stack[a.p] -= a.a; pot += a.a;
      if (a.k === 'bet' || a.k === 'raise' || a.k === 'call') bets[a.p] = a.to;
      snap({ kind: 'action', i, who: a.p, text: actionText(r, a, lastTo), t: a.t });
      if (a.k === 'bet' || a.k === 'raise') lastTo = a.to;
    });
    if (!S.length) snap({ kind: 'start', text: 'Blindes postées' });
    for (const s of ['f', 't', 'r']) {
      if (r.b.length >= BOARD_N[s] && boardN < BOARD_N[s]) {
        street = s; boardN = BOARD_N[s]; bets = { H: 0, V: 0 };
        snap({ kind: 'street', text: STREETS[s] + ' (joueurs à tapis)' });
      }
    }
    for (const p of ['H', 'V']) if (r.ret[p]) { stack[p] += r.ret[p]; pot -= r.ret[p]; }
    bets = { H: 0, V: 0 };
    for (const p of ['H', 'V']) if (r.win[p]) stack[p] += r.win[p];
    const winners = Object.entries(r.win).map(([p, v]) => WHO[p] + ' gagne ' + num(v) + ' bb');
    snap({ kind: 'end', text: (r.end === 'sd' ? 'Abattage — ' : '') + winners.join(', ') + ' · ton résultat ' + signed(r.net) + ' bb', reveal: r.end === 'sd' });
    return S;
  }

  function open(r, step) {
    current = r;
    steps = buildSteps(r);
    stepIndex = step === undefined ? 0 : step;
    const reveal = document.getElementById('reveal');
    reveal.checked = false;
    reveal.disabled = !r.vc.length;
    document.getElementById('p-title').textContent = r.d + ' · ' + (PT_LABEL[r.pt] || r.pt) + ' · toi au ' + (r.hp === 'BTN' ? 'bouton' : 'big blind');
    document.getElementById('p-id').textContent = 'Main ' + r.id + ' · ' + r.g;
    const player = document.getElementById('player');
    player.hidden = false;
    renderLog();
    renderStep();
    markCurrent();
    writeHash();
    gtoLoad(r, false);
    if (window.matchMedia('(max-width: 860px)').matches) player.scrollTop = 0;
  }

  function seat(who, s, revealed) {
    const r = current;
    const pos = who === 'H' ? r.hp : (r.hp === 'BTN' ? 'BB' : 'BTN');
    const name = who === 'H' ? DATA.hero : (r.opp || DATA.villain);
    const hole = who === 'H' ? cards(r.hc) : (revealed && r.vc.length ? cards(r.vc) : cards([], true));
    const box = document.getElementById('seat-' + who);
    box.textContent = '';
    box.append(
      el('div', {}, el('div', { class: 'who' }, el('i', { style: 'background:var(--' + (who === 'H' ? 'hero' : 'villain') + ')' }), WHO[who] + ' · ' + pos),
        el('div', { class: 'stack' }, name + ' — tapis ' + num(s.stack[who]) + ' bb')),
      el('div', { class: 'hole' }, hole),
      el('div', { class: 'bet' }, s.bets[who] ? el('span', {}, 'mise ' + num(s.bets[who]) + ' bb') : ''));
  }

  function renderStep() {
    const r = current, s = steps[stepIndex];
    const reveal = document.getElementById('reveal');
    const revealed = (s.reveal || reveal.checked) && r.vc.length > 0;
    // Ordre visuel : lui en haut, toi en bas
    seat('V', s, revealed);
    seat('H', s, revealed);
    const board = document.getElementById('board');
    board.textContent = '';
    r.b.slice(0, s.boardN).forEach((c) => board.append(card(c)));
    for (let k = s.boardN; k < 5; k++) board.append(el('span', { class: 'slot' }));
    document.getElementById('pot').textContent = 'Pot ' + num(s.pot) + ' bb' + (s.kind === 'end' && r.rake ? ' (rake ' + num(r.rake, 2) + ' bb)' : '');
    document.getElementById('now').textContent = s.text + (s.t !== undefined && s.t !== null && s.kind === 'action' ? '  (' + s.t + ' s)' : '');
    const code = s.street;
    const info = [];
    if (code !== 'p' && r.hd[code]) info.push('Ta main : ' + r.hd[code]);
    if (revealed && code !== 'p' && r.vd[code]) info.push('Sa main : ' + r.vd[code]);
    if (revealed && r.eq[code] !== undefined) info.push('Ton équité : ' + (code === 'p' ? '≈ ' : '') + Math.round(100 * r.eq[code]) + ' %');
    document.getElementById('info').textContent = info.join(' · ');
    document.getElementById('p-pos').textContent = (stepIndex + 1) + ' / ' + steps.length;
    document.getElementById('b-first').disabled = document.getElementById('b-prev').disabled = stepIndex === 0;
    document.getElementById('b-last').disabled = document.getElementById('b-next').disabled = stepIndex === steps.length - 1;
    document.querySelectorAll('#log button').forEach((b) => b.setAttribute('aria-current', +b.dataset.step === stepIndex ? 'true' : 'false'));
    renderGto();
  }

  function renderLog() {
    const r = current;
    const log = document.getElementById('log');
    log.textContent = '';
    steps.forEach((s, k) => {
      if (s.kind === 'street' || k === 0) {
        const head = el('h3', {}, k === 0 ? 'Préflop' : STREETS[s.street]);
        if (s.boardN) head.append(cards(r.b.slice(0, s.boardN)));
        log.append(head);
        if (s.kind === 'street') return;
      }
      const label = s.kind === 'action'
        ? [el('span', { class: 'tag-' + s.who.toLowerCase() }, WHO[s.who]), s.text.slice(WHO[s.who].length), s.t !== null && s.t !== undefined ? el('span', { class: 't' }, '  ' + s.t + ' s') : '']
        : [s.text];
      log.append(el('button', { type: 'button', 'data-step': k, onclick: () => go(k) }, label));
    });
  }

  function go(k) {
    if (!current) return;
    stepIndex = Math.max(0, Math.min(steps.length - 1, k));
    renderStep();
  }

  function close() {
    document.getElementById('player').hidden = true;
    current = null;
    markCurrent();
    writeHash();
  }


  // ---------- solveur GTOpen (application seulement) ----------
  const GTO = { state: {}, timer: null };
  const GRID_RANKS = 'AKQJT98765432';
  const VERDICT_CLASS = { 'principale': 'v-main', 'secondaire': 'v-mixed', 'écart': 'v-dev' };
  const pct = (x) => Math.round(100 * x) + ' %';
  const duration = (s) => (s >= 60 ? Math.floor(s / 60) + ' min ' : '') + Math.round(s % 60) + ' s';

  async function gtoCall(url, body) {
    const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Erreur ' + res.status);
    return data;
  }

  function gtoEligible(r) {
    return DATA.solver && r && r.b.length >= 3;
  }

  function gtoLoad(r, start) {
    clearTimeout(GTO.timer);
    if (!gtoEligible(r)) { renderGto(); return; }
    gtoCall('/api/resoudre', { hand: r.id, start: !!start })
      .then((v) => gtoUpdate(r.id, v))
      .catch((e) => gtoUpdate(r.id, { state: 'error', error: e.message }));
  }

  function gtoUpdate(id, v) {
    GTO.state[id] = v;
    if (!current || current.id !== id) return;
    renderGto();
    clearTimeout(GTO.timer);
    if (v.state === 'running' || v.state === 'waiting') {
      GTO.timer = setTimeout(() => gtoCall('/api/resoudre/' + encodeURIComponent(v.job))
        .then((w) => gtoUpdate(id, w)).catch((e) => gtoUpdate(id, { state: 'error', error: e.message })), 1500);
    }
  }

  function gtoColors(actions) {
    const shades = ['var(--g-bet1)', 'var(--g-bet2)', 'var(--g-bet3)', 'var(--g-bet4)'];
    const sized = actions.map((a, k) => k).filter((k) => (actions[k].kind === 'bet' || actions[k].kind === 'raise') && !actions[k].allin);
    return actions.map((a, k) => {
      if (a.kind === 'fold') return 'var(--g-fold)';
      if (a.kind === 'check' || a.kind === 'call') return 'var(--g-pass)';
      if (a.allin) return 'var(--g-allin)';
      const rank = sized.indexOf(k);
      return shades[sized.length === 1 ? 1 : Math.round(rank * 3 / (sized.length - 1))];
    });
  }

  function classOf(combo) {
    const r1 = combo[0], r2 = combo[2];
    if (r1 === r2) return r1 + r2;
    const [hi, lo] = GRID_RANKS.indexOf(r1) < GRID_RANKS.indexOf(r2) ? [r1, r2] : [r2, r1];
    return hi + lo + (combo[1] === combo[3] ? 's' : 'o');
  }

  function gtoGrid(d, colors) {
    const grid = el('div', { class: 'g-grid', role: 'img', 'aria-label': 'Stratégie de la range par main' });
    const mine = d.combo ? classOf(d.combo) : null;
    for (let i = 0; i < 13; i++) {
      for (let j = 0; j < 13; j++) {
        const hand = i === j ? GRID_RANKS[i] + GRID_RANKS[i] : i < j ? GRID_RANKS[i] + GRID_RANKS[j] + 's' : GRID_RANKS[j] + GRID_RANKS[i] + 'o';
        const c = d.classes[hand];
        const cell = el('div', { class: 'g-c' + (c ? '' : ' out') + (hand === mine ? ' me' : '') }, el('span', {}, hand));
        if (c) {
          const [w, ...s] = c;
          let x = 0;
          const stops = [];
          s.forEach((f, k) => { if (f > 0) { stops.push(colors[k] + ' ' + (100 * x).toFixed(1) + '% ' + (100 * (x + f)).toFixed(1) + '%'); x += f; } });
          if (stops.length) cell.style.background = 'linear-gradient(to right, ' + stops.join(', ') + ') bottom / 100% ' + Math.max(8, Math.round(100 * Math.min(1, w))) + '% no-repeat, var(--g-empty)';
          if (w < 0.85) cell.classList.add('low');  // libellé sur le fond vide : texte sombre
          cell.title = hand + ' : ' + s.map((f, k) => d.actions[k].label + ' ' + pct(f)).join(' · ') + ' · présence ' + pct(Math.min(1, w));
        }
        grid.append(cell);
      }
    }
    return grid;
  }

  function gtoRevealed() {
    const s = steps[stepIndex];
    return document.getElementById('reveal').checked || !!(s && s.reveal);
  }

  // Sa main n'est montrée que si le replayer la dévoile (case « Montrer sa main » ou abattage).
  function gtoVisible(d) {
    if (d.who === 'H' || gtoRevealed()) return d;
    return Object.assign({}, d, { combo: null, strategy: null, evs: null, verdict: null, frequency: null, ev_loss: null });
  }

  function gtoDetail(d) {
    d = gtoVisible(d);
    const colors = gtoColors(d.actions);
    const mine = d.who === 'H';
    const box = el('div', { class: 'gto-detail' });
    const handCards = d.combo ? cards([d.combo.slice(0, 2), d.combo.slice(2)]) : '';
    box.append(el('div', { class: 'title' }, STREETS[d.street] + ' · ' + WHO[d.who] + ' ', handCards, ' — ' + (mine ? 'tu as joué : ' : 'il a joué : ') + d.played));
    if (d.chosen === null) box.append(el('div', { class: 'muted' }, 'Cette action n\'existe pas dans l\'arbre résolu.'));
    else if (d.approx) box.append(el('div', { class: 'muted' }, 'Taille la plus proche dans l\'arbre : ' + d.actions[d.chosen].label + '.'));
    const head = [el('th', {}, 'Action')];
    if (d.strategy) head.push(el('th', {}, mine ? 'Ta main' : 'Sa main'));
    head.push(el('th', {}, mine ? 'Ta range' : 'Sa range'));
    if (d.evs) head.push(el('th', {}, 'EV (bb)'));
    const rows = d.actions.map((a, k) => {
      const tds = [el('td', {}, el('span', { class: 'sw', style: 'background:' + colors[k] }), a.label)];
      if (d.strategy) tds.push(el('td', {}, pct(d.strategy[k])));
      tds.push(el('td', {}, pct(d.range[k])));
      if (d.evs) tds.push(el('td', {}, d.evs[k] === null ? '—' : num(d.evs[k], 2)));
      return el('tr', { class: k === d.chosen ? 'chosen' : '' }, tds);
    });
    box.append(el('table', {}, el('thead', {}, el('tr', {}, head)), el('tbody', {}, rows)));
    if (d.verdict) {
      const text = (mine ? 'Avec ta main' : 'Avec sa main') + ', le solveur joue « ' + d.actions[d.chosen].label + ' » ' + pct(d.frequency) + ' du temps : ';
      const loss = d.ev_loss > 0.005 ? ' · perte d\'EV ≈ ' + num(d.ev_loss, 2) + ' bb' : '';
      box.append(el('p', {}, text, el('b', { class: VERDICT_CLASS[d.verdict] }, 'action ' + d.verdict), loss));
      if (d.reach !== null && d.reach < 0.05) box.append(el('p', { class: 'muted' }, 'Le solveur n\'arrive presque jamais ici avec cette main : stratégie à lire avec prudence.'));
    } else if (!mine) {
      box.append(el('p', { class: 'muted' }, 'Sa main est inconnue : la colonne « Sa range » montre ce que le solveur fait avec toutes les mains qu\'il peut avoir ici.'));
    }
    box.append(gtoGrid(d, colors));
    box.append(el('div', { class: 'g-note' }, 'Grille : ' + (mine ? 'ta' : 'sa') + ' range à ce moment du coup ; couleurs = actions, hauteur = part de la main encore présente.'));
    return box;
  }

  function renderGto() {
    const box = document.getElementById('gto');
    box.textContent = '';
    const r = current;
    const v = r && GTO.state[r.id];
    if (!gtoEligible(r) || !v) { box.hidden = true; return; }
    box.hidden = false;
    box.append(el('h3', {}, 'Solveur GTOpen'));
    const solver = v.solver || {};
    if (v.state === 'unsupported') { box.append(el('p', { class: 'muted' }, v.message)); return; }
    if (v.state === 'unavailable' || (v.state === 'absent' && solver.ready === false)) {
      box.append(el('p', {}, v.message || solver.message));
      box.append(el('p', {}, 'Commande : ', el('code', {}, v.install || solver.install)));
      return;
    }
    if (v.state === 'absent' || v.state === 'cancelled' || v.state === 'error') {
      if (v.error) box.append(el('p', { class: 'err' }, v.error));
      box.append(el('p', { class: 'muted' }, 'Calcule la stratégie d\'équilibre à partir du flop (ranges de ta solution préflop, tailles jouées dans la main) puis la compare à chaque décision du coup. Compte quelques minutes ; tu peux continuer à naviguer pendant le calcul.'));
      box.append(el('button', { type: 'button', class: 'go', onclick: () => gtoLoad(r, true) }, v.state === 'error' ? 'Réessayer' : 'Résoudre ce coup'));
      return;
    }
    if (v.state === 'waiting' || v.state === 'running') {
      const p = v.progress || {};
      const frac = p.iteration ? Math.min(1, p.iteration / v.max_iterations) : 0;
      const text = v.state === 'waiting' ? 'En attente : une autre résolution est en cours.'
        : (p.iteration ? 'Itération ' + p.iteration + ' / ' + v.max_iterations + ' · exploitabilité ' + num(p.exploit_pct, 1) + ' % du pot (objectif ' + num(v.target, 1) + ' %)'
          : 'Construction de l\'arbre' + (p.tree_nodes ? ' (' + p.tree_nodes.toLocaleString('fr-FR') + ' nœuds)' : '') + '…')
          + (v.elapsed ? ' · ' + duration(v.elapsed) : '');
      box.append(el('p', {}, text), el('div', { class: 'gto-bar' }, el('span', { style: 'width:' + (100 * frac).toFixed(1) + '%' })));
      box.append(el('button', { type: 'button', class: 'stop', onclick: () => gtoCall('/api/resoudre/' + encodeURIComponent(v.job) + '/arreter', {}).then((w) => gtoUpdate(r.id, w)) }, 'Arrêter'));
      return;
    }
    const res = v.result;
    box.append(el('p', {}, el('b', {}, res.pot_type), ' · ' + res.iterations + ' itérations · exploitabilité ' + num(res.exploit_pct, 2) + ' % du pot · ' + duration(res.seconds)));
    box.append(el('p', { class: 'muted' }, 'Tailles de l\'arbre — ' + res.menu + '.'));
    if (res.added.length) box.append(el('p', { class: 'muted' }, (res.added.includes('H') ? 'Ta main' : 'Sa main') + ' n\'était pas dans la range du solveur : elle a été ajoutée avec un poids infime pour lire sa stratégie.'));
    if (res.stopped) box.append(el('p', { class: 'err' }, 'Suivi de la ligne interrompu : ' + res.stopped));
    const s = steps[stepIndex];
    const list = el('div', { class: 'gto-list' });
    res.decisions.map(gtoVisible).forEach((d) => {
      const k = steps.findIndex((x) => x.kind === 'action' && x.i === d.i);
      const badge = d.chosen === null ? el('span', { class: 'muted' }, 'hors de l\'arbre')
        : d.verdict ? el('span', { class: VERDICT_CLASS[d.verdict] }, d.verdict + (d.frequency !== null ? ' ' + pct(d.frequency) : ''))
          : el('span', { class: 'muted' }, 'range : ' + pct(d.range[d.chosen]));
      list.append(el('button', { type: 'button', 'aria-current': s && s.i === d.i ? 'true' : 'false', onclick: () => go(k) },
        el('span', { class: 'st' }, STREETS[d.street]), el('span', { class: 'tag-' + d.who.toLowerCase() }, WHO[d.who]), d.played, badge));
    });
    box.append(list);
    const d = s && s.kind === 'action' ? res.decisions.find((x) => x.i === s.i) : null;
    if (d) box.append(gtoDetail(d));
    else box.append(el('p', { class: 'muted' }, 'Clique sur une décision (ou avance dans le coup) pour voir la stratégie du solveur.'));
  }

  // ---------- URL ----------
  function writeHash() {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(F)) if (v && v !== DEFAULTS[k]) params.set(k, v);
    if (current) params.set('hand', current.id);
    const hash = params.toString();
    history.replaceState(null, '', hash ? '#' + hash : location.pathname + location.search);
    const link = document.getElementById('p-link');
    if (current) link.href = '#hand=' + encodeURIComponent(current.id);
  }

  function readHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const values = {};
    for (const [k, v] of params) if (k !== 'hand') values[k] = v;
    setFilters(values);
    const id = params.get('hand');
    const r = id && R.find((x) => x.id === id);
    if (r) open(r);
  }

  // ---------- démarrage ----------
  document.getElementById('meta').textContent = R.length + ' mains · toi : ' + DATA.hero + ' ·';
  buildFilters();
  document.getElementById('b-first').onclick = () => go(0);
  document.getElementById('b-prev').onclick = () => go(stepIndex - 1);
  document.getElementById('b-next').onclick = () => go(stepIndex + 1);
  document.getElementById('b-last').onclick = () => go(steps.length - 1);
  document.getElementById('p-close').onclick = close;
  document.getElementById('reveal').onchange = renderStep;  // met aussi à jour le panneau du solveur
  document.addEventListener('keydown', (e) => {
    if (!current || ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
    if (e.key === 'ArrowRight') { go(stepIndex + 1); e.preventDefault(); }
    else if (e.key === 'ArrowLeft') { go(stepIndex - 1); e.preventDefault(); }
    else if (e.key === 'Home') { go(0); e.preventDefault(); }
    else if (e.key === 'End') { go(steps.length - 1); e.preventDefault(); }
    else if (e.key === 'Escape') close();
  });
  window.addEventListener('hashchange', readHash);
  readHash();
})();
"""
