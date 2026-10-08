"""Page « Leakfinding » : le rapport d'un joueur (toi ou un élève) et ce qu'il doit travailler (voir analyzer/leaks.py,
et analyzer/ring_leaks.py pour les tables à plusieurs). Un rapport par format de table : heads-up, 6-max, 3-max.

En version autonome (standalone), la page se télécharge pour être envoyée à l'élève : sans boutons ni liens vers
l'application."""
from __future__ import annotations

import re
from html import escape
from typing import Optional
from urllib.parse import quote

from .. import leaks
from ..report import cards_html, html_page, num
from ..selfreport import OPP_SCRIPT, OPP_STYLE, opponent_row_data, opponent_tools
from ..theory import review, studyspots
from .review_page import STYLE as REVIEW_STYLE
from .review_page import _costly_table, _drills, _duration, _situations_table
from .ring_page import _why

FORMAT_NAMES = {"HU": "Heads-up"}

STYLE = """
.lk-list { margin: 0; padding-left: 22px; display: flex; flex-direction: column; gap: 10px; }
.lk-list li::marker { font-weight: 700; }
.lk-t { font-weight: 600; }
.lk-e { font-size: 13px; color: var(--ink-2); margin-top: 2px; }
.lk-a { font-size: 13px; margin-top: 2px; }
.lk-a a { font-weight: 600; margin-left: 6px; }
table.lk td.sec { font-weight: 600; background: var(--page); }
table.lk td.plus { background: var(--hi-bg); }
table.lk td.moins { background: var(--lo-bg); }
table.lk td.dim, table.lk span.n { color: var(--muted); font-size: 12px; }
table.lk td.small { font-size: 12px; color: var(--ink-2); }
table.lk .where { display: block; font-size: 11px; color: var(--muted); }
.lk-dl { font-weight: 600; }
.lk-fmt { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 12px; }
.lk-fmt a { font-size: 13px; padding: 4px 12px; border-radius: 999px; border: 1px solid var(--border); text-decoration: none;
  color: var(--ink-2); background: var(--surface); }
.lk-fmt a span { color: var(--muted); font-size: 12px; margin-left: 2px; }
.lk-fmt a[aria-current="page"] { background: var(--ink); color: var(--page); border-color: var(--ink); font-weight: 600; }
.lk-fmt a[aria-current="page"] span { color: var(--page); opacity: .75; }
details.lk-all { margin: 14px 0 0; }
details.lk-all > summary { font-weight: 600; }
details.lk-all > .inner-body { padding: 12px 16px; }
.lk-hint { border-left: 3px solid var(--series-4); padding: 6px 10px; margin: 12px 0 0; font-size: 13px; color: var(--ink-2);
  background: var(--surface); border-radius: 0 6px 6px 0; }
"""

SCRIPT = """
(function () {
  var box = document.querySelector('.lk-head');
  if (!box) return;
  var api = box.dataset.api, query = box.dataset.query || '', done = Number(box.dataset.done), timer = null;
  var run = box.querySelector('.lk-run'), stop = box.querySelector('.lk-stop'), status = box.querySelector('.rv-status');
  function post(url) { return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(function (r) { return r.json(); }); }
  function show(s) {
    if (s.done !== done && (!s.busy || s.done > done + 2)) { location.reload(); return; }
    status.textContent = '';
    stop.hidden = !s.busy;
    run.disabled = !s.ready;
    run.hidden = !!s.busy || s.done >= s.total;
    if (!s.ready) status.textContent = 'Installe d\\'abord le solveur : python -m analyzer gtopen --installer';
    else if (s.current) {
      var p = s.current.progress || {};
      status.textContent = 'Main ' + s.current.hand + ' : ' + (p.iteration ? 'itération ' + p.iteration : 'construction de l\\'arbre')
        + ' · ' + s.done + ' / ' + s.total + ' analysées · ' + (s.busy - 1) + ' en attente';
    } else if (s.busy) status.textContent = s.busy + ' main(s) en attente';
    clearTimeout(timer);
    if (s.busy) timer = setTimeout(refresh, 2500);
  }
  function refresh() { fetch(api + query).then(function (r) { return r.json(); }).then(show); }
  run.addEventListener('click', function () { run.disabled = true; post(api + '/lancer' + query).then(show); });
  stop.addEventListener('click', function () { post(api + '/arreter' + query).then(show); });
  refresh();
})();
document.querySelectorAll('select.lk-kind').forEach(function (sel) {
  sel.addEventListener('change', function () {
    fetch('/api/joueurs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: sel.dataset.name, kind: sel.value || null }) }).then(function () { location.reload(); });
  });
});
"""


def _pct(r) -> str:
    return "–" if not r or not r.opps else f"{round(100 * r.hits / r.opps)} %"


def _bb100(x: Optional[float]) -> str:
    return "–" if x is None else num(x, 1, sign=True) + " bb/100"


def _conf(confidence: str) -> str:
    return f'<span class="conf {"solide" if confidence == "solide" else "indicatif"}">{escape(confidence)}</span>'


def _ring(report: leaks.Report) -> bool:
    return report.table_format != "HU"


def format_query(table_format: str) -> str:
    """« ?format=6-max » : le rapport d'un format de table (rien pour le heads-up)."""
    return "" if table_format == "HU" else f"?format={quote(table_format)}"


def _leak_link(leak: leaks.Leak, pages: str, drills: dict, standalone: bool) -> str:
    if standalone or not leak.link:
        return ""
    if leak.link.startswith("drill:"):
        _, family, key = leak.link.split(":", 2)
        if key not in drills.get(family, ()):
            return ""
        href = f"/entraineur?famille={quote(family)}&situation={quote(key, safe='')}"
    elif leak.link == "preflop" or leak.link.split("#")[0] == "mains":
        href = f"{pages}/{leak.link}"
    else:  # « plan », ou « plan#famille=… » : le plan de jeu de la famille
        href = "/etudes/" + leak.link
    return f'<a href="{escape(href)}">{escape(leak.link_text)}</a>'


def _example(leak: leaks.Leak, standalone: bool) -> str:
    if not leak.example or leak.example[2] < 0.005:
        return ""
    hand, index, lost = leak.example
    text = f"Main la plus chère : {escape(hand)} (−{num(lost, 2)} bb)"
    if not standalone:
        text += f' <a class="open" href="/explorateur/{quote(hand, safe="")}#d={index}">Revoir ↗</a>'
    return f'<div class="lk-e">{text}</div>'


def leaks_html(report: leaks.Report, pages: str, standalone: bool) -> str:
    if not report.leaks:
        versus = "à ses tables" if _ring(report) else "contre les réguliers"
        return (f'<p class="note">Pas de leak net pour l\'instant : pas assez de mains {versus}, ou un jeu proche de la '
                'théorie dans les situations mesurées. Les mains analysées par le solveur affinent le rapport.</p>')
    drills = {} if standalone else _drills()
    items = "".join(
        f'<li><div class="lk-t">{escape(x.title)} {_conf(x.confidence)}</div>'
        f'<div class="lk-e">{escape(x.evidence)}</div>'
        f'<div class="lk-a">→ {escape(x.advice)}{_leak_link(x, pages, drills, standalone)}</div>'
        f'{_example(x, standalone)}</li>'
        for x in report.leaks)
    return f'<ol class="lk-list">{items}</ol>'


def _reference(s: leaks.Stat) -> str:
    if s.reference is not None:
        return f"{round(100 * s.reference)} %"
    if s.band is not None:
        return f"{round(100 * s.band[0])}–{round(100 * s.band[1])} %"
    return "–"


def _shows_verdict(report: leaks.Report, scope: str) -> bool:
    """Les colonnes où un écart se colore : contre les réguliers et les récréatifs (heads-up), toutes ses mains (tables à
    plusieurs, où c'est la seule)."""
    return scope != "all" or report.solver_scope == "all"


def gaps_table(found: list, scope: str, who: str) -> str:
    """Le tableau des écarts les plus importants (leaks.top_stat_gaps) : sa fréquence, la théorie, l'écart, la
    confiance et ce qu'il faut faire ; who : l'en-tête de sa colonne."""
    rows = []
    for s, direction, confidence in found:
        r = s.ratios[scope]
        gap = round(100 * s.gap(scope))
        advice = (s.advice or {}).get(direction) or leaks.ADVICE.get((s.kind, direction), "écart à corriger")
        note = f' <span class="n" title="{escape(s.note)}">*</span>' if s.note else ""
        rows.append(f'<tr><td>{escape(s.label)}<span class="where">{escape(s.section)}</span></td>'
                    f'<td class="num {direction}">{_pct(r)} <span class="n">({r.opps})</span></td>'
                    f'<td class="num">{_reference(s)}{note}</td>'
                    f'<td class="num">{"+" if direction == "plus" else "−"}{gap} pts</td>'
                    f'<td>{_conf(confidence)}</td><td class="small">{escape(leaks._cap(advice))}</td></tr>')
    return ('<div class="scroll"><table class="stats lk"><thead><tr><th>Situation</th>'
            f'<th class="num">{escape(who)}</th><th class="num">Théorie</th><th class="num">Écart</th><th>Confiance</th>'
            f'<th>À faire</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def gaps_html(report: leaks.Report) -> str:
    """Les écarts à la théorie les plus importants, avant le détail de toutes les stats."""
    found = leaks.top_gaps(report)
    if not found:
        return ('<p class="muted">Aucun écart net à la théorie pour l\'instant : pas assez d\'occasions, ou des fréquences '
                'proches de la théorie. Le détail de ses stats est plus bas.</p>')
    who = "Ses mains" if _ring(report) else "Contre réguliers"
    return f'<h3>Les écarts les plus importants</h3>{gaps_table(found, report.solver_scope, who)}'


def stats_html(report: leaks.Report) -> str:
    rows, section = [], None
    scopes = report.scopes
    for s in report.stats:
        if s.section != section:
            section = s.section
            rows.append(f'<tr><td class="sec" colspan="{len(scopes) + 3}">{escape(section)}</td></tr>')
        cells = []
        for scope, _ in scopes:
            r = s.ratios[scope]
            verdict = s.verdict(scope) if _shows_verdict(report, scope) else None
            cls = f' class="num {verdict[0]}"' if verdict else ' class="num"'
            cells.append(f'<td{cls}>{_pct(r)} <span class="n">({r.opps})</span></td>' if r.opps else '<td class="num dim">–</td>')
        v = s.verdict(report.solver_scope)
        note = f' <span class="n" title="{escape(s.note)}">*</span>' if s.note else ""
        rows.append(f'<tr><td>{escape(s.label)}</td>{"".join(cells)}<td class="num">{_reference(s)}{note}</td>'
                    f'<td>{_conf(v[1]) if v else ""}</td></tr>')
    if not rows:
        return '<p class="muted">Pas encore assez de mains.</p>'
    head = "".join(f'<th class="num">{escape(label)}</th>' for _, label in scopes)
    versus = "Écart" if _ring(report) else "Écart (réguliers)"
    reference = "Théorie" if _ring(report) else "Solveur"
    return (f'<div class="scroll"><table class="stats lk"><thead><tr><th>Situation</th>{head}<th class="num">{reference}</th>'
            f'<th>{versus}</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def info_html(report: leaks.Report) -> str:
    labels = (("vpip", "VPIP"), ("pfr", "PFR"), ("wtsd", "Va à l'abattage (WTSD)"), ("wsd", "Gagne à l'abattage (W$SD)"))
    scopes = report.scopes
    rows = [f'<tr><td>Mains</td>{"".join(f"<td class=num>{report.scope_hands[s]}</td>" for s, _ in scopes)}</tr>',
            f'<tr><td>Résultat</td>{"".join(f"<td class=num>{_bb100(report.winrate[s])}</td>" for s, _ in scopes)}</tr>']
    for key, label in labels:
        rows.append(f'<tr><td>{label}</td>' + "".join(f'<td class="num">{_pct(report.info[s][key])}</td>'
                                                     for s, _ in scopes) + "</tr>")
    head = "".join(f'<th class="num">{escape(label)}</th>' for _, label in scopes)
    return f'<div class="scroll"><table class="stats lk"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _pick_row(p: leaks.Pick, hero: str, pages: str, standalone: bool) -> str:
    cards = cards_html(p.hand.hole_cards.get(hero, [])) + " " + cards_html(p.hand.board)
    heads_up = len(p.hand.seats) == 2
    status_html = None
    if p.digest is not None:
        lost = sum(review.loss(d) or 0 for d in p.digest["decisions"] if d["who"] == "H")
        status = f"analysée : {num(lost, 2)} bb perdus" if lost >= 0.005 else "analysée : rien perdu"
        href = f"/explorateur/{quote(p.hand.hand_id, safe='')}"
    else:
        status = "à analyser" if p.spot is not None else ("à revoir à la main" if p.kind == "rec" else "hors solveur")
        if p.spot is None and p.why:
            status_html = _why(p.why)
        # une main heads-up se rejoue dans Mes spots ; celle d'une table à plusieurs s'ouvre au solveur
        href = (f"{pages}/spots#hand={quote(p.hand.hand_id, safe='')}" if heads_up
                else f"/explorateur/{quote(p.hand.hand_id, safe='')}")
    link = "" if standalone else f'<a class="open" href="{escape(href)}">Revoir ↗</a>'
    return (f'<tr><td>{escape(p.line)}</td><td>{cards}</td><td class="num">{num(p.pot_bb, 0)} bb</td>'
            f'<td class="num">{num(p.net_bb, 1, sign=True)} bb</td><td class="small">{status_html or escape(status)}</td>'
            f'<td class="small">{escape(p.hand.hand_id)}</td><td>{link}</td></tr>')


def picks_html(report: leaks.Report, scope: str, pages: str, standalone: bool) -> str:
    picks = report.picks.get(scope, [])
    if not picks:
        return '<p class="muted">Aucune main dans cette catégorie.</p>'
    rows = "".join(_pick_row(p, report.hero, pages, standalone) for p in picks)
    return ('<div class="scroll"><table class="stats review"><thead><tr><th>Ligne</th><th>Main et board</th>'
            '<th class="num">Pot</th><th class="num">Résultat</th><th>Solveur</th><th>Main</th><th></th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def _head(report: leaks.Report, api: str) -> str:
    picks = report.picks.get(report.solver_scope, [])
    state_total = sum(1 for p in picks if p.digest is not None or p.spot is not None)
    done = sum(1 for p in picks if p.digest is not None)
    todo = state_total - done
    eta = 2.5 * todo
    return (f'<div class="rv-head lk-head" data-api="{escape(api)}" data-query="{escape(format_query(report.table_format))}" '
            f'data-done="{done}"><span><b>{done} / {state_total}</b> mains choisies analysées</span>'
            f'<button type="button" class="go lk-run"{" hidden" if not todo else ""}>Analyser les {todo} mains '
            'choisies</button><button type="button" class="lk-stop" hidden>Arrêter</button><span class="rv-status"></span></div>'
            + (f'<p class="note">Chaque main se résout une fois : compte environ {_duration(eta)} sur 4 cœurs. Le rapport se '
               'complète au fur et à mesure ; tu peux fermer la page.</p>' if todo else ""))


def opponents_html(opponents: list[dict]) -> str:
    """Ses adversaires et leur type, réglable : il décide quelles mains comptent pour les leaks. Recherche par nom,
    filtre par type, tri par mains, résultat ou date."""
    if not opponents:
        return ""
    names = {"reg": "Régulier", "rec": "Récréatif"}
    rows = []
    for o in opponents:
        auto = f"Auto : {names[o['suggestion']]}" if o.get("suggestion") else "Auto : Régulier"
        options = "".join(f'<option value="{v}"{" selected" if (o.get("source") == "toi" and o["kind"] == v) or (o.get("source") != "toi" and not v) else ""}>{escape(t)}</option>'
                          for v, t in (("", auto), ("reg", "Régulier"), ("rec", "Récréatif")))
        data = opponent_row_data(o["name"], o.get("kind") or "reg", o["hands"], o["net_bb"], o.get("bb100", 0.0),
                                 o.get("last", ""))
        rows.append(f'<tr {data}><td>{escape(o["name"])}</td><td class="num">{o["hands"]}</td>'
                    f'<td class="num">{num(o["net_bb"], 1, sign=True)} bb</td>'
                    f'<td><select class="lk-kind" data-name="{escape(o["name"])}">{options}</select></td></tr>')
    tools = opponent_tools() if len(opponents) > 1 else ""
    return (f'<h2>Ses adversaires</h2><div class="card opp-box">{tools}<div class="scroll"><table class="stats opp-table">'
            '<thead><tr><th data-sort="name">Adversaire</th><th class="num" data-sort="hands">Mains</th>'
            '<th class="num" data-sort="net">Son résultat</th><th>Type</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div><p class="note">Le type décide quelles mains comptent pour les leaks '
            '(seulement contre les réguliers). Sans choix, une suggestion d\'après les stats de l\'adversaire.</p></div>')


def formats_html(formats: list[tuple[str, int]], current: str) -> str:
    """Le choix du format de table : un rapport par format (ses mains de chaque format)."""
    if len(formats) < 2:
        return ""
    links = "".join(
        f'<a href="{escape(format_query(fmt) or "?format=HU")}" aria-current="{"page" if fmt == current else "false"}">'
        f'{escape(FORMAT_NAMES.get(fmt, fmt))} <span>{n}</span></a>' for fmt, n in formats)
    return f'<nav class="lk-fmt" aria-label="Format de table">{links}</nav>'


def _tiles(report: leaks.Report) -> str:
    r = report.review
    solver = (f'<div class="tile"><div class="label">Face au solveur</div><div class="value">{r["analyzed"]} mains</div>'
              f'<div class="sub">{num(r["lost"], 1)} bb perdus en {r["decisions"]} décisions</div></div>')
    if _ring(report):
        info = report.info["all"]
        return ('<div class="tiles">'
                f'<div class="tile"><div class="label">Mains en {escape(report.table_format)}</div>'
                f'<div class="value">{report.hands}</div><div class="sub">{report.context.get("pots", 0)} pots à deux au '
                'flop</div></div>'
                f'<div class="tile"><div class="label">Résultat</div><div class="value">{_bb100(report.winrate["all"])}</div></div>'
                f'<div class="tile"><div class="label">VPIP / PFR</div><div class="value">{_pct(info["vpip"])} / '
                f'{_pct(info["pfr"])}</div></div>{solver}</div>')
    return ('<div class="tiles">'
            f'<div class="tile"><div class="label">Mains</div><div class="value">{report.hands}</div>'
            f'<div class="sub">{report.scope_hands["reg"]} contre réguliers, {report.scope_hands["rec"]} contre récréatifs</div></div>'
            f'<div class="tile"><div class="label">Contre réguliers</div><div class="value">{_bb100(report.winrate["reg"])}</div></div>'
            f'<div class="tile"><div class="label">Contre récréatifs</div><div class="value">{_bb100(report.winrate["rec"])}</div></div>'
            f'{solver}</div>')


def _hints(report: leaks.Report, standalone: bool) -> str:
    """Tables à plusieurs : ce qui manque pour comparer à la théorie (charts, flops 6-max résolus)."""
    if not _ring(report):
        return ""
    out = []
    fmt = escape(report.table_format)
    if not report.context.get("charts"):
        where = "" if standalone else " (Mon jeu › Tables à plusieurs : « Charger les charts »)"
        out.append(f"Ses charts {fmt} ne sont pas encore là : le préflop n'est comparé qu'aux repères indicatifs de "
                   f"l'open d'un régulier. Avec les charts{where}, chaque décision est comparée main par main.")
    if report.table_format == studyspots.RING_FORMAT and not report.context.get("plans"):
        where = "" if standalone else " (Études du solveur › 6-max : « Résoudre tous les flops 6-max manquants »)"
        out.append(f"Pas encore de flop 6-max résolu : l'après-flop n'a pas de repère du solveur{where}.")
    elif report.table_format != studyspots.RING_FORMAT:
        out.append(f"Les spots d'étude du solveur sont en 6-max : l'après-flop en {fmt} n'a pas de repère.")
    return "".join(f'<p class="lk-hint">{text}</p>' for text in out)


def build_leaks_page(report: leaks.Report, api: str, pages: str, embed: bool = True, standalone: bool = False,
                     name: Optional[str] = None, opponents: Optional[list[dict]] = None,
                     formats: Optional[list[tuple[str, int]]] = None) -> str:
    """api : adresse des actions (analyse de la sélection) ; pages : préfixe des pages du joueur (/moi, /eleve/x) ;
    formats : les formats de table de ses mains, avec leur nombre (le choix en haut de la page)."""
    who = name or report.hero
    ring = _ring(report)
    fmt = report.table_format
    download = "" if standalone else (
        f'<p><a class="lk-dl" href="{escape(pages)}/rapport{escape(format_query(fmt))}" download>Télécharger le rapport</a> '
        '<span class="muted small">(page autonome à envoyer, sans les boutons de l\'application)</span></p>')
    solver = (_situations_table(report.review["digests"], "H", False) + "<h3>Ses décisions les plus chères</h3>"
              + _costly_table(report.review["digests"], "H", True))
    if standalone:  # pas de liens vers l'application dans le rapport envoyé
        solver = re.sub(r'<a class="open"[^>]*>.*?</a>', "", solver)
    if ring:
        meta = (f"Leakfinding {escape(fmt)} de {escape(who)} : ce qu'il faut travailler en priorité à ses tables "
                f"{escape(fmt)}, d'après ses mains face à ses charts préflop (avec les mêmes cartes), aux plans de jeu des "
                "flops 6-max résolus et au solveur (pots à deux au flop).")
        theory_note = (
            "Fréquence (et nombre d'occasions) sur toutes ses mains du format, face à la théorie : avant le flop, ses "
            "charts avec les mêmes cartes que lui (ce qu'aurait joué un joueur qui les suit, sur les mains qu'il a reçues) "
            "ou, sans charts, le repère indicatif d'un régulier ; après le flop, dans les pots à deux joueurs, la moyenne "
            "des plans de jeu des flops 6-max résolus de même structure (qui a l'initiative, en position ou non). "
            "« Solide » : le hasard explique mal l'écart (intervalle de confiance à 90 %, 20 occasions au moins) ; "
            "« indicatif » : écart net sur moins de mains. * : précision sur le repère.")
        leaks_note = ("Classés par confiance puis par poids (fréquence de la situation, écart, et ce que la décision met en "
                      "jeu ; ou EV perdue face au solveur). À une table à plusieurs, toutes ses mains comptent.")
        solver_title = "Face au solveur, ses pots à deux au flop"
        solver_note = ("Ses plus gros pots à deux joueurs au flop passent au solveur, avec les ranges de ses charts pour la "
                       "ligne jouée ; chaque décision y est comparée à la meilleure action pour sa main exacte (EV perdue "
                       "en bb).")
        picks = (f'<div class="card">{picks_html(report, "all", pages, standalone)}'
                 '<p class="note">Les plus gros pots de chaque ligne (type de pot, positions, dernière street jouée), deux '
                 'par ligne : de quoi couvrir des spots variés. « Hors solveur » : la ligne n\'a pas de range dans ses '
                 'charts.</p></div>')
    else:
        families = ", ".join(studyspots.FAMILIES[f]["name"] for f in studyspots.FAMILIES)
        meta = (f"Leakfinding de {escape(who)} : ce qu'il faut travailler en priorité, d'après ses mains face à la théorie "
                "(solution préflop HU 100 bb, plans de jeu des flops résolus, mains résolues par le solveur).")
        theory_note = (
            "Fréquence (et nombre d'occasions) sur toutes ses mains, contre les réguliers et contre les récréatifs, face à "
            "celle du solveur : la solution préflop HU 100 bb, et après le flop la moyenne des plans de jeu des flops "
            f"résolus ({families}). « Solide » : le hasard explique mal l'écart (intervalle de confiance à 90 %, 20 "
            "occasions au moins) ; « indicatif » : écart net sur moins de mains. * : repère fragile (peu de flops "
            "résolus).")
        leaks_note = ("Classés par confiance puis par poids (fréquence de la situation, écart, et ce que la décision met en "
                      "jeu ; ou EV perdue face au solveur). Seules les mains contre les réguliers comptent : contre un "
                      "récréatif, l'exploitation prime sur la théorie.")
        solver_title = "Face au solveur, contre les réguliers"
        solver_note = ("Les mains choisies plus bas passent au solveur ; chaque décision y est comparée à la meilleure "
                       "action pour sa main exacte (EV perdue en bb). « S'entraîner » ouvre la situation dans l'entraîneur.")
        picks = (f'<div class="card"><h3>Contre les réguliers</h3>{picks_html(report, "reg", pages, standalone)}'
                 f'<h3>Contre les récréatifs</h3>{picks_html(report, "rec", pages, standalone)}'
                 '<p class="note">Les plus gros pots de chaque ligne (type de pot, position, dernière street jouée), deux par '
                 'ligne : de quoi couvrir les spots variés plutôt que dix fois le même. Contre les réguliers, le solveur donne '
                 'son avis ; contre les récréatifs, elles sont à revoir à la main.</p></div>')
    rec = "".join(f"<li>{escape(n)}</li>" for n in report.rec_notes)
    count = len(report.stats)
    body = f"""
<div class="meta">{meta}</div>
{"" if standalone else formats_html(formats or [], fmt)}
{download}
{_tiles(report)}
{_hints(report, standalone)}
<h2>Les leaks à travailler</h2>
<div class="card">{leaks_html(report, pages, standalone)}
<p class="note">{leaks_note}</p></div>
<h2>Ses stats face à la théorie</h2>
<div class="card">{gaps_html(report)}
<details class="lk-all"><summary>Le détail : toutes ses stats ({count} situation{"s" if count > 1 else ""})</summary>
<div class="inner-body">{info_html(report)}{stats_html(report)}</div></details>
<p class="note">{theory_note}</p></div>
<h2>{solver_title}</h2>
<div class="card">{"" if standalone else _head(report, api)}{solver}
<p class="note">{solver_note}</p></div>
<h2>Mains à revoir</h2>
{picks}
{f'<h2>Contre les récréatifs</h2><div class="card"><ul>{rec}</ul></div>' if rec else ""}
{"" if standalone or ring else opponents_html(opponents or [])}
"""
    style = f"<style>{REVIEW_STYLE}{STYLE}{OPP_STYLE}</style>"
    title = f"Leakfinding {fmt} — {who}" if ring else f"Leakfinding — {who}"
    return html_page(title, style + body, embed, script="" if standalone else SCRIPT + OPP_SCRIPT)
