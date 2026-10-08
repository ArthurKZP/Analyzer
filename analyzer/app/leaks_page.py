"""Page « Leakfinding » : le rapport d'un joueur (toi ou un élève) et ce qu'il doit travailler (voir analyzer/leaks.py,
et analyzer/ring_leaks.py pour les tables à plusieurs). Deux rapports : le heads-up, et les tables à plusieurs (3 à 9 joueurs ensemble).

En version autonome (standalone), la page se télécharge pour être envoyée à l'élève : sans boutons ni liens vers
l'application."""
from __future__ import annotations

import re
from html import escape
from typing import Optional
from urllib.parse import quote

from .. import leaks
from ..report import cards_html, format_query, format_switch, html_page, num
from ..selfreport import OPP_SCRIPT, OPP_STYLE, opponent_row_data, opponent_tools
from ..theory import review, studyspots
from .review_page import STYLE as REVIEW_STYLE
from .review_page import _costly_table, _drills, _duration, _situations_table
from .ring_page import _why

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
"""

# Le type d'un adversaire, réglé dans la liste : la page se recalcule.
KIND_SCRIPT = """
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
        return ('<p class="note">Pas de leak net pour l\'instant : pas assez de mains contre les réguliers, ou un jeu '
                'proche de la théorie dans les situations mesurées. Les mains analysées par le solveur affinent le '
                'rapport.</p>')
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
    """Les colonnes où un écart se colore : contre les réguliers et contre les récréatifs (toutes ses mains seulement si
    c'est la portée comparée à la théorie)."""
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
    who = "Contre réguliers" if report.solver_scope == "reg" else "Ses mains"
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
    versus = "Écart" if report.solver_scope == "all" else "Écart (réguliers)"
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


def opponents_html(opponents: list[dict], ring: bool = False, title: str = "Tes adversaires",
                   result: str = "Ton résultat", pick: bool = False,
                   groups: Optional[dict[str, list[str]]] = None) -> str:
    """Les adversaires du joueur et leur type, réglable : il décide quelles mains comptent pour les leaks. Recherche par
    nom, filtre par type, tri par mains, résultat ou date. ring : ceux des tables à plusieurs (mains à la même table,
    résultat dans les pots disputés ensemble) ; result : la colonne du résultat du joueur contre chacun (« Ton
    résultat », « Résultat de Paul ») ; pick : une case à cocher par joueur (pour les regrouper sous un alias) ;
    groups : les alias et leurs pseudos ({alias: [pseudos]}), signalés à côté du nom."""
    if not opponents:
        return ""
    names = {"reg": "Régulier", "rec": "Récréatif"}
    groups = groups or {}
    rows = []
    for o in opponents:
        auto = f"Auto : {names[o['suggestion']]}" if o.get("suggestion") else "Auto : Régulier"
        options = "".join(f'<option value="{v}"{" selected" if (o.get("source") == "toi" and o["kind"] == v) or (o.get("source") != "toi" and not v) else ""}>{escape(t)}</option>'
                          for v, t in (("", auto), ("reg", "Régulier"), ("rec", "Récréatif")))
        data = opponent_row_data(o["name"], o.get("kind") or "reg", o["hands"], o["net_bb"], o.get("bb100", 0.0),
                                 o.get("last", ""))
        pots = f'<td class="num">{o.get("pots", 0)}</td>' if ring else ""
        why = escape(", ".join(o.get("reasons") or []))
        box = (f'<td class="al-cell"><input type="checkbox" class="al-pick" value="{escape(o["name"])}" '
               f'aria-label="Choisir {escape(o["name"])}"></td>') if pick else ""
        pseudos = groups.get(o["name"])
        tag = (f' <span class="al-tag" title="Pseudos : {escape(", ".join(pseudos))}">{len(pseudos)} pseudo(s)</span>'
               if pseudos else "")
        rows.append(f'<tr {data}>{box}<td>{escape(o["name"])}{tag}</td><td class="num">{o["hands"]}</td>{pots}'
                    f'<td class="num">{num(o["net_bb"], 1, sign=True)} bb</td>'
                    f'<td><select class="lk-kind" data-name="{escape(o["name"])}" title="{why}">{options}</select></td></tr>')
    tools = opponent_tools() if len(opponents) > 1 else ""
    head = ('<th class="num" data-sort="hands">Mains ensemble</th><th class="num">Pots disputés</th>'
            f'<th class="num" data-sort="net">{escape(result)} (pots)</th>') if ring else (
        f'<th class="num" data-sort="hands">Mains</th><th class="num" data-sort="net">{escape(result)}</th>')
    note = ("Le type décide quelles mains comptent pour les leaks : une main compte contre les récréatifs quand un "
            "récréatif a mis de l'argent dans le pot pendant que le joueur y était encore. Sans choix, une suggestion "
            "d'après les fréquences de l'adversaire aux tables à plusieurs (trop de mains jouées, de limps, de calls). "
            f"« {result} » : dans les pots disputés ensemble, en bb." if ring else
            "Le type décide quelles mains comptent pour les leaks (seulement contre les réguliers). Sans choix, une "
            "suggestion d'après les stats de l'adversaire.")
    first = '<th class="al-cell"></th>' if pick else ""
    return (f'<h2>{escape(title)}</h2><div class="card opp-box">{tools}<div class="scroll"><table class="stats opp-table">'
            f'<thead><tr>{first}<th data-sort="name">Adversaire</th>{head}<th>Type</th></tr></thead><tbody>'
            + "".join(rows) + f'</tbody></table></div><p class="note">{escape(note)}</p></div>')


def _tiles(report: leaks.Report) -> str:
    r = report.review
    label = "Mains aux tables à plusieurs" if _ring(report) else "Mains"
    return ('<div class="tiles">'
            f'<div class="tile"><div class="label">{label}</div><div class="value">{report.hands}</div>'
            f'<div class="sub">{report.scope_hands["reg"]} contre réguliers, {report.scope_hands["rec"]} contre récréatifs</div></div>'
            f'<div class="tile"><div class="label">Contre réguliers</div><div class="value">{_bb100(report.winrate["reg"])}</div></div>'
            f'<div class="tile"><div class="label">Contre récréatifs</div><div class="value">{_bb100(report.winrate["rec"])}</div></div>'
            f'<div class="tile"><div class="label">Face au solveur</div><div class="value">{r["analyzed"]} mains</div>'
            f'<div class="sub">{num(r["lost"], 1)} bb perdus en {r["decisions"]} décisions</div></div></div>')


def _hints(report: leaks.Report, standalone: bool) -> str:
    """Tables à plusieurs : ce qui manque pour comparer à la théorie (charts, flops 6-max résolus)."""
    if not _ring(report):
        return ""
    out = []
    if not report.context.get("charts"):
        where = "" if standalone else " (Mon jeu › Tables à plusieurs : « Charger les charts »)"
        out.append("Ses charts ne sont pas encore là : le préflop n'est comparé qu'aux repères indicatifs de l'open d'un "
                   f"régulier 6-max. Avec les charts{where}, chaque décision est comparée main par main.")
    if not report.context.get("plans"):
        where = "" if standalone else " (Études du solveur › 6-max : « Résoudre tous les flops 6-max manquants »)"
        out.append(f"Pas encore de flop 6-max résolu : l'après-flop n'a pas de repère du solveur{where}.")
    return "".join(f'<p class="lk-hint">{text}</p>' for text in out)


def build_leaks_page(report: leaks.Report, api: str, pages: str, embed: bool = True, standalone: bool = False,
                     name: Optional[str] = None, opponents: Optional[list[dict]] = None,
                     formats: Optional[list[tuple[str, int]]] = None, period: str = "") -> str:
    """api : adresse des actions (analyse de la sélection) ; pages : préfixe des pages du joueur (/moi, /eleve/x) ;
    formats : les formats de table de ses mains, avec leur nombre (le choix en haut de la page) ; period : la période
    analysée, en une phrase (rien pour toutes les mains)."""
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
        tables = ", ".join(report.context.get("formats") or [])
        meta = (f"Leakfinding des tables à plusieurs de {escape(who)}{' (' + escape(tables) + ')' if tables else ''} : ce "
                "qu'il faut travailler en priorité, d'après ses mains face aux charts préflop (avec les mêmes cartes), aux "
                "plans de jeu des flops 6-max résolus et au solveur (pots à deux au flop).")
        theory_note = (
            "Fréquence (et nombre d'occasions) sur toutes ses mains de 3 joueurs et plus, contre les réguliers et contre "
            "les récréatifs, face à la théorie : avant le flop, les charts avec les mêmes cartes que lui (ce qu'aurait "
            "joué un joueur qui les suit, sur les mains qu'il a reçues ; ceux de chaque table, sinon ceux du 6-max à même "
            "nombre de joueurs derrière) ou, sans charts, le repère indicatif d'un régulier ; après le flop, dans les pots "
            "à deux joueurs, la moyenne des plans de jeu des flops 6-max résolus de même structure (qui a l'initiative, "
            "en position ou non). Une main compte contre les récréatifs quand un récréatif a mis de l'argent dans le pot "
            "pendant qu'il y était encore. « Solide » : le hasard explique mal l'écart (intervalle de confiance à 90 %, "
            "20 occasions au moins) ; « indicatif » : écart net sur moins de mains. * : précision sur le repère.")
        leaks_note = ("Classés par confiance puis par poids (fréquence de la situation, écart, et ce que la décision met en "
                      "jeu ; ou EV perdue face au solveur). Seules les mains contre les réguliers comptent : contre un "
                      "récréatif, l'exploitation prime sur la théorie.")
        solver_title = "Face au solveur, contre les réguliers"
        solver_note = ("Ses plus gros pots à deux joueurs au flop contre les réguliers passent au solveur, avec les ranges "
                       "des charts pour la ligne jouée ; chaque décision y est comparée à la meilleure action pour sa main "
                       "exacte (EV perdue en bb).")
        picks = (f'<div class="card"><h3>Contre les réguliers</h3>{picks_html(report, "reg", pages, standalone)}'
                 f'<h3>Contre les récréatifs</h3>{picks_html(report, "rec", pages, standalone)}'
                 '<p class="note">Les plus gros pots à deux joueurs de chaque ligne (type de pot, positions, dernière street '
                 'jouée), deux par ligne : de quoi couvrir des spots variés. Contre les réguliers, le solveur donne son '
                 'avis (« hors solveur » : la ligne n\'a pas de range dans les charts) ; contre les récréatifs, elles sont '
                 'à revoir à la main.</p></div>')
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
<div class="meta">{meta}{f" <b>{escape(period)}</b>" if period else ""}</div>
{"" if standalone else format_switch(formats or [], fmt)}
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
{"" if standalone else opponents_html(opponents or [], ring, *(("Tes adversaires", "Ton résultat") if name is None
                                                                else ("Ses adversaires", f"Résultat de {who}")))}
"""
    style = f"<style>{REVIEW_STYLE}{STYLE}{OPP_STYLE}</style>"
    title = f"Leakfinding tables à plusieurs — {who}" if ring else f"Leakfinding — {who}"
    return html_page(title, style + body, embed, script="" if standalone else SCRIPT + KIND_SCRIPT + OPP_SCRIPT)
