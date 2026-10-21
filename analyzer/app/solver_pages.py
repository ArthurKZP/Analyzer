"""Tes mains face au solveur, format par format (le heads-up, les tables à plusieurs ; jamais mélangés) : le panneau
« Face au solveur » en tête de chaque onglet de format de Mes mains (ses chiffres, l'analyse à lancer, les mains
analysées à parcourir dans la liste dessous, les erreurs qui coûtent le plus et les erreurs récurrentes), et le Bilan
du solveur, qui dit ce qui ressort des mains analysées dans chaque format.

Chaque format se lit dans le résumé de Library.solver_review (leaks.solver_review) : tes mains contre les réguliers
(contre un récréatif, l'exploitation prime sur la théorie) ; aux tables à plusieurs, tes pots à deux au flop."""
from __future__ import annotations

import json
from html import escape
from typing import Optional

from ..report import FORMAT_NAMES, fold, html_page, num
from ..theory import review
from .review_page import STYLE as REVIEW_STYLE
from .review_page import costly_table, family_tag, situations_table

LIMITS = (5, 10, 20, 0)  # mains passées au solveur d'un clic (0 : toutes)
ORDERS = (("leaks", "liées à tes leaks, les plus chères d'abord"), ("pertes", "les plus chères"),
          ("pots", "les plus gros pots"))
FORMAT_LABELS = {"all": "toutes tes mains", "HU": "heads-up", "ring": "tables à plusieurs"}
STREETS = (("flop", "Flop"), ("turn", "Turn"), ("river", "River"))
POT_TYPES = (("SRP", "SRP"), ("pot 3bet", "Pots 3bet"), ("pot 4bet", "Pots 4bet"))
POT_WORDS = {"SRP": "SRP", "pot 3bet": "pots 3bet", "pot 4bet": "pots 4bet"}  # dans une phrase
SMALL = 30  # mains analysées : en dessous, l'échantillon reste petit

# Le panneau va dans deux sortes de pages (celles du rapport, le visualiseur de mains) : ses couleurs prennent les
# variables de l'une ou de l'autre.
STYLE = """
.fs { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px;
  margin: 0 0 14px; font-size: 13px; }
.fs h2 { font-size: 15px; margin: 0; }
.fs-top { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 14px; }
.fs-line { color: var(--ink-2); }
.fs-line b { color: var(--ink); }
.fs-bad { color: var(--alert, var(--loss)); }
.fs-acts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin: 10px 0 2px; }
.fs-acts button, .fs-run button { font: inherit; font-size: 13px; padding: 4px 12px; border-radius: 6px;
  cursor: pointer; border: 1px solid var(--border); background: var(--page); color: var(--ink); }
.fs-acts button.fs-main { border-color: var(--series-1, var(--accent)); color: var(--series-1, var(--accent));
  font-weight: 600; }
.fs-run { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin: 8px 0 0; }
.fs-run select { font: inherit; font-size: 13px; padding: 3px 6px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--page); color: var(--ink); }
.fs-run button.fs-go { background: var(--series-1, var(--accent)); border-color: var(--series-1, var(--accent));
  color: #fff; font-weight: 600; }
.fs-run button:disabled { opacity: .5; cursor: default; }
.fs-eta, .fs-status { color: var(--ink-2); font-size: 12px; }
.fs-status .bar { display: inline-block; vertical-align: middle; width: 120px; height: 6px; border-radius: 3px;
  background: var(--grid); overflow: hidden; margin-left: 6px; }
.fs-status .bar span { display: block; height: 100%; background: var(--series-1, var(--accent)); }
.fs-note { color: var(--muted); font-size: 12px; margin: 6px 0 0; }
.fs details { margin-top: 10px; border-top: 1px solid var(--grid); padding-top: 6px; }
.fs summary { cursor: pointer; font-weight: 600; }
.fs summary span { font-weight: 400; color: var(--muted); font-size: 12px; margin-left: 6px; }
.fs .scroll { overflow-x: auto; }
.fs table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; margin: 6px 0; }
.fs th, .fs td { padding: 4px 6px; border-bottom: 1px solid var(--grid); text-align: left; vertical-align: middle; }
.fs th { font-size: 11px; color: var(--muted); font-weight: 600; }
.fs td.num, .fs th.num { text-align: right; }
.fs td.loss { font-weight: 600; color: var(--alert, var(--loss)); white-space: nowrap; }
.fs td.small { font-size: 12px; color: var(--ink-2); }
.fs .fam { font-size: 10px; color: var(--muted); border: 1px solid var(--border); border-radius: 4px; padding: 0 4px;
  margin-left: 4px; }
.fs a.open { font-weight: 600; text-decoration: none; white-space: nowrap; }
.fs .freq { white-space: nowrap; } .fs .freq .th { color: var(--muted); }
.fs .conf { font-size: 11px; border-radius: 4px; padding: 0 5px; background: var(--grid); }
.fs .note { color: var(--muted); font-size: 12px; }
"""

# Le lancement d'une analyse : combien de mains, lesquelles (et, au bilan, de quel format), la durée estimée ; puis
# l'avancement, demandé toutes les 3 s pendant les résolutions. onDone : quand une main de plus est analysée.
RUN_SCRIPT = """
function fsDuration(m) {
  if (m < 90) return Math.max(1, Math.round(m)) + ' min';
  var h = m / 60;
  return h < 48 ? Math.round(h) + ' h' : (h / 24).toFixed(1).replace('.', ',') + ' jours';
}
function fsRun(box, onDone) {
  var orders = JSON.parse(box.dataset.orders || '{}'), api = box.dataset.api, timer = null, done = null;
  var fmtSel = box.querySelector('.fs-format'), limit = box.querySelector('.fs-limit');
  var order = box.querySelector('.fs-order'), eta = box.querySelector('.fs-eta');
  var go = box.querySelector('.fs-go'), stop = box.querySelector('.fs-stop'), status = box.querySelector('.fs-status');
  var fmt = function () { return fmtSel ? fmtSel.value : box.dataset.format; };
  var labels = JSON.parse(box.dataset.labels || '{}');
  function options() {
    var mine = orders[fmt()] || {}, keep = order.value;
    order.textContent = '';
    Object.keys(labels).forEach(function (k) {
      if (!mine[k] || !mine[k].n) return;
      var o = document.createElement('option');
      o.value = k; o.textContent = labels[k] + ' (' + mine[k].n + ')';
      order.append(o);
    });
    if (keep && mine[keep] && mine[keep].n) order.value = keep;
    update();
  }
  function update() {
    var found = (orders[fmt()] || {})[order.value];
    eta.textContent = found ? '≈ ' + fsDuration(found.minutes[limit.value] || 0) + ' sur 4 cœurs' : '';
    go.disabled = !found;
  }
  function call(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ format: fmt() }, body || {})) }).then(function (r) { return r.json(); });
  }
  function show(s) {
    if (done === null) done = s.done;
    if (s.done !== done) { done = s.done; onDone(); }
    status.textContent = '';
    stop.hidden = !s.busy;
    go.hidden = !!s.busy;
    if (!s.ready) { status.append('Installe d\\'abord le solveur : python -m analyzer gtopen --installer'); go.disabled = true; }
    else if (s.current) {
      var p = s.current.progress || {}, frac = p.iteration ? Math.min(1, p.iteration / s.current.max_iterations) : 0;
      status.append((p.iteration ? 'Résolution en cours' : 'Construction de l\\'arbre') + ' · ' + s.done + ' / '
        + s.total + ' analysées · ' + Math.max(0, s.busy - 1) + ' en attente');
      var bar = document.createElement('span'), fill = document.createElement('span');
      bar.className = 'bar'; fill.style.width = (100 * frac).toFixed(1) + '%'; bar.append(fill); status.append(bar);
    } else if (s.busy) status.append(s.busy + ' main(s) en attente (une autre résolution passe d\\'abord)');
    clearTimeout(timer);
    if (s.busy) timer = setTimeout(refresh, 3000);
  }
  function refresh() {
    fetch(api + '?format=' + encodeURIComponent(fmt())).then(function (r) { return r.json(); }).then(show)
      .catch(function () {});
  }
  if (fmtSel) fmtSel.addEventListener('change', function () { done = null; options(); refresh(); });
  limit.addEventListener('change', update);
  order.addEventListener('change', update);
  go.addEventListener('click', function () {
    go.disabled = true;
    call(api + '/lancer', { ordre: order.value, limite: Number(limit.value) }).then(show)
      .finally(function () { go.disabled = false; });
  });
  stop.addEventListener('click', function () { call(api + '/arreter').then(show); });
  options();
  refresh();
}
"""

# Le panneau dans un onglet de format : chargé après la page (la liste des mains s'affiche sans l'attendre), il
# se recharge quand une main de plus est analysée. La page qui l'accueille dit comment montrer les mains analysées
# (window.fsFilter) et comment se mettre à jour (window.fsRefresh).
PANEL_SCRIPT = RUN_SCRIPT + """
(function () {
  var slot = document.querySelector('.fs-slot');
  if (!slot) return;
  function load() {
    fetch(slot.dataset.src).then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        var open = Array.prototype.map.call(slot.querySelectorAll('details[open]'), function (d) { return d.id; });
        slot.innerHTML = html;
        open.forEach(function (id) { var d = id && document.getElementById(id); if (d) d.open = true; });
        var run = slot.querySelector('.fs-run');
        if (run) fsRun(run, function () { load(); if (window.fsRefresh) window.fsRefresh(); });
        slot.querySelectorAll('[data-sv]').forEach(function (b) {
          b.addEventListener('click', function () { if (window.fsFilter) window.fsFilter(b.dataset.sv); });
        });
      }).catch(function () { slot.textContent = ''; });
  }
  load();
})();
"""


def slot_html(src: str) -> str:
    """La place du panneau dans la page (chargé ensuite depuis src)."""
    return f'<div class="fs-slot" data-src="{escape(src)}"></div>'


def picker_data(orders: dict[str, dict]) -> dict:
    """Ce que le choix des mains à analyser montre : pour chaque format et chaque ordre (Library.review_orders), le
    nombre de mains et la durée de leurs résolutions selon combien on en lance (LIMITS ; 0 : toutes), en minutes."""
    out = {}
    for fmt, found in orders.items():
        out[fmt] = {}
        for key, items in found.items():
            minutes = [m for _, m in items]
            out[fmt][key] = {"n": len(items),
                             "minutes": {str(n): round(sum(minutes[:n] if n else minutes), 1) for n in LIMITS}}
    return out


def run_html(api: str, orders: dict[str, dict], formats: list[str], fixed: Optional[str] = None) -> str:
    """Le lancement d'une analyse : le format (fixed : celui du panneau ; sinon un choix, « toutes tes mains » quand
    il y en a plusieurs), combien de mains, lesquelles, la durée estimée, Analyser et Arrêter."""
    data = picker_data(orders)
    if not any(x["n"] for found in data.values() for x in found.values()):
        return ""
    choice = ""
    if fixed is None:
        keys = (["all"] if len(formats) > 1 else []) + formats
        choice = ('<select class="fs-format" aria-label="Quelles mains">' + "".join(
            f'<option value="{k}">{escape(FORMAT_LABELS[k].capitalize())}</option>' for k in keys) + "</select>")
    limits = "".join(f'<option value="{n}"{" selected" if n == 10 else ""}>{f"{n} mains" if n else "toutes"}</option>'
                     for n in LIMITS)
    labels = escape(json.dumps(dict(ORDERS)))
    return (f'<div class="fs-run" data-api="{escape(api)}" data-orders="{escape(json.dumps(data))}" '
            f'data-labels="{labels}"' + (f' data-format="{escape(fixed)}"' if fixed else "") + ">"
            f'{choice}<select class="fs-limit" aria-label="Combien de mains">{limits}</select>'
            '<select class="fs-order" aria-label="Lesquelles"></select><span class="fs-eta"></span>'
            '<button type="button" class="fs-go">Analyser</button>'
            '<button type="button" class="fs-stop" hidden>Arrêter</button><span class="fs-status"></span></div>')


def lost_text(value: float, digits: int = 1) -> str:
    """L'EV perdue : « −12,3 bb », « 0 bb » quand il n'y a rien de perdu."""
    return f"−{num(value, digits)} bb" if value >= 0.5 * 10 ** -digits else "0 bb"


def _numbers(data: dict) -> dict:
    """Les chiffres d'un format : mains analysées et à résoudre, décisions, erreurs, EV perdue, mains avec une
    erreur."""
    mine = review.decisions_of(data["digests"], "H")
    known = [review.loss(d) for _, d in mine if d["ev_loss"] is not None]
    bad = {g["hand"] for g, d in mine if (review.loss(d) or 0) >= review.ERROR}
    analyzed = data["analyzed"]
    return {"analyzed": analyzed, "total": analyzed + len(data["todo"]), "decisions": len(known),
            "errors": sum(x >= review.ERROR for x in known), "lost": sum(known), "bad_hands": len(bad),
            "per_hand": sum(known) / analyzed if analyzed else None}


def _line(n: dict) -> str:
    """« 20 mains analysées sur 340 allées au flop · 46 décisions, 7 erreurs · EV perdue −12,3 bb (−0,62 par main) »."""
    text = f'<b>{n["analyzed"]}</b> mains analysées sur {n["total"]} allées au flop contre les réguliers'
    if n["analyzed"]:
        errors = f'{n["errors"]} erreur{"s" if n["errors"] > 1 else ""}'
        text += (f' · {n["decisions"]} décisions, <b class="{"fs-bad" if n["errors"] else ""}">{errors}</b>'
                 f' · EV perdue <b>{lost_text(n["lost"])}</b> ({lost_text(n["per_hand"], 2)} par main)')
    return text


def excluded_note(data: dict) -> str:
    """Tes mains contre des récréatifs, laissées de côté (data : Library.solver_review)."""
    n = data.get("excluded") or 0
    if not n:
        return ""
    return (f'<p class="fs-note">{n} main{"s" if n > 1 else ""} contre des récréatifs '
            f'{"sont laissées" if n > 1 else "est laissée"} de côté : contre eux, l\'exploitation prime sur la '
            "théorie (le type de chaque adversaire se règle dans Paramètres › Joueurs et alias).</p>")


def panel_html(fmt: str, data: dict, orders: dict, api: str, pages: str = "") -> str:
    """Le panneau « Face au solveur » d'un onglet de format : data, Library.solver_review(fmt) ; orders, les mains à
    résoudre de ce format dans chaque ordre (Library.review_orders) ; api : celle de l'analyse (…/revue) ; pages :
    le préfixe des pages (« /moi »), pour le lien du bilan."""
    n = _numbers(data)
    acts = []
    if n["analyzed"]:
        acts.append(f'<button type="button" class="fs-main" data-sv="done">Voir les {n["analyzed"]} mains '
                    "analysées</button>")
    if n["bad_hands"]:
        acts.append(f'<button type="button" data-sv="err">Les {n["bad_hands"]} avec une erreur</button>'
                    if n["bad_hands"] > 1 else '<button type="button" data-sv="err">Celle avec une erreur</button>')
    acts.append(f'<a href="{escape(pages)}/solveur">Le bilan du solveur</a>')
    run = run_html(api, {fmt: orders}, [fmt], fixed=fmt)
    body = (f'<div class="fs-top"><h2>Face au solveur — {escape(FORMAT_LABELS[fmt])}</h2>'
            f'<span class="fs-line">{_line(n)}</span></div><div class="fs-acts">{"".join(acts)}</div>{run}'
            + excluded_note(data))
    if not n["analyzed"]:
        body += ('<p class="fs-note">Aucune main analysée pour l\'instant : lance l\'analyse des plus chères, ou de '
                 "celles où se jouent tes leaks. Chaque main se résout une fois ; la liste se complète au fur et à "
                 "mesure, tu peux fermer la page.</p>")
        return f'<style>{STYLE}</style><section class="fs" aria-label="Face au solveur">{body}</section>'
    body += (f'<details id="fs-couteuses-{fmt}"><summary>Les erreurs qui coûtent le plus<span>les décisions où tu as '
             f'perdu le plus d\'EV, à revoir dans l\'explorateur</span></summary>'
             f'{costly_table(data["digests"], "H", True)}</details>'
             f'<details id="fs-recurrentes-{fmt}"><summary>Les erreurs récurrentes<span>situation par situation : '
             f'tes erreurs, l\'EV perdue et tes fréquences face au solveur</span></summary>'
             f'{situations_table(data["digests"], "H", False)}</details>')
    return f'<style>{STYLE}</style><section class="fs" aria-label="Face au solveur">{body}</section>'


# --- Le bilan du solveur ---------------------------------------------------------------------------------------

def breakdown(digests: list[dict]) -> dict[str, list[dict]]:
    """Où part l'EV : tes décisions, tes erreurs et l'EV perdue par street, par type de pot et par position."""
    groups: dict[str, dict[str, dict]] = {"street": {}, "pot": {}, "position": {}}
    for g, d in review.decisions_of(digests, "H"):
        lost = review.loss(d)
        if lost is None:
            continue
        for kind, key in (("street", d["street"]), ("pot", g.get("pot_type") or "SRP"),
                          ("position", g.get("hero_position") or "")):
            row = groups[kind].setdefault(key, {"key": key, "n": 0, "errors": 0, "lost": 0.0})
            row["n"] += 1
            row["errors"] += lost >= review.ERROR
            row["lost"] += lost
    order = {"street": [k for k, _ in STREETS], "pot": [k for k, _ in POT_TYPES]}
    out = {}
    for kind, rows in groups.items():
        known = order.get(kind)
        out[kind] = sorted(rows.values(), key=lambda r: (known.index(r["key"]) if known and r["key"] in known
                                                          else 99, -r["lost"]))
    return out


def _share(part: float, whole: float) -> str:
    return f"{round(100 * part / whole)} %" if whole > 0 else "–"


def insights(data: dict) -> list[str]:
    """Ce qui ressort des mains analysées d'un format, en quelques phrases (HTML)."""
    n = _numbers(data)
    if not n["analyzed"]:
        return []
    out = []
    if n["lost"] >= 0.05:
        parts = breakdown(data["digests"])
        street = max(parts["street"], key=lambda r: r["lost"])
        pot = max(parts["pot"], key=lambda r: r["lost"])
        out.append(f'L\'EV part surtout <b>{"au " if street["key"] == "flop" else "à la "}{street["key"]}</b> '
                   f'({_share(street["lost"], n["lost"])} de l\'EV perdue) et en '
                   f'<b>{escape(POT_WORDS.get(pot["key"], pot["key"]))}</b> ({_share(pot["lost"], n["lost"])}).')
    groups = [g for g in data["groups"] if g["known"] and g["errors"]]
    if groups:
        top = groups[0]
        out.append(f'La situation qui te coûte le plus : <b>{escape(top["label"])}</b> '
                   f'({family_tag(top["family"])}) — {top["errors"]} erreur{"s" if top["errors"] > 1 else ""}'
                   f' sur {top["n"]} fois, −{num(top["lost"], 1)} bb.')
    devs = sorted(((abs(dev["z"]), g, dev) for g in data["groups"] for dev in review.deviations(g)),
                  key=lambda x: -x[0])
    if devs:
        _, g, dev = devs[0]
        out.append(f'Ton écart de fréquence le plus net : {escape(g["label"])} ({family_tag(g["family"])}), '
                   f'{escape(dev["label"])} {round(100 * dev["observed"])} % contre {round(100 * dev["expected"])} % '
                   f'pour le solveur sur {g["n"]} fois ({escape(dev["confidence"])}) : '
                   f'{escape(review.exploit(g, dev, False).lower().rstrip("."))}.')
    if not out:
        out.append("Pas d'erreur notable dans les mains analysées : tes décisions suivent celles du solveur.")
    if n["analyzed"] < SMALL:
        out.append(f"Échantillon encore petit ({n['analyzed']} mains) : analyse-en plus pour des conclusions solides.")
    return out


def _breakdown_table(data: dict) -> str:
    parts = breakdown(data["digests"])
    total = sum(r["lost"] for r in parts["street"])
    names = {**dict(STREETS), **dict(POT_TYPES)}
    rows = []
    for kind, title in (("street", "Street"), ("pot", "Type de pot"), ("position", "Ta position")):
        for k, r in enumerate(parts[kind]):
            head = f'<td rowspan="{len(parts[kind])}"><b>{title}</b></td>' if k == 0 else ""
            rows.append(f'<tr>{head}<td>{escape(names.get(r["key"], r["key"]) or "–")}</td>'
                        f'<td class="num">{r["n"]}</td><td class="num">{r["errors"]}</td>'
                        f'<td class="num">{lost_text(r["lost"])}</td><td class="num">{_share(r["lost"], total)}</td>'
                        "</tr>")
    if not rows:
        return ""
    return ('<div class="scroll"><table class="stats"><thead><tr><th></th><th></th><th class="num">Décisions</th>'
            '<th class="num">Erreurs</th><th class="num">EV perdue</th><th class="num">Part</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _top_situations(data: dict, limit: int = 5) -> str:
    groups = [g for g in data["groups"] if g["known"] and g["lost"] >= 0.05][:limit]
    if not groups:
        return ""
    rows = "".join(
        f'<tr><td>{escape(g["label"])}<span class="fam">{family_tag(g["family"])}</span></td>'
        f'<td class="num">{g["n"]}</td><td class="num">{g["errors"]}</td><td class="num">−{num(g["lost"], 1)} bb</td>'
        f'<td class="num">−{num(g["lost"] / g["known"], 2)} bb</td></tr>' for g in groups)
    return ('<div class="scroll"><table class="stats review"><thead><tr><th>Situation</th><th class="num">Fois</th>'
            '<th class="num">Erreurs</th><th class="num">EV perdue</th><th class="num">Par fois</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def build_solver_bilan(sections: list[tuple[str, dict]], orders: dict[str, dict], api: str, pages: str = "",
                       embed: bool = True, empty_note: str = "") -> str:
    """Le Bilan du solveur (Mes mains › Bilan) : le lancement d'une analyse (toutes tes mains, ou un format), puis,
    format par format (sections : [(format, Library.solver_review)]), ce qui ressort des mains analysées : les
    chiffres, l'essentiel en phrases, où part l'EV, les situations qui coûtent le plus et le lien vers les mains.
    orders : les mains à résoudre de chaque format et de tous (« all »), dans chaque ordre."""
    formats = [fmt for fmt, _ in sections]
    if not sections:
        body = f'<p class="note">{escape(empty_note or "Aucune main à passer au solveur.")}</p>'
        return html_page("Bilan du solveur", body, embed)
    run = run_html(api, orders, formats, fixed=formats[0] if len(formats) == 1 else None)
    head = ('<div class="meta">Ce que le solveur dit de tes mains analysées, format par format (les formats ne se '
            "mélangent pas) : tes décisions après le flop contre les réguliers, comparées à celles du solveur dans "
            "le même coup. Chaque format a, dans son onglet, ses mains analysées à parcourir et le détail de tes "
            "erreurs.</div>")
    launch = (f'<div class="card fs" style="margin:0 0 6px"><b>Analyser des mains</b>{run}<p class="fs-note">Les '
              "plus chères d'abord, ou celles où se jouent tes leaks : chaque main se résout une fois (quelques "
              "minutes) ; la page se complète au fur et à mesure, tu peux la fermer.</p></div>" if run else "")
    blocks = []
    compare = []
    for fmt, data in sections:
        n = _numbers(data)
        tab = "spots" if fmt == "HU" else "tables"
        title = FORMAT_NAMES.get(fmt, fmt)
        anchor = f'<h2 id="{escape(fmt.lower())}">{escape(title)}</h2>'
        if not n["analyzed"]:
            blocks.append(f'{anchor}<div class="card"><p class="muted">Aucune main analysée sur {n["total"]} allées '
                          f'au flop contre les réguliers : lance l\'analyse ci-dessus.</p>{excluded_note(data)}</div>')
            continue
        compare.append((title, n))
        tiles = (
            '<div class="tiles">'
            f'<div class="tile"><div class="label">Mains analysées</div><div class="value">{n["analyzed"]}</div>'
            f'<div class="sub">sur {n["total"]} allées au flop</div></div>'
            f'<div class="tile"><div class="label">Tes décisions</div><div class="value">{n["decisions"]}</div>'
            f'<div class="sub">{n["errors"]} erreur(s) de 0,25 bb et plus</div></div>'
            f'<div class="tile"><div class="label">EV perdue</div><div class="value">{lost_text(n["lost"])}</div>'
            f'<div class="sub">{lost_text(n["per_hand"], 2)} par main analysée</div></div></div>')
        brief = "".join(f"<li>{x}</li>" for x in insights(data))
        link = (f'<p><a href="{escape(tab)}#sv=done&amp;sort=evloss">Les {n["analyzed"]} mains analysées et le détail de '
                f'tes erreurs ({escape(title.lower())}) →</a></p>' if fmt == "HU" else
                f'<p><a href="{escape(tab)}#solveur">Les {n["analyzed"]} mains analysées et le détail de tes erreurs '
                f'({escape(title.lower())}) →</a></p>')
        situations = _top_situations(data)
        detail = fold("Où part l'EV", f'<div class="card">{_breakdown_table(data)}<p class="note">Tes décisions jugées '
                      "par le solveur (main connue), par street, type de pot et position : combien, tes erreurs et "
                      "l'EV perdue.</p></div>", "par street, type de pot et position", f"ev-{fmt}")
        blocks.append(f'{anchor}{tiles}<div class="card"><h3>En bref</h3><ul class="brief">{brief}</ul>{link}'
                      f'{excluded_note(data)}</div>'
                      + (f'<div class="card"><h3>Les situations qui te coûtent le plus</h3>{situations}</div>'
                         if situations else "") + detail)
    if len(compare) > 1:
        (a, na), (b, nb) = compare[:2]
        worse, better = ((a, na), (b, nb)) if na["per_hand"] >= nb["per_hand"] else ((b, nb), (a, na))
        head += (f'<p class="note">Par main analysée, tu perds plus en {escape(worse[0].lower())} '
                 f'(−{num(worse[1]["per_hand"], 2)} bb) qu\'en {escape(better[0].lower())} '
                 f'(−{num(better[1]["per_hand"], 2)} bb).</p>')
    style = (f"<style>{STYLE}{REVIEW_STYLE}\n.fs .fs-run {{ margin-top: 6px; }} "
             "ul.brief { margin: 4px 0; padding-left: 18px; }</style>")
    return html_page("Bilan du solveur", style + head + launch + "".join(blocks), embed, script=RUN_SCRIPT + """
(function () {
  var run = document.querySelector('.fs-run');
  if (run) fsRun(run, function () { location.reload(); });
})();
""")
