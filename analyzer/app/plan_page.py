"""Page « Plan de jeu suggéré » : les études résolues réduites à des règles simples (voir theory/coach.py), en
heads-up et aux tables à plusieurs (6-max, une famille par paire de positions et type de pot).

Pour chaque famille : en attaque, les niveaux du plan (du plus simple au plus précis, chacun avec ce qu'il coûte),
puis les flops regroupés en trois stratégies de c-bet (miser range, stratégie mixte, checker range) et par taille,
avec des flops en exemple (ouverts dans l'explorateur), et la suite à la turn et à la river selon la carte (la taille
préférée comprise) ; en défense, la réponse de l'autre joueur ; contre le field, le plan ajusté aux leaks d'une
population de l'Étude du field (field_plan)."""
from __future__ import annotations

import json
from html import escape
from typing import Optional
from urllib.parse import quote

from .. import field_plan, stakes
from ..field import STYLES
from ..report import cards_html, html_page, num
from ..theory import coach, postflop, ring_ranges, studyspots

STYLE = """
.pl-switch { display: flex; flex-direction: column; gap: 8px; margin: 4px 0 12px; }
.pl-row { display: flex; align-items: flex-start; gap: 6px 14px; }
.pl-fmt { font-weight: 600; font-size: 13px; min-width: 70px; padding-top: 6px; }
.pl-pairs { display: flex; flex-wrap: wrap; gap: 8px 16px; flex: 1; }
.pl-pair { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pl-pair > span { font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.pl-fams { display: flex; gap: 0; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; width: fit-content; }
.pl-fams button { font: inherit; font-size: 13px; padding: 5px 14px; border: 0; border-right: 1px solid var(--border);
  background: var(--surface); color: var(--ink); cursor: pointer; }
.pl-fams button:last-child { border-right: 0; }
.pl-fams button[aria-pressed="true"] { background: var(--series-1); color: #fff; }
.pl-head { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; }
.pl-head button { font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.pl-head button.go { background: var(--series-1); border-color: var(--series-1); color: #fff; font-weight: 600; }
.pl-status { font-size: 12px; color: var(--ink-2); }
details.pl-chat > summary { cursor: pointer; margin-bottom: 8px; }
details.pl-group { border: 1px solid var(--border); border-radius: 10px; background: var(--surface); margin: 10px 0; }
details.pl-group > summary { cursor: pointer; padding: 10px 14px; font-weight: 600; display: flex; gap: 10px;
  align-items: baseline; flex-wrap: wrap; }
details.pl-group > summary .sub { font-weight: 400; font-size: 13px; color: var(--ink-2); }
details.pl-sub { border-top: 1px solid var(--border); margin-top: 8px; }
details.pl-sub > summary { cursor: pointer; padding: 8px 0 4px; font-weight: 600; font-size: 14px; }
details.pl-sub > div { padding-bottom: 4px; }
.pl-body { padding: 0 14px 12px; }
.pl-body h3 { font-size: 14px; margin: 14px 0 6px; }
.pl-flops { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 8px; }
.pl-flops a { text-decoration: none; border: 1px solid var(--border); border-radius: 6px; padding: 2px 4px; }
.pl-flops a:hover { border-color: var(--series-1); }
.pl-why { font-size: 13px; color: var(--ink-2); background: var(--page); border-radius: 8px; padding: 8px 10px; margin: 6px 0; }
ul.rules { margin: 4px 0; padding-left: 18px; font-size: 13px; }
ul.rules li { margin: 2px 0; }
ul.rules b { font-weight: 600; }
table.pl-cards td { vertical-align: top; font-size: 13px; }
table.pl-cards td.rules-cell { min-width: 280px; }
table.pl-cards ul.rules { margin: 0; padding-left: 16px; }
.pl-strats { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; margin: 6px 0 4px; }
.pl-strat { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--surface);
  display: flex; flex-direction: column; gap: 8px; }
.pl-strat .st-head { display: flex; flex-direction: column; gap: 2px; }
.pl-strat .st-head b { font-size: 15px; }
.pl-strat .st-head small { font-size: 12px; color: var(--ink-2); }
.st-size { display: block; text-decoration: none; color: var(--ink); border-radius: 8px; padding: 6px 8px; }
a.st-size:hover { outline: 1px solid var(--series-1); }
.st-size b { font-weight: 600; }
.st-size span { display: block; font-size: 12px; color: var(--ink-2); }
.pl-ex { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.pl-ex a { text-decoration: none; border: 1px solid var(--border); border-radius: 6px; padding: 1px 3px; background: var(--surface); }
.pl-ex a:hover { border-color: var(--series-1); }
.st-range { background: var(--hi-bg); }
.st-mixte { background: color-mix(in srgb, var(--hi-bg) 45%, transparent); }
.st-check { background: var(--lo-bg); }
.pl-sides { display: flex; gap: 0; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; width: fit-content; margin: 8px 0 4px; }
.pl-sides button { font: inherit; font-size: 14px; padding: 6px 16px; border: 0; border-right: 1px solid var(--border);
  background: var(--surface); color: var(--ink); cursor: pointer; }
.pl-sides button:last-child { border-right: 0; }
.pl-sides button[aria-pressed="true"] { background: var(--ink); color: var(--surface); }
.pl-lvl { font-size: 12px; font-weight: 600; border-radius: 6px; padding: 1px 8px; }
.pl-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 8px; }
.pl-chip { font-size: 13px; border-radius: 999px; padding: 3px 10px; border: 1px solid var(--border); white-space: nowrap; }
.pl-chip b { font-weight: 600; margin-right: 4px; }
.pl-chip.up { background: var(--hi-bg); border-color: transparent; }
.pl-chip.down { background: var(--lo-bg); border-color: transparent; }
.pl-chip.mix { border-style: dashed; }
table.pl-matrix td { vertical-align: middle; }
table.pl-matrix .pl-chip { font-size: 12px; padding: 2px 8px; }
.pl-size b { font-weight: 600; white-space: nowrap; }
.pl-size div { font-size: 12px; color: var(--ink-2); }
.pl-ladder { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; margin: 4px 0 6px; }
.pl-ladder a { display: block; text-decoration: none; color: var(--ink); border: 1px solid var(--border);
  border-radius: 10px; padding: 8px 10px; background: var(--surface); }
.pl-ladder a:hover { border-color: var(--series-1); }
.pl-ladder .n { font-size: 12px; color: var(--ink-2); }
.pl-ladder .v { font-size: 18px; font-weight: 600; margin-top: 2px; }
.pl-ladder .v small { font-size: 12px; font-weight: 400; color: var(--ink-2); }
.pl-ladder .s { font-size: 12px; color: var(--ink-2); margin-top: 2px; }
details.pl-street > summary { font-weight: 600; font-size: 13px; }
details.pl-street > summary .sub { font-weight: 400; color: var(--ink-2); margin-left: 6px; }
.pl-tag { font-size: 11px; font-weight: 600; border-radius: 6px; padding: 1px 6px; background: var(--hi-bg); margin-left: 6px; }
.pl-refine { border: 1px dashed var(--border); border-radius: 8px; padding: 8px 10px; margin: 10px 0 4px; }
.pl-refine button { font: inherit; font-size: 13px; padding: 4px 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.pl-refine .pl-refine-go { background: var(--series-1); border-color: var(--series-1); color: #fff; font-weight: 600; }
.pl-rule { font-size: 16px; font-weight: 600; margin: 4px 0 6px; }
.pl-level h3 { margin-top: 16px; }
table.pl-lv td { vertical-align: middle; font-size: 13px; }
.pl-pops { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 10px; }
.pl-pops button { font: inherit; font-size: 13px; padding: 4px 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.pl-pops button[aria-pressed="true"] { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.pl-bands { font-size: 13px; margin: 0 0 8px; }
.pl-bands a[aria-current="page"] { font-weight: 600; color: var(--ink); text-decoration: none; }
ul.pl-tweaks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
ul.pl-tweaks li { border-left: 3px solid var(--axis); padding: 1px 0 1px 10px; font-size: 13px; }
ul.pl-tweaks li.strong { border-left-color: var(--series-1); }
ul.pl-tweaks .t { font-weight: 600; font-size: 14px; }
ul.pl-tweaks .n { font-size: 12px; color: var(--muted); margin-top: 2px; }
.pl-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); gap: 12px; }
"""

SCRIPT = """
(function () {
  var box = document.getElementById('coach-box');
  if (box && window.AnalyzerCoach) {
    window.AnalyzerCoach.mount(box, { suggestions: JSON.parse(box.dataset.suggestions || '[]') });
  }
  var buttons = document.querySelectorAll('.pl-fams button');
  function show(fam) {
    buttons.forEach(function (b) { b.setAttribute('aria-pressed', b.dataset.fam === fam ? 'true' : 'false'); });
    document.querySelectorAll('section.pl-fam').forEach(function (s) { s.hidden = s.dataset.fam !== fam; });
    try { localStorage.setItem('analyzer-plan-famille', fam); } catch (e) { /* choix non retenu */ }
  }
  buttons.forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.fam); }); });
  var saved = null;
  try { saved = localStorage.getItem('analyzer-plan-famille'); } catch (e) { saved = null; }
  var first = document.querySelector('.pl-fams button[data-default]') || buttons[0];
  var wanted = (location.hash.match(/famille=([\w+-]+)/) || [])[1];  // lien d'une autre page : « #famille=6max_bb_btn_srp »
  var known = function (f) { return f && document.querySelector('.pl-fams button[data-fam="' + f + '"]'); };
  show(known(wanted) ? wanted : known(saved) ? saved : first.dataset.fam);

  // Attaque, défense ou contre le field, dans chaque famille (« #…&cote=field » : celui-là d'emblée)
  function side(b) {
    var fam = b.closest('section.pl-fam');
    fam.querySelectorAll('.pl-sides button').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
    fam.querySelectorAll('.pl-side').forEach(function (s) { s.hidden = s.dataset.side !== b.dataset.side; });
  }
  document.querySelectorAll('.pl-sides button').forEach(function (b) {
    b.addEventListener('click', function () { side(b); });
  });
  var cote = (location.hash.match(/cote=(\w+)/) || [])[1];
  if (cote) document.querySelectorAll('.pl-sides button[data-side="' + cote + '"]').forEach(side);
  // La population du field (réguliers, récréatifs, un style), retenue
  function pop(key) {
    document.querySelectorAll('.pl-pops').forEach(function (bar) {
      var known = bar.querySelector('button[data-pop="' + key + '"]') ? key : bar.querySelector('button').dataset.pop;
      bar.querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', x.dataset.pop === known ? 'true' : 'false'); });
      bar.parentNode.querySelectorAll('.pl-pop').forEach(function (s) { s.hidden = s.dataset.pop !== known; });
    });
    try { localStorage.setItem('analyzer-plan-pop', key); } catch (e) { /* choix non retenu */ }
  }
  document.querySelectorAll('.pl-pops button').forEach(function (b) {
    b.addEventListener('click', function () { pop(b.dataset.pop); });
  });
  var savedPop = null;
  try { savedPop = localStorage.getItem('analyzer-plan-pop'); } catch (e) { savedPop = null; }
  if (document.querySelector('.pl-pops')) pop(savedPop || 'reg');
  // Un clic sur un groupe ouvre son détail et y descend
  document.querySelectorAll('a.pl-jump').forEach(function (a) {
    a.addEventListener('click', function () {
      var target = document.querySelector(a.getAttribute('href'));
      if (target) target.open = true;
    });
  });
  // Affiner la suite d'un niveau au solveur : une tâche de la file par niveau, suivie jusqu'au bout
  var boxes = document.querySelectorAll('.pl-refine'), rtimer = null, seen = {};
  function rpost(url, box) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ famille: box.dataset.fam, niveau: box.dataset.level }) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || 'Erreur.'); return j; }); });
  }
  function rshow(s) {
    var busy = false, reload = false;
    boxes.forEach(function (box) {
      var key = box.dataset.fam + '|' + box.dataset.level, job = s.jobs[key];
      var go = box.querySelector('.pl-refine-go'), stop = box.querySelector('.pl-refine-stop'), st = box.querySelector('.pl-status');
      var active = !!job && (job.state === 'waiting' || job.state === 'running');
      busy = busy || active;
      go.hidden = active; go.disabled = !s.ready; stop.hidden = !active;
      if (active) {
        var p = job.progress || {};
        seen[key] = true;
        st.textContent = job.state === 'waiting' ? 'En file, après les résolutions en cours…'
          : 'Flop ' + Math.min((p.done || 0) + 1, p.total) + ' sur ' + p.total + (p.stage ? ' (' + p.stage + ')' : '')
            + (p.iteration ? ' · itération ' + p.iteration : '');
      } else if (job && job.state === 'done' && seen[key]) reload = true;
      else if (job && job.state === 'error') st.textContent = job.error || 'Erreur.';
      else st.textContent = s.ready ? '' : 'Installe d\\'abord le solveur : python -m analyzer gtopen --installer';
    });
    if (reload) { location.reload(); return; }
    clearTimeout(rtimer);
    if (busy) rtimer = setTimeout(rrefresh, 3000);
  }
  function rrefresh() { fetch('/api/plan/affiner').then(function (r) { return r.json(); }).then(rshow); }
  boxes.forEach(function (box) {
    var go = box.querySelector('.pl-refine-go');
    go.addEventListener('click', function () {
      go.disabled = true;
      rpost('/api/plan/affiner', box).then(rshow).catch(function (e) {
        go.disabled = false; box.querySelector('.pl-status').textContent = e.message;
      });
    });
    box.querySelector('.pl-refine-stop').addEventListener('click', function () { rpost('/api/plan/affiner/arreter', box).then(rshow); });
  });
  if (boxes.length) rrefresh();
  var head = document.querySelector('.pl-head');
  if (!head) return;
  var run = head.querySelector('.pl-run'), stop = head.querySelector('.pl-stop'), status = head.querySelector('.pl-status');
  var missing = Number(head.dataset.missing), timer = null;
  function post(url) { return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(function (r) { return r.json(); }); }
  function show2(s) {
    if (s.missing !== missing && !s.busy) { location.reload(); return; }
    status.textContent = '';
    if (run) { run.hidden = !!s.busy || !s.missing; run.disabled = !s.ready; }
    if (stop) stop.hidden = !s.busy;
    if (!s.ready) status.textContent = 'Installe d\\'abord le solveur : python -m analyzer gtopen --installer';
    else if (s.busy) status.textContent = (s.current ? s.current.spot.replace('spot:', '') + ' : ' + (s.current.stage || '…') + ' · ' : '')
      + s.busy + ' étude(s) en file';
    clearTimeout(timer);
    if (s.busy) timer = setTimeout(refresh, 2500);
  }
  function refresh() { fetch('/api/plan').then(function (r) { return r.json(); }).then(show2); }
  if (run) run.addEventListener('click', function () { run.disabled = true; post('/api/plan/preparer').then(show2); });
  if (stop) stop.addEventListener('click', function () { post('/api/plan/arreter').then(show2); });
  refresh();
})();
"""


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _de(who: str) -> str:
    """« le bouton » -> « du bouton », « la BB » -> « de la BB »."""
    return "du " + who[3:] if who.startswith("le ") else "de " + who


def _pct(x: float) -> str:
    return f"{round(100 * x)} %"


def _chip(rule: dict, defender: bool = False, label: bool = True) -> str:
    """Une famille de mains et ce qu'elle fait : « Fortes · mise 92 % »."""
    kind, pct = rule["kind"], _pct(rule["pct"])
    if defender:
        text = {"raise": f"relance {pct}", "call": f"paie {pct}", "fold": f"folde {pct}"}.get(
            kind, f"{rule['verdict'].lower()} (paie {pct})")
        cls = {"raise": "up", "fold": "down", "call": "flat"}.get(kind, "mix")
    else:
        verdict = rule["verdict"]
        if kind == "bet":
            text = ("bluff " if verdict == "Bluffe" else "valeur " if verdict.startswith("Mise (") else "mise ") + pct
        elif kind == "check":
            text = f"checke (mise {pct})"
        else:
            text = f"mélange (mise {pct})"
        if kind != "check" and rule.get("size") in coach.SIZE_SHORT:
            text += " · " + coach.SIZE_SHORT[rule["size"]]
        cls = {"bet": "up", "check": "down"}.get(kind, "mix")
    name = f'<b>{escape(rule["label"])}</b> ' if label else ""
    return (f'<span class="pl-chip {cls}" title="{escape(rule["label"])} : {_pct(rule["share"])} de la range">'
            f'{name}{escape(text)}</span>')


def _chips(rules: list[dict], defender: bool = False) -> str:
    if not rules:
        return '<p class="muted">Pas assez de données.</p>'
    return '<div class="pl-chips">' + "".join(_chip(r, defender) for r in rules) + "</div>"


def _size_text(size: dict) -> str:
    """« grosse (75 %) »."""
    pcts = " / ".join(str(x) for x in size["pcts"])
    return size["label"] + (f" ({pcts} %)" if pcts else "")


def _sizes_cell(row: dict) -> str:
    """La taille préférée à cette carte (sa part des mises), puis les autres."""
    if not row["sizes"]:
        return '<span class="muted">–</span>'
    first, rest = row["sizes"][0], row["sizes"][1:]
    others = " · ".join(f"{_size_text(x)} {_pct(x['share'])}" for x in rest if x["share"] >= 0.05)
    return (f'<div class="pl-size"><b>{escape(_size_text(first))}</b> · {_pct(first["share"])} des mises'
            + (f"<div>{escape(others)}</div>" if others else "") + "</div>")


def _matrix(rows: list[dict], street: str, measure: str, defender: bool = False) -> str:
    """Selon la carte de turn ou de river : la fréquence de mise (ou de fold), la taille préférée (mises) et ce que
    fait chaque famille."""
    if not rows:
        return '<p class="muted">Pas encore de données.</p>'
    groups = [(k, label) for k, label, _, _ in coach.GROUPS if any(r["key"] == k for row in rows for r in row["rules"])]
    head = "".join(f"<th>{escape(label)}</th>" for _, label in groups)
    size_head = "" if defender else "<th>Taille préférée</th>"
    body = []
    for row in rows:
        by = {r["key"]: r for r in row["rules"]}
        cells = "".join(f"<td>{_chip(by[k], defender, label=False) if k in by else ''}</td>" for k, _ in groups)
        value = row["fold"] if defender else row["aggr"]
        size = "" if defender else f"<td>{_sizes_cell(row)}</td>"
        body.append(f'<tr><td><b>{escape(row["label"])}</b><div class="small muted">{escape(row["text"])}</div></td>'
                    f'<td class="num">{_pct(value)}</td>{size}{cells}</tr>')
    return (f'<div class="scroll"><table class="stats pl-matrix"><thead><tr><th>{escape(street)}</th>'
            f'<th class="num">{escape(measure)}</th>{size_head}{head}</tr></thead><tbody>{"".join(body)}</tbody>'
            "</table></div>")


def _details_rules(rules: list[tuple[str, str]]) -> str:
    if not rules:
        return '<p class="muted">Pas assez de données.</p>'
    return '<ul class="rules">' + "".join(f"<li><b>{escape(what)}</b> : {escape(who)}</li>" for what, who in rules) + "</ul>"


def _examples(rows: list[dict], n: int = 4) -> list[dict]:
    """Des flops en exemple, de catégories différentes d'abord."""
    seen, first, rest = set(), [], []
    for r in rows:
        (rest if r["category"] in seen else first).append(r)
        seen.add(r["category"])
    return (first + rest)[:n]


def _links(rows: list[dict], cls: str = "pl-flops") -> str:
    """Les flops, chacun ouvrant son étude dans l'explorateur."""
    return f'<div class="{cls}">' + "".join(
        f'<a href="/explorateur/{quote(r["id"], safe="")}" target="_blank" rel="noopener" '
        f'title="c-bet {_pct(r["cbet"])} · ouvrir l\'étude dans l\'explorateur">{cards_html(r["board"])}</a>'
        for r in rows) + "</div>"


def _categories(g: dict) -> str:
    return ", ".join(f"{label.lower()} ({n})" if n > 1 else label.lower() for label, n in g["categories"])


def _facing_text(d: Optional[dict]) -> str:
    if not d:
        return "pas encore de données"
    return f'folde {_pct(d["fold"])} · paie {_pct(d["call"])} · relance {_pct(d["raise"])}'


def _overview(plan: dict, side: str) -> str:
    """Les trois stratégies de c-bet, chacune avec ses tailles, leurs chiffres et des flops en exemple."""
    by_key = {g["key"]: g for g in plan["groups"]}
    cards = []
    for st in plan["strategies"]:
        items = []
        for key in st["groups"]:
            g = by_key[key]
            figures = (f'c-bet {_pct(g["cbet"])}' if side == "attaque" else _facing_text(g["defense"]["vs_cbet"]))
            items.append(f'<div><a class="st-size st-{st["key"]} pl-jump" href="#{side[:3]}-{plan["family"]}-{key}">'
                         f'<b>{escape(_cap(g["size_text"]))}</b><span>{escape(figures)} · {len(g["flops"])} flop(s)'
                         f'</span></a>{_links(_examples(g["flops"]), "pl-ex")}</div>')
        if not items:
            shares = [r["cbet"] for r in plan["flops"]]
            why = ""
            if shares and st["key"] == "check":
                why = f" : sur ces flops, {plan['who']} mise toujours au moins {_pct(min(shares))}"
            elif shares and st["key"] == "range":
                why = f" : sur ces flops, {plan['who']} mise au plus {_pct(max(shares))}"
            items.append(f'<p class="muted small">Aucun flop résolu ne s\'y range{escape(why)}.</p>')
        title = st["label"] if side == "attaque" else "Face à : " + st["label"].lower()
        cards.append(f'<div class="pl-strat"><div class="st-head"><b>{escape(title)}</b>'
                     f'<small>{escape(st["text"])}</small></div>{"".join(items)}</div>')
    return f'<div class="pl-strats">{"".join(cards)}</div>'


def _sub(title: str, content: str) -> str:
    return f'<details class="pl-sub"><summary>{escape(title)}</summary><div>{content}</div></details>'


def _attack(plan: dict, g: dict) -> str:
    who = plan["who"]
    at = g["attack"]
    facing_raise = "face à la relance" if coach.aggressor_of(plan["family"]) == 0 else "face au check-raise"
    vs_xr = (f'<h3>{escape(_cap(who))} {facing_raise} : folde {_pct(at["vs_xr"]["fold"])}, '
             f'sur-relance {_pct(at["vs_xr"]["raise"])}</h3>' + _chips(at["vs_xr"]["rules"], defender=True)
             if at["vs_xr"] else "")
    summary = (f'<span class="pl-lvl st-{g["strategy"]}">{escape(g["strategy_label"])}</span> {escape(g["size_text"])}'
               f'<span class="sub">c-bet {_pct(g["cbet"])} · {len(g["flops"])} flop(s) : {escape(_categories(g))}</span>')
    delayed = _matrix(at["delayed"], "Turn", "Mise") if at["delayed"] else ""
    return f"""
<details class="pl-group" id="att-{plan["family"]}-{g["key"]}"><summary>{summary}</summary><div class="pl-body">
{_links(g["flops"])}
<div class="pl-why">{escape(g["why"])}</div>
<h3>Au flop, {escape(who)} mise {_pct(g["cbet"])} en {escape(g["size_text"])}</h3>
{_chips(at["flop"])}
<h3>À la turn, si la c-bet est payée : selon la carte, combien et quelle taille</h3>
{_matrix(at["turn"], "Turn", "Continue")}
<h3>À la river, après deux mises payées : selon la carte, combien et quelle taille</h3>
{_matrix(at["river"], "River", "Mise")}
{_sub(f"Quand {who} checke le flop : c-bet retardée à la turn", delayed) if delayed else ""}
{_sub(_cap(facing_raise), vs_xr) if vs_xr else ""}
{_sub("Détail par famille de mains (onze familles)", _details_rules(at["flop_detail"]))}
</div></details>"""


def _defense(plan: dict, g: dict) -> str:
    who, other = plan["who"], plan["other"]
    de = g["defense"]
    facing = de["vs_cbet"]
    summary = (f'Face à : <span class="pl-lvl st-{g["strategy"]}">{escape(g["strategy_label"])}</span> '
               f'{escape(g["size_text"])}<span class="sub">{escape(_facing_text(facing))} · {len(g["flops"])} flop(s) : '
               f'{escape(_categories(g))}</span>')
    checked = ""
    if de["stab"]:
        checked = (f'<h3>Quand {escape(who)} checke : {escape(other)} mise {_pct(de["stab"]["aggr"])}</h3>'
                   + _chips(de["stab"]["rules"]))
    elif de["probe"]:
        checked = (f"<h3>Flop checké par {escape(who)} : {escape(other)} mène à la turn (probe)</h3>"
                   + _matrix(de["probe"], "Turn", "Mise"))
    detail = _details_rules(facing["detail"]) if facing else '<p class="muted">Pas assez de données.</p>'
    return f"""
<details class="pl-group" id="def-{plan["family"]}-{g["key"]}"><summary>{summary}</summary><div class="pl-body">
{_links(g["flops"])}
{f'<div class="pl-why">{escape(de["text"])}</div>' if de["text"] else ""}
<h3>Face à la c-bet</h3>
{_chips(facing["rules"], defender=True) if facing else '<p class="muted">Pas encore de données.</p>'}
<h3>Face au 2e barrel, selon la turn</h3>
{_matrix(de["vs_barrel"], "Turn", "Folde", True)}
<h3>Face au 3e barrel, selon la river</h3>
{_matrix(de["vs_barrel3"], "River", "Folde", True)}
{checked}
{_sub("Détail par famille de mains face à la c-bet (onze familles)", detail)}
</div></details>"""


def _table(plan: dict) -> str:
    order = [k for k, _, _ in coach.STRATEGIES]

    def size(r: dict) -> str:
        return coach.SIZE_LABEL[r["size_group"]] + (" " + " / ".join(str(x) for x in r["sizes"]) + " %" if r["sizes"] else "")
    rows = "".join(
        f'<tr><td><a href="/explorateur/{quote(r["id"], safe="")}" target="_blank" rel="noopener">{cards_html(r["board"])}</a></td>'
        f'<td>{escape(coach.CATEGORY_LABEL[r["category"]])}</td>'
        f'<td>{escape(coach.STRATEGY_LABEL[r["strategy"]])}</td><td class="num">{_pct(r["cbet"])}</td>'
        f'<td>{escape(size(r))}</td><td class="num">{_pct(r["fold"]) if r["fold"] is not None else "–"}</td>'
        f'<td class="num">{coach._pts(r["eq_adv"])}</td><td class="num">{coach._pts(r["nut_adv"])}</td></tr>'
        for r in sorted(plan["flops"], key=lambda r: (order.index(r["strategy"]), -r["cbet"])))
    return ('<div class="scroll"><table class="stats"><thead><tr><th>Flop</th><th>Catégorie</th><th>Stratégie</th>'
            '<th class="num">C-bet</th><th>Taille</th><th class="num">Fold face à la c-bet</th><th class="num">Équité</th>'
            f'<th class="num">Nuts</th></tr></thead><tbody>{rows}</tbody></table></div>')


# --- Niveaux : du plus simple au plus précis -----------------------------------------------------------------------

LEVEL_TITLES = {"une": "Une seule règle au flop", "flop": "Selon le type de flop",
                "mains": "Selon le flop et tes mains", "detail": "En onze familles de mains"}
DEFENSE_TITLES = {"une": "Une règle par famille de mains", "flop": "Selon le type de flop",
                  "detail": "En onze familles de mains"}
ACTION_SHORT = {"fold": "folde", "check": "checke", "call": "paie", "small": "petite", "medium": "moyenne",
                "big": "grosse", "overbet": "overbet", "raise": "relance", "allin": "tapis"}
TURN_NODES = ("barrel", "delayed", "vs_barrel", "probe", "vs_probe")  # (les autres : le flop, ou la river)
RIVER_NODES = ("barrel3", "vs_barrel3")


def _loss(x: Optional[float]) -> str:
    """Une perte en part du pot : « 1,8 % du pot »."""
    if x is None:
        return "–"
    return "moins de 0,1 % du pot" if x < 0.0005 else f"{num(100 * x, 1)} % du pot"


def _action(cls: str, sizes: dict) -> str:
    """« petite (33 %) », « checke », « paie »…"""
    return ACTION_SHORT[cls] + (f" ({sizes[cls]} %)" if cls in sizes else "")


def _action_chip(cls: str, sizes: dict, label: str = "") -> str:
    kind = "down" if cls in ("check", "fold") else "flat" if cls == "call" else "up"
    name = f"<b>{escape(label)}</b> " if label else ""
    return f'<span class="pl-chip {kind}">{name}{escape(_action(cls, sizes))}</span>'


def _range_rule(who: str, cls: str, sizes: dict) -> str:
    """« Le bouton mise toute sa range en petite mise (33 %) » / « La SB checke toute sa range »."""
    if cls == "check":
        return f"{_cap(who)} checke toute sa range"
    return f"{_cap(who)} mise toute sa range en {coach.class_text(cls, sizes)}"


def _hands_of(hands: str) -> list[tuple[str, str]]:
    """Les colonnes d'une règle : toute la range, les quatre familles de mains ou les onze."""
    if hands == "range":
        return [("range", "Toute la range")]
    if hands == "familles":
        return [(k, label) for k, label, _, _ in coach.GROUPS]
    return list(coach.BUCKETS)


def _street_of(name: str) -> str:
    return "Turn" if name in TURN_NODES else "River" if name in RIVER_NODES else "Flop"


def _level_table(step: dict, sizes: dict, hands: list[tuple[str, str]], first: str = "Flop") -> str:
    """Une règle par type de flop ou de carte (et par famille de mains) : le tableau, avec la perte de chaque type
    et le nombre de flops lus qui en ont."""
    head = "".join(f"<th>{escape(label)}</th>" for _, label in hands)
    order = ([(c, label) for c, label in coach.CATEGORIES] if first == "Flop" else list(coach.CARDS))
    rows = []
    for key, label in ((k, label) for k, label in order if k in step["rules"]):
        rules = step["rules"][key]
        cells = "".join(f"<td>{_action_chip(rules[k]['class'], sizes) if k in rules else ''}</td>" for k, _ in hands)
        flops = f'<span class="muted">({step["flops"][key]})</span>'
        rows.append(f'<tr><td>{escape(label)} {flops}</td>{cells}<td class="num">{escape(_loss(step["by"][key]))}</td>'
                    "</tr>")
    what = "Flop (flops résolus)" if first == "Flop" else f"Carte de la {first.lower()} (flops)"
    return (f'<div class="scroll"><table class="stats pl-lv"><thead><tr><th>{what}</th>{head}'
            f'<th class="num">Perte</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _street_titles(plan: dict, side: str) -> dict[str, str]:
    who, other = plan["who"], plan["other"]
    ip = coach.aggressor_of(plan["family"]) == 1
    if side == "attack":
        return {"vs_xr": f"Flop · face au check-raise {_de(other)}" if ip else f"Flop · face à la relance {_de(other)}",
                "vs_stab": f"Flop · après ton check, face à la mise {_de(other)} (stab)",
                "barrel": "Turn · après la c-bet payée",
                "delayed": "Turn · après le flop checké (c-bet retardée)",
                "vs_probe": f"Turn · après le flop checké, face à la mise {_de(other)} (probe)",
                "barrel3": "River · après la c-bet et le 2e barrel payés"}
    return {"stab": f"Flop · quand {who} checke (stab)",
            "vs_barrel": "Turn · face au 2e barrel",
            "probe": f"Turn · quand {who} checke le flop (probe)",
            "vs_barrel3": "River · face au 3e barrel"}


def _plays(step: dict) -> tuple[bool, bool]:
    """(le niveau mise au flop, il y checke) : ce qui décide de la suite à montrer."""
    classes = {r["class"] for cell in step["rules"].values() for r in cell.values()}
    return any(c != "check" for c in classes), "check" in classes


def _suites(levels: dict, side: str) -> list[dict]:
    """La suite de chaque niveau : {nœud: (règles, tailles, affinée)}, celles affinées au solveur à la place des
    autres quand il y en a."""
    out = []
    for step in levels["steps"]:
        refined = step.get("refined") or {}
        suite = {name: (st, levels["street_sizes"].get(name, {}), False) for name, st in step["streets"].items()}
        if refined.get("flops"):
            suite.update({name: (st, refined["street_sizes"].get(name, {}), True)
                          for name, st in refined["streets"].items()})
        out.append(suite)
    return out


def _street_block(title: str, street: dict, sizes: dict, refined: bool, same: Optional[int], opened: bool,
                  first: str) -> str:
    tag = ' <span class="pl-tag">affinée au solveur</span>' if refined else ""
    summary = f'{escape(title)} <span class="sub">perte {escape(_loss(street["loss"]))}</span>{tag}'
    hands = _hands_of(street["hands"])
    if same is not None:
        body = f'<p class="small muted" style="margin:2px 0">Comme au niveau {same}.</p>'
    elif not street["by_flop"]:
        rules = street["rules"].get("tous", {})
        body = '<div class="pl-chips">' + " ".join(
            _action_chip(rules[k]["class"], sizes, label) for k, label in hands if k in rules) + "</div>"
    else:
        body = _level_table(street, sizes, hands, first)
    return (f'<details class="pl-sub pl-street"{" open" if opened else ""}><summary>{summary}</summary>'
            f'<div>{body}</div></details>')


def _suite(plan: dict, levels: dict, side: str, k: int, suites: list[dict]) -> str:
    """La suite d'un niveau (k : son rang, à partir de 0) : les autres décisions de la main, flop, turn et river."""
    step, suite = levels["steps"][k], suites[k]
    titles = _street_titles(plan, side)
    bets, checks = _plays(step) if side == "attack" else (True, True)
    wanted = {"vs_xr": bets, "vs_stab": checks, "barrel": bets, "delayed": checks, "vs_probe": checks,
              "barrel3": bets}
    blocks = []
    for name in coach.SIDE_NODES[side][1:]:
        if name not in suite or not wanted.get(name, True):
            continue
        street, sizes, refined = suite[name]
        before = suites[k - 1].get(name) if k else None
        same = k if before and before[0]["rules"] == street["rules"] and before[2] == refined else None
        blocks.append(_street_block(titles[name], street, sizes, refined, same, k == 0, _street_of(name)))
    if not blocks:
        return ""
    more = (" : les autres décisions de la main, une action par famille de mains (sur toutes les cartes, puis selon "
            "la carte de la turn ou de la river aux niveaux suivants) ; la perte en % du pot de la street" if k == 0
            else "")
    return f'<p class="small" style="margin:10px 0 0"><b>La suite</b>{more}.</p>{"".join(blocks)}'


def _refine_box(plan: dict, step: dict, first: bool = False) -> str:
    """Affiner la suite de ce niveau au solveur : le bouton, son coût, et ce que l'affinage a donné (first : le
    premier niveau, qui dit pourquoi)."""
    info, done = plan.get("refine") or {}, step.get("refined") or {}
    if not info.get("flops"):
        return ""
    parts = []
    if done.get("flops"):
        boards = " ".join(cards_html(b) for b in done["boards"])
        text = (f"Suite affinée au solveur sur {done['flops']} flop(s) {boards} résolu(s) de nouveau avec ce flop "
                "imposé, l'adversaire s'y adaptant.")
        if done.get("loss") is not None:
            margin = (f" ; précision des résolutions : ± {num(100 * done['margin'], 1)} % du pot"
                      if done.get("margin") else "")
            text += (" Ce que coûte vraiment ce flop simplifié face à un adversaire qui s'adapte : "
                     f"<b>{escape(_loss(max(done['loss'], 0.0)))}</b>{margin}.")
        parts.append(text)
    if done.get("stale"):
        parts.append(f"{done['stale']} flop(s) affiné(s) avec une règle du flop d'avant (des flops lus depuis l'ont "
                     "changée) : à affiner de nouveau.")
    if first and not done.get("flops"):
        parts.append("La suite est lue aux nœuds du solveur, après sa propre stratégie du flop : avec un flop "
                     "simplifié, les ranges qui arrivent à la turn ne sont plus tout à fait les mêmes.")
    minutes = f", environ {max(1, round(info['seconds'] / 60))} min" if info.get("seconds") else ""
    what = (" (dans la file des résolutions), puis la turn et la river relues" if first else "")
    parts.append(f"Affiner : {info['flops']} flop(s) résolu(s) de nouveau avec ce flop imposé{minutes}{what}.")
    label = "Affiner de nouveau" if done.get("flops") else "Affiner au solveur"
    return (f'<div class="pl-refine" data-fam="{escape(plan["family"])}" data-level="{escape(step["key"])}">'
            f'<p class="small" style="margin:0 0 6px">{" ".join(parts)}</p>'
            f'<button type="button" class="pl-refine-go">{label}</button> '
            '<button type="button" class="pl-refine-stop" hidden>Arrêter</button> '
            '<span class="pl-status"></span></div>')


def _ladder(levels: dict, titles: dict, anchor: str, suites: list[dict]) -> str:
    """Les niveaux côte à côte, avec ce que chacun coûte au flop, puis à la turn et à la river : un clic descend à son
    détail."""
    tiles = []
    for k, st in enumerate(levels["steps"]):
        suite = suites[k]
        turn = next((suite[n][0]["loss"] for n in ("barrel", "vs_barrel", "delayed") if n in suite), None)
        river = next((suite[n][0]["loss"] for n in RIVER_NODES if n in suite), None)
        later = " · ".join(f"{label} {_loss(x).replace(' du pot', '')}"
                           for label, x in (("turn", turn), ("river", river)) if x is not None)
        tiles.append(
            f'<a href="#{anchor}-{st["key"]}"><div class="n">Niveau {k + 1} · {escape(titles[st["key"]])}</div>'
            f'<div class="v">{escape(_loss(st["loss"]).replace(" du pot", ""))} '
            '<small>du pot perdu au flop</small></div>'
            + (f'<div class="s">{escape(later)}</div>' if later else "") + "</a>")
    return f'<div class="pl-ladder">{"".join(tiles)}</div>'


def _levels_note(plan: dict, levels: dict) -> str:
    older = plan["levels"]["older"]
    more = (f" {older} flop(s) lu(s) par une version d'avant n'y sont pas encore (ou seulement pour le flop) : "
            "<b>Préparer le plan</b> les relit." if older else "")
    return (f'<p class="note">Perte : ce que coûte la règle face à la stratégie du solveur, en % du pot par main de la '
            f'range (du pot de la street pour la suite), en moyenne sur les {levels["flops"]} flop(s) résolu(s) et '
            "lu(s), face à un adversaire qui joue la stratégie du solveur sans s'adapter (un adversaire qui s'adapte "
            "ferait payer un peu plus : l'affinage au solveur le mesure). Commence par le niveau 1 et passe au suivant "
            f"quand il est acquis.{more}</p>")


def _levels_missing(plan: dict) -> str:
    return ('<div class="card"><p class="note" style="margin:0">Pour chiffrer les niveaux du plus simple au plus '
            f'précis, le coach doit relire tes études : {plan["levels"]["older"]} flop(s) lu(s) par une version '
            "d'avant. Clique sur <b>Préparer le plan</b> en haut de la page.</p></div>")


def _levels_attack(plan: dict) -> str:
    levels = plan["levels"]["attack"]
    if not levels:
        return _levels_missing(plan)
    who, sizes, fam = plan["who"], levels["sizes"], plan["family"]
    suites = _suites(levels, "attack")
    one = levels["steps"][0]
    rule = one["rules"]["tous"]["range"]["class"]
    options = " ".join(f'<span class="pl-chip {"down" if c == "check" else "up"}"><b>{escape(_action(c, sizes))}</b> '
                       f'{escape(_loss(x))}</span>' for c, x in levels["options"])
    out = [f'<h2>Du plus simple au plus précis, du flop à la river</h2><div class="card pl-level">'
           f'{_ladder(levels, LEVEL_TITLES, "lv-" + fam, suites)}']
    for k, step in enumerate(levels["steps"]):
        out.append(f'<h3 id="lv-{fam}-{step["key"]}">Niveau {k + 1} · {LEVEL_TITLES[step["key"]]} : '
                   f'{escape(_loss(step["loss"]))} au flop</h3>')
        if step["key"] == "une":
            out.append(f'<p class="pl-rule">{escape(_range_rule(who, rule, sizes))}, sur tous les flops.</p>'
                       '<p class="small" style="margin:0">Ce que coûte chaque action jouée par toute la range :</p>'
                       f'<div class="pl-chips">{options}</div>')
        elif step["key"] == "detail":
            out.append('<p class="small">Les onze familles du détail (deux paires et mieux, overpair, top pair bon '
                       "ou petit kicker…), une action chacune par type de flop ; au-delà, le solveur mélange selon la "
                       "main exacte : les stratégies ci-dessous, puis l'explorateur.</p>")
        else:
            out.append(_level_table(step, sizes, _hands_of(step["hands"])))
        out.append(_suite(plan, levels, "attack", k, suites) + _refine_box(plan, step, k == 0))
    out.append(f'{_levels_note(plan, levels)}</div>')
    return "".join(out)


def _levels_defense(plan: dict) -> str:
    levels = plan["levels"]["defense"]
    if not levels:
        return ""
    other, sizes, fam = plan["other"], levels["sizes"], plan["family"]
    suites = _suites(levels, "defense")
    hands = _hands_of("familles")
    out = [f'<h2>Face à la c-bet : du plus simple au plus précis, du flop à la river</h2><div class="card pl-level">'
           f'{_ladder(levels, DEFENSE_TITLES, "lvd-" + fam, suites)}']
    for k, step in enumerate(levels["steps"]):
        out.append(f'<h3 id="lvd-{fam}-{step["key"]}">Niveau {k + 1} · {DEFENSE_TITLES[step["key"]]} : '
                   f'{escape(_loss(step["loss"]))} au flop</h3>')
        if step["key"] == "une":
            rules = step["rules"]["tous"]
            chips = " ".join(_action_chip(rules[h]["class"], sizes, label) for h, label in hands if h in rules)
            out.append(f'<p class="small" style="margin:0">{escape(_cap(other))} face à la c-bet, sur tous les flops :'
                       f'</p><div class="pl-chips">{chips}</div>')
        elif step["key"] == "detail":
            out.append('<p class="small">Les onze familles du détail, une action chacune par type de flop ; au-delà, '
                       "le solveur mélange selon la main exacte.</p>")
        else:
            out.append(_level_table(step, sizes, hands))
        out.append(_suite(plan, levels, "defense", k, suites))
    out.append(f'{_levels_note(plan, levels)}</div>')
    return "".join(out)


# --- Contre le field ------------------------------------------------------------------------------------------------

def _tweaks(items: list[dict]) -> str:
    if not items:
        return '<p class="muted small">Pas d\'écart net sur ces situations : joue le plan suggéré.</p>'
    out = []
    for it in items:
        conf = "" if it["strong"] else ' <span class="conf c-à-confirmer">à confirmer</span>'
        out.append(f'<li class="{"strong" if it["strong"] else ""}"><div class="t">{escape(it["title"])}{conf}</div>'
                   f'<div>{escape(it["text"])}</div><div class="n">{escape(it["fact"])} : {escape(it["numbers"])}</div>'
                   "</li>")
    return f'<ul class="pl-tweaks">{"".join(out)}</ul>'


def _field_population(plan: dict, pop: dict, band: str, words: str) -> str:
    found = field_plan.adjustments(plan["baseline"], pop["study"], coach.aggressor_of(plan["family"]) == 1)
    level = stakes.label(band)
    return (
        f'<div class="pl-pop" data-pop="{escape(pop["key"])}" hidden>'
        f'<p class="note">{escape(pop["label"])} {words}, {escape(level[0].lower() + level[1:])} : '
        f'{num(pop["study"].hands, 0)} mains, {pop["players"]} joueur(s) ; leurs leaks dans l\'Étude du field, face '
        "aux repères d'un régulier solide de ces limites.</p>"
        f'<div class="pl-cols"><div class="card"><h3 style="margin-top:0">Quand tu as l\'initiative '
        f'({escape(plan["who"])})</h3>{_tweaks(found["attack"])}</div>'
        f'<div class="card"><h3 style="margin-top:0">Quand tu défends ({escape(plan["other"])})</h3>'
        f'{_tweaks(found["defense"])}</div></div></div>')


def _field_side(plan: dict, info: Optional[dict]) -> str:
    """Le plan ajusté aux leaks d'une population du field (au choix), en attaque et en défense."""
    words = "aux tables à plusieurs" if plan["ring"] else "en heads-up"
    if not info or not info["populations"]:
        return (f'<div class="card"><p class="note" style="margin:0">Pas encore d\'adversaires {words} dans '
                "l'Étude du field : importe des mains pour ajuster le plan à ceux que tu affrontes.</p></div>")
    band = info["band"]
    bands = ""
    if len(info["bands"]) > 1:
        links = " · ".join(
            f'<a href="?limites={b}#famille={plan["family"]}&amp;cote=field"'
            f'{" aria-current=page" if b == band else ""}>{escape(stakes.NAMES[b])} ({num(n, 0)} mains)</a>'
            for b, n in info["bands"])
        bands = f'<p class="pl-bands">Limites : {links}</p>'
    buttons = "".join(f'<button type="button" data-pop="{escape(p["key"])}">{escape(p["label"])} '
                      f'<span class="muted">({p["players"]})</span></button>' for p in info["populations"])
    blocks = "".join(_field_population(plan, p, band, words) for p in info["populations"])
    return (
        "<h2>Contre le field : le plan suggéré, légèrement ajusté</h2>"
        '<p class="note">Pars du plan suggéré (onglets En attaque et En défense) et applique ces ajustements contre '
        "la population choisie : chacun vient d'un de leurs leaks, avec le chiffre du plan et celui à viser. « À "
        "confirmer » : l'écart est net, l'échantillon encore petit.</p>"
        f'{bands}<div class="pl-pops" role="group" aria-label="Population">{buttons}</div>{blocks}')


def _legend() -> str:
    groups = " ; ".join(f"<b>{escape(label)}</b> : {escape(text)}" for _, label, _, text in coach.GROUPS)
    return (f'<p class="note">Les mains en quatre familles : {groups}. « Mélange » : le solveur joue les deux selon la '
            'main exacte ; le pourcentage est sa fréquence de mise, suivie de sa taille la plus jouée. Taille préférée '
            "(turn et river) : la taille la plus jouée à cette carte et sa part des mises, puis les autres. Un flop en "
            "exemple ouvre son étude dans l'explorateur, pour entrer dans le détail.</p>")


def _family(plan: dict, field_info: Optional[dict] = None) -> str:
    if plan["ring"]:
        try:
            studyspots.ring_spot_ranges(plan["family"])
        except postflop.Unsupported as exc:  # tes charts n'ont pas cette ligne
            return f'<div class="card"><p class="note">{escape(str(exc))}</p></div>'
    if not plan["count"]:
        where = ("Études du solveur › 6-max" if plan["ring"] else "l'onglet de la série dans Études du solveur")
        hint = (f"{plan['missing']} étude(s) attendent leur lecture : clique sur <b>Préparer le plan</b>."
                if plan["missing"] else f"Résous des flops de cette série ({where}, ou l'explorateur) : le plan se "
                "construit à partir des études.")
        return (f'<div class="card"><p class="muted">Pas encore de plan pour : {escape(plan["title"])}. {hint}</p>'
                "</div>")
    total = len(studyspots.flop_set(plan["family"]))
    who, other = plan["who"], plan["other"]
    attack = "".join(_attack(plan, g) for g in plan["groups"])
    defense = "".join(_defense(plan, g) for g in plan["groups"])
    return f"""
<p class="note">{escape(plan["label"])}. D'après <b>{plan["count"]}</b> flop(s) résolu(s) sur {total} dans la série ; plus
il y en a, plus le plan est précis. {escape(_cap(who))} a l'initiative et attaque, {escape(other)} défend.</p>
<div class="pl-sides" role="group" aria-label="Attaque, défense ou contre le field">
<button type="button" data-side="attaque" aria-pressed="true">En attaque · {escape(who)}</button>
<button type="button" data-side="defense" aria-pressed="false">En défense · {escape(other)}</button>
<button type="button" data-side="field" aria-pressed="false">Contre le field</button></div>
<div class="pl-side" data-side="attaque">
{_levels_attack(plan)}
<h2>Au flop : trois stratégies de c-bet</h2>
<div class="card">{_overview(plan, "attaque")}{_legend()}</div>
<h2>Stratégie par stratégie</h2>
{attack}
</div>
<div class="pl-side" data-side="defense" hidden>
{_levels_defense(plan)}
<h2>Face à la c-bet, selon la stratégie {escape(_de(who))}</h2>
<div class="card">{_overview(plan, "defense")}{_legend()}</div>
<h2>Stratégie par stratégie</h2>
{defense}
</div>
<div class="pl-side" data-side="field" hidden>
{_field_side(plan, field_info)}
</div>
<h2>Les flops résolus</h2>
<div class="card">{_table(plan)}
<p class="note">Fold face à la c-bet : la défense {escape(_de(other))} ; équité : écart d'équité moyenne entre les deux
ranges au flop, pour celui qui a l'initiative ; nuts : écart de part de deux paires et mieux. Un clic ouvre le flop dans
l'explorateur.</p></div>
"""


def build_coach_page(state: dict, embed: bool = True, villains: tuple = (), field: Optional[dict] = None) -> str:
    """state : bilan des plans (Library.plan_state) pour le bandeau de préparation ; villains : adversaires
    proposés dans les questions d'exemple du coach ; field : les populations de l'Étude du field par format
    (Library.plan_field : {"HU" | "ring": {"band", "bands", "populations": [{"key", "label", "players", "study"}]}})."""
    charts = ring_ranges.solution(studyspots.RING_FORMAT) is not None
    families = coach.plan_families() if charts else tuple(studyspots.FAMILIES)
    plans = [coach.family_plan(f) for f in families]
    default = max(plans, key=lambda p: p["count"])["family"] if plans else "srp"

    def tab(p: dict) -> str:
        return (f'<button type="button" data-fam="{p["family"]}"{" data-default" if p["family"] == default else ""}>'
                f'{escape(p["name"])} <span class="muted">({p["count"]})</span></button>')
    hu = [p for p in plans if not p["ring"]]
    rows = [f'<div class="pl-row"><span class="pl-fmt">Heads-up</span><div class="pl-fams">{"".join(map(tab, hu))}</div></div>']
    if charts:
        pairs: dict[str, list[dict]] = {}
        for p in plans:
            if p["ring"]:
                pairs.setdefault(p["pair"], []).append(p)
        rows.append('<div class="pl-row"><span class="pl-fmt">6-max</span><div class="pl-pairs">' + "".join(
            f'<div class="pl-pair"><span>{escape(pair)}</span><div class="pl-fams">{"".join(map(tab, group))}</div></div>'
            for pair, group in pairs.items()) + "</div></div>")
    else:
        rows.append('<div class="pl-row"><span class="pl-fmt">6-max</span><span class="muted small" '
                    'style="padding-top:6px">charge d\'abord tes charts 6-max (onglet <b>Tables à plusieurs</b> de '
                    '<i>Mon jeu</i>) puis résous des flops dans <i>Études du solveur › 6-max</i>.</span></div>')
    tabs = "".join(rows)
    missing = state.get("missing", 0)
    todo = (f"<b>{missing}</b> étude(s) à lire (ou à relire, pour chiffrer les niveaux du plan)" if missing
            else "Toutes les études sont lues.")
    head = (f'<div class="card"><div class="pl-head" data-missing="{missing}">'
            f'<span>{todo}</span>'
            f'<button type="button" class="go pl-run"{" hidden" if not missing else ""}>Préparer le plan</button>'
            '<button type="button" class="pl-stop" hidden>Arrêter</button><span class="pl-status"></span></div>'
            '<p class="note">Chaque étude est ouverte une fois pour lire ses stratégies (une vingtaine de secondes en '
            'pot 4bet, un peu plus d\'une minute en SRP sur 4 cœurs) ; une étude résolue à partir de maintenant est '
            'lue tout de suite.</p></div>')
    field = field or {}
    sections = "".join(f'<section class="pl-fam" data-fam="{p["family"]}" hidden>'
                       f'{_family(p, field.get("ring" if p["ring"] else "HU"))}</section>' for p in plans)
    suggestions = ["Résume-moi le plan de jeu en SRP", "Sur quels flops puis-je c-better toute ma range ?",
                   "Comment continuer à la turn après une c-bet payée ?"]
    ring = max((p for p in plans if p["ring"] and p["count"]), key=lambda p: p["count"], default=None)
    if ring:
        suggestions.insert(1, f"Résume-moi le plan de jeu {ring['title']}")
    suggestions += [q.format(v=v) for v in villains[:1] for q in ("Comment exploiter {v} ?",
                                                                    "Dans quelles lignes {v} bluffe-t-il ?")]
    chat = (f'<details class="card pl-chat" open><summary><b>Discuter avec le coach</b> '
            '<span class="muted">· il lit tes études, ton plan de jeu et les écarts de tes adversaires</span></summary>'
            f'<div id="coach-box" data-suggestions="{escape(json.dumps(suggestions, ensure_ascii=False))}"></div></details>'
            '<link rel="stylesheet" href="/static/coach.css"><script src="/static/coach.js"></script>')
    body = f"""
<div class="meta">Un plan de jeu simple, du flop à la river, tiré des études résolues : ce que le solveur fait, regroupé
en règles qu'un humain peut appliquer. En heads-up et aux tables à plusieurs (6-max, avec tes charts).</div>
{head}
{chat}
<div class="pl-switch" role="group" aria-label="Format et type de pot">{tabs}</div>
{sections}
"""
    return html_page("Plan de jeu suggéré", f"<style>{STYLE}</style>{body}", embed, script=SCRIPT)
