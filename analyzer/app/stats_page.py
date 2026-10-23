"""L'onglet Statistiques (Mon jeu, un élève, un joueur de référence) : tes stats, celles que tu as choisies dans le
catalogue (analyzer/statlib.py), dans ton ordre, sur toutes tes mains, contre les réguliers et contre les récréatifs,
face aux repères ; puis position par position. « Choisir mes stats » : ajouter, retirer, ranger, comme dans
PokerTracker (paramètres : stat_selection, communs à tous les espaces). Ses morceaux servent aussi à l'Étude du field
(field_stats_page.py : field_stat_selection)."""
from __future__ import annotations

import json
from html import escape
from typing import Callable, Optional

from .. import statlib
from ..report import html_page, num, pct_cell
from ..selfreport import STATS_LEGEND

STYLE = """
.sl-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 10px 0 4px; }
.sl-bar button, .sl-panel button { font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 6px;
  cursor: pointer; border: 1px solid var(--border); background: var(--page); color: var(--ink); }
.sl-bar button.primary, .sl-panel button.primary { background: var(--ink); color: var(--page);
  border-color: var(--ink); }
.sl-panel { border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin: 10px 0 16px;
  background: var(--card, var(--page)); }
.sl-cols { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 16px; }
@media (max-width: 760px) { .sl-cols { grid-template-columns: 1fr; } }
.sl-search { width: 100%; box-sizing: border-box; font: inherit; padding: 6px 8px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--page); color: var(--ink); margin-bottom: 8px; }
.sl-catalog { max-height: 460px; overflow: auto; padding-right: 6px; }
.sl-catalog h4 { margin: 10px 0 4px; font-size: 12px; color: var(--ink-2); }
.sl-catalog label { display: flex; gap: 6px; align-items: baseline; font-size: 13px; padding: 2px 0; cursor: pointer; }
.sl-catalog label .sl-help { color: var(--muted); font-size: 11px; }
.sl-chosen { list-style: none; margin: 0; padding: 0; max-height: 460px; overflow: auto; }
.sl-chosen li { display: flex; align-items: center; gap: 6px; padding: 3px 4px; border-bottom: 1px solid var(--axis);
  font-size: 13px; }
.sl-chosen li span { flex: 1; }
.sl-chosen li button { padding: 0 6px; font-size: 12px; }
.sl-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; align-items: center; }
.sl-msg { font-size: 12px; color: var(--muted); }
table.sl-table tr.grp td { font-weight: 600; font-size: 12px; color: var(--ink-2); padding-top: 12px;
  border-bottom: 1px solid var(--axis); }
table.sl-table td.lbl { white-space: nowrap; }
table.sl-table td.lbl abbr { text-decoration: none; border-bottom: 1px dotted var(--muted); cursor: help; }
.sl-switch { display: flex; gap: 6px; margin: 6px 0 10px; flex-wrap: wrap; }
.sl-switch button { font: inherit; font-size: 13px; padding: 4px 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border); background: var(--page); color: var(--ink); }
.sl-switch button[aria-pressed="true"] { background: var(--ink); color: var(--page); border-color: var(--ink); }
"""

UNITS = {"bb": " bb", "×": "×", "% du pot": " %"}
MIN_SAMPLE = 15
RefOf = Callable[[statlib.Stat], Optional[tuple]]  # le repère d'une stat sur la page


def _own_ref(fmt: str) -> RefOf:
    return lambda stat: stat.refs.get(fmt)


def cell(stat: statlib.Stat, fmt: str, table: Optional[statlib.Table], positions: Optional[tuple] = None,
         ref_of: Optional[RefOf] = None) -> str:
    """La valeur d'une stat sur ces mains (positions : seulement celles-là), en cellule de tableau ; colorée face à
    son repère (ref_of, sinon celui de la stat), sauf à une seule position quand le repère vaut pour toutes."""
    if table is None:
        return '<td class="num muted">–</td>'
    found = statlib.value(stat, fmt, table, positions)
    if found.value is None:
        return '<td class="num muted">–</td>'
    if found.ratio is not None and stat.kind in ("pct", "afq"):
        colored = not positions or bool(stat.positions)
        ref = (ref_of or _own_ref(fmt))(stat)
        return pct_cell(found.ratio, stat=statlib.stat_def(stat, fmt, ref) if colored and ref else None)
    small = " small" if found.sample < MIN_SAMPLE else ""
    if stat.kind == "hands":
        return f'<td class="num"><span class="v">{num(found.value, 0)}</span></td>'
    if stat.kind == "result":
        text = num(found.value, 1, sign=True)
        sample = f"{num(found.sample, 0)} mains"
    elif stat.kind == "af":
        text, sample = num(found.value, 1), f"{found.sample} actions"
    else:
        digits = 0 if stat.unit == "% du pot" else 1
        text, sample = num(found.value, digits) + UNITS.get(stat.unit, ""), f"{found.sample} fois"
    return f'<td class="num"><span class="v">{text}</span><span class="n{small}">{sample}</span></td>'


def _ref(ref: Optional[tuple]) -> str:
    return f'<td class="num muted">{ref[0]:.0f}–{ref[1]:.0f}&nbsp;%</td>' if ref else '<td class="num muted"></td>'


def _label(stat: statlib.Stat) -> str:
    return f'<td class="lbl"><abbr title="{escape(stat.help)}">{escape(stat.label)}</abbr></td>'


def overview_table(chosen: list[statlib.Stat], fmt: str, scopes: list[tuple[str, str, statlib.Table]],
                   tail: tuple = (), ref_of: Optional[RefOf] = None) -> str:
    """Tes stats, dans ton ordre (un titre à chaque changement de groupe) : une colonne par portée, le repère (ref_of,
    sinon celui de la stat), puis les portées de tail (« Toi » dans l'Étude du field)."""
    ref_of = ref_of or _own_ref(fmt)
    head = "".join(f'<th class="num">{escape(label)}</th>' for _, label, _ in scopes)
    after = "".join(f'<th class="num">{escape(label)}</th>' for _, label, _ in tail)
    width = len(scopes) + len(tail) + 2
    rows, group = [], None
    for stat in chosen:
        if stat.group != group:
            group = stat.group
            rows.append(f'<tr class="grp"><td colspan="{width}">{escape(group)}</td></tr>')
        cells = "".join(cell(stat, fmt, table, ref_of=ref_of) for _, _, table in scopes)
        last = "".join(cell(stat, fmt, table, ref_of=ref_of) for _, _, table in tail)
        rows.append(f"<tr>{_label(stat)}{cells}{_ref(ref_of(stat))}{last}</tr>")
    return (f'<div class="card scroll"><table class="stats sl-table"><thead><tr><th>Stat</th>{head}'
            f'<th class="num">Repère</th>{after}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def positions_table(chosen: list[statlib.Stat], fmt: str, table: statlib.Table,
                    ref_of: Optional[RefOf] = None) -> str:
    """Tes stats position par position (les positions où tu as des mains, dans l'ordre de parole)."""
    order = statlib.POSITIONS[fmt]
    present = [p for p in order if p in table.positions] + sorted(p for p in table.positions if p not in order)
    head = "".join(f'<th class="num">{escape(p)}</th>' for p in present)
    rows, group = [], None
    for stat in chosen:
        if stat.group != group:
            group = stat.group
            rows.append(f'<tr class="grp"><td colspan="{len(present) + 2}">{escape(group)}</td></tr>')
        cells = "".join(cell(stat, fmt, table, (p,), ref_of) for p in present)
        rows.append(f"<tr>{_label(stat)}{cell(stat, fmt, table, ref_of=ref_of)}{cells}</tr>")
    return (f'<div class="scroll"><table class="stats sl-table"><thead><tr><th>Stat</th><th class="num">Toutes</th>'
            f'{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="note">En couleur à une position : seulement les stats dont le repère vaut pour elle (l\'open '
            "par position par exemple).</p>")


def positions_block(chosen: list[statlib.Stat], fmt: str, shown: list[tuple[str, str, statlib.Table]],
                    ref_of: Optional[RefOf] = None, what: str = "Adversaires") -> str:
    """Les stats position par position de chaque portée (shown), une à la fois (boutons au-dessus)."""
    switch = (f'<div class="sl-switch" role="group" aria-label="{escape(what)}">' + "".join(
        f'<button type="button" data-scope="{escape(scope)}" aria-pressed="{str(k == 0).lower()}">'
        f"{escape(label)} ({num(table.total.hands, 0)})</button>" for k, (scope, label, table) in enumerate(shown))
        + "</div>") if len(shown) > 1 else ""
    return switch + "".join(
        f'<section class="sl-pos" data-scope="{escape(scope)}"{"" if k == 0 else " hidden"}>'
        f"{positions_table(chosen, fmt, table, ref_of)}</section>" for k, (scope, _, table) in enumerate(shown))


TO_ALL = "Ton choix vaut pour ce format, dans ton espace comme pour tes élèves et tes joueurs de référence."


def chooser(fmt: str, chosen: list[statlib.Stat], saved: Optional[dict], setting: str = "stat_selection",
            scope_note: str = TO_ALL) -> str:
    """Le panneau « Choisir mes stats » : le catalogue (cases à cocher, recherche) et tes stats dans ton ordre ;
    setting : le paramètre où il s'enregistre (saved : sa valeur)."""
    picked = {s.id for s in chosen}
    groups = []
    for group in statlib.GROUPS:
        stats = [s for s in statlib.available(fmt) if s.group == group]
        if not stats:
            continue
        items = "".join(
            f'<label data-text="{escape((s.label + " " + s.help).lower())}"><input type="checkbox" '
            f'data-id="{escape(s.id)}" data-label="{escape(s.label)}"{" checked" if s.id in picked else ""}>'
            f'<span>{escape(s.label)} <span class="sl-help">— {escape(s.help)}</span></span></label>' for s in stats)
        groups.append(f'<div class="sl-group"><h4>{escape(group)}</h4>{items}</div>')
    data = {"format": fmt, "saved": saved, "selected": [s.id for s in chosen], "setting": setting}
    return (
        '<div class="sl-panel" hidden><div class="sl-cols"><div><input type="search" class="sl-search" '
        f'placeholder="Chercher une stat (c-bet, 3bet, river…)" aria-label="Chercher une stat">'
        f'<div class="sl-catalog">{"".join(groups)}</div></div>'
        '<div><b>Tes stats, dans l\'ordre</b> <span class="sl-msg sl-count"></span>'
        '<ol class="sl-chosen"></ol></div></div>'
        '<div class="sl-actions"><button type="button" class="primary sl-save">Enregistrer</button>'
        '<button type="button" class="sl-cancel">Annuler</button>'
        '<button type="button" class="sl-default">Revenir aux stats par défaut</button>'
        '<span class="sl-msg sl-status"></span></div>'
        '<p class="note">Coche une stat pour l\'ajouter (à la fin), décoche-la pour la retirer ; ↑ et ↓ la déplacent. '
        f"{escape(scope_note)}</p></div>"
        f'<script type="application/json" id="sl-data">{_json(data)}</script>')


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def build_stats_page(fmt: str, hero: str, scopes: list[tuple[str, str, Optional[statlib.Table]]],
                     saved: Optional[dict] = None, head: str = "", embed: bool = True) -> str:
    """L'onglet Statistiques dans ce format (« HU », « ring ») : scopes, [(portée, libellé, statlib.tally de tes
    mains)] — toutes tes mains, contre les réguliers, contre les récréatifs (None : pas de mains) ; saved : ta
    sélection (settings stat_selection)."""
    shown = [(scope, label, table) for scope, label, table in scopes if table is not None and table.total.hands]
    where = "heads-up" if fmt == "HU" else "aux tables à plusieurs"
    if not shown:
        body = (f'{head}<p class="note">Aucune main {where} pour l\'instant.</p>')
        return html_page(f"Statistiques — {hero}", body, embed)
    chosen = statlib.selection(fmt, saved)
    total = shown[0][2].total.hands
    refs = ("la solution préflop et un régulier HU solide à 100 bb" if fmt == "HU"
            else "un régulier 6-max à 100 bb")
    meta = (f'<div class="meta">{num(total, 0)} mains {where} · {len(chosen)} stat(s) choisie(s) sur '
            f"{len(statlib.available(fmt))} · repères indicatifs : {refs} · l'essentiel est dans le bilan.</div>")
    note = ('<p class="note">Fréquences : fois où tu l\'as fait / fois où tu pouvais le faire ; en couleur, hors du '
            "repère ; chiffres en gris : moins de 15 occasions. Tailles : moyenne de tes mises (tapis exclus). "
            "Contre les réguliers, contre les récréatifs : les mains où l'un d'eux était dans le coup.</p>")
    body = (f"<style>{STYLE}</style>{head}{meta}{BAR}{chooser(fmt, chosen, saved)}"
            + (f"{STATS_LEGEND}{overview_table(chosen, fmt, shown)}{note}" if chosen else NONE_CHOSEN)
            + (f'<h2>Position par position</h2><div class="card">{positions_block(chosen, fmt, shown)}</div>'
               if chosen else ""))
    return html_page(f"Statistiques — {hero}", body, embed, script=SCRIPT)


BAR = ('<div class="sl-bar"><button type="button" class="primary sl-open">Choisir mes stats</button>'
       '<span class="sl-msg">Survole le nom d\'une stat pour sa définition.</span></div>')
NONE_CHOSEN = '<p class="note">Aucune stat choisie : clique sur « Choisir mes stats ».</p>'


SCRIPT = r"""
(function () {
  var panel = document.querySelector('.sl-panel');
  var data = JSON.parse(document.getElementById('sl-data').textContent);
  var boxes = Array.prototype.slice.call(panel.querySelectorAll('input[type=checkbox]'));
  var labels = {};
  boxes.forEach(function (b) { labels[b.dataset.id] = b.dataset.label; });
  var sel = data.selected.slice();
  var list = panel.querySelector('.sl-chosen'), count = panel.querySelector('.sl-count');
  var status = panel.querySelector('.sl-status');

  function render() {
    list.textContent = '';
    sel.forEach(function (id, k) {
      var li = document.createElement('li');
      var name = document.createElement('span');
      name.textContent = labels[id] || id;
      li.appendChild(name);
      [['↑', -1, 'Monter'], ['↓', 1, 'Descendre'], ['✕', 0, 'Retirer']].forEach(function (b) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = b[0];
        btn.title = b[2];
        btn.setAttribute('aria-label', b[2] + ' : ' + (labels[id] || id));
        btn.addEventListener('click', function () {
          if (b[1] === 0) sel.splice(k, 1);
          else {
            var j = k + b[1];
            if (j < 0 || j >= sel.length) return;
            sel[k] = sel[j]; sel[j] = id;
          }
          sync();
        });
        li.appendChild(btn);
      });
      list.appendChild(li);
    });
    count.textContent = '(' + sel.length + ')';
  }
  function sync() {
    boxes.forEach(function (b) { b.checked = sel.indexOf(b.dataset.id) >= 0; });
    render();
  }
  boxes.forEach(function (b) {
    b.addEventListener('change', function () {
      var k = sel.indexOf(b.dataset.id);
      if (b.checked && k < 0) sel.push(b.dataset.id);
      if (!b.checked && k >= 0) sel.splice(k, 1);
      render();
    });
  });
  panel.querySelector('.sl-search').addEventListener('input', function (e) {
    var q = e.target.value.trim().toLowerCase();
    panel.querySelectorAll('.sl-catalog label').forEach(function (l) {
      l.hidden = q && l.dataset.text.indexOf(q) < 0;
    });
    panel.querySelectorAll('.sl-group').forEach(function (g) {
      g.hidden = !g.querySelector('label:not([hidden])');
    });
  });
  function save(value) {
    var all = Object.assign({}, data.saved || {});
    if (value === null) delete all[data.format]; else all[data.format] = value;
    status.textContent = 'Enregistrement…';
    var body = {};
    body[data.setting] = Object.keys(all).length ? all : null;
    fetch('/api/parametres', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); }); })
      .then(function () { location.reload(); })
      .catch(function (err) { status.textContent = 'Erreur : ' + err.message; });
  }
  document.querySelector('.sl-open').addEventListener('click', function () {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) panel.querySelector('.sl-search').focus();
  });
  panel.querySelector('.sl-cancel').addEventListener('click', function () {
    sel = data.selected.slice(); sync(); panel.hidden = true;
  });
  panel.querySelector('.sl-save').addEventListener('click', function () { save(sel); });
  panel.querySelector('.sl-default').addEventListener('click', function () { save(null); });
  document.querySelectorAll('.sl-switch button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.sl-pos').forEach(function (s) { s.hidden = s.dataset.scope !== b.dataset.scope; });
      document.querySelectorAll('.sl-switch button').forEach(function (x) {
        x.setAttribute('aria-pressed', String(x === b));
      });
    });
  });
  render();
})();
"""
