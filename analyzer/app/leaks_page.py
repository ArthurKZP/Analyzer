"""Page « Leakfinding » : le rapport d'un joueur (toi ou un élève) et ce qu'il doit travailler (voir analyzer/leaks.py).

En version autonome (standalone), la page se télécharge pour être envoyée à l'élève : sans boutons ni liens vers
l'application."""
from __future__ import annotations

import re
from html import escape
from typing import Optional
from urllib.parse import quote

from .. import leaks
from ..report import cards_html, html_page, num
from ..theory import review, studyspots
from .review_page import STYLE as REVIEW_STYLE
from .review_page import _costly_table, _drills, _duration, _situations_table

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
.lk-dl { font-weight: 600; }
"""

SCRIPT = """
(function () {
  var box = document.querySelector('.lk-head');
  if (!box) return;
  var api = box.dataset.api, done = Number(box.dataset.done), timer = null;
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
  function refresh() { fetch(api).then(function (r) { return r.json(); }).then(show); }
  run.addEventListener('click', function () { run.disabled = true; post(api + '/lancer').then(show); });
  stop.addEventListener('click', function () { post(api + '/arreter').then(show); });
  refresh();
  document.querySelectorAll('select.lk-kind').forEach(function (sel) {
    sel.addEventListener('change', function () {
      fetch('/api/joueurs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: sel.dataset.name, kind: sel.value || null }) }).then(function () { location.reload(); });
    });
  });
})();
"""


def _pct(r) -> str:
    return "–" if not r or not r.opps else f"{round(100 * r.hits / r.opps)} %"


def _bb100(x: Optional[float]) -> str:
    return "–" if x is None else num(x, 1, sign=True) + " bb/100"


def _conf(confidence: str) -> str:
    return f'<span class="conf {"solide" if confidence == "solide" else "indicatif"}">{escape(confidence)}</span>'


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
    else:
        href = "/etudes/plan"
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
        return ('<p class="note">Pas de leak net pour l\'instant : pas assez de mains contre les réguliers, ou un jeu proche '
                'de la théorie dans les situations mesurées. Les mains analysées par le solveur affinent le rapport.</p>')
    drills = {} if standalone else _drills()
    items = "".join(
        f'<li><div class="lk-t">{escape(x.title)} {_conf(x.confidence)}</div>'
        f'<div class="lk-e">{escape(x.evidence)}</div>'
        f'<div class="lk-a">→ {escape(x.advice)}{_leak_link(x, pages, drills, standalone)}</div>'
        f'{_example(x, standalone)}</li>'
        for x in report.leaks)
    return f'<ol class="lk-list">{items}</ol>'


def stats_html(report: leaks.Report) -> str:
    rows, section = [], None
    for s in report.stats:
        if s.section != section:
            section = s.section
            rows.append(f'<tr><td class="sec" colspan="6">{escape(section)}</td></tr>')
        cells = []
        for scope, _ in leaks.SCOPES:
            r = s.ratios[scope]
            verdict = s.verdict(scope) if scope != "all" else None
            cls = f' class="num {verdict[0]}"' if verdict else ' class="num"'
            cells.append(f'<td{cls}>{_pct(r)} <span class="n">({r.opps})</span></td>' if r.opps else '<td class="num dim">–</td>')
        v = s.verdict("reg")
        ref = "–" if s.reference is None else f"{round(100 * s.reference)} %"
        note = f' <span class="n" title="{escape(s.note)}">*</span>' if s.note else ""
        rows.append(f'<tr><td>{escape(s.label)}</td>{"".join(cells)}<td class="num">{ref}{note}</td>'
                    f'<td>{_conf(v[1]) if v else ""}</td></tr>')
    if not rows:
        return '<p class="muted">Pas encore assez de mains.</p>'
    head = "".join(f'<th class="num">{escape(label)}</th>' for _, label in leaks.SCOPES)
    return (f'<div class="scroll"><table class="stats lk"><thead><tr><th>Situation</th>{head}<th class="num">Solveur</th>'
            f'<th>Écart (réguliers)</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def info_html(report: leaks.Report) -> str:
    labels = (("vpip", "VPIP"), ("pfr", "PFR"), ("wtsd", "Va à l'abattage (WTSD)"), ("wsd", "Gagne à l'abattage (W$SD)"))
    rows = [f'<tr><td>Mains</td>{"".join(f"<td class=num>{report.scope_hands[s]}</td>" for s, _ in leaks.SCOPES)}</tr>',
            f'<tr><td>Résultat</td>{"".join(f"<td class=num>{_bb100(report.winrate[s])}</td>" for s, _ in leaks.SCOPES)}</tr>']
    for key, label in labels:
        rows.append(f'<tr><td>{label}</td>' + "".join(f'<td class="num">{_pct(report.info[s][key])}</td>'
                                                     for s, _ in leaks.SCOPES) + "</tr>")
    head = "".join(f'<th class="num">{escape(label)}</th>' for _, label in leaks.SCOPES)
    return f'<div class="scroll"><table class="stats lk"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _pick_row(p: leaks.Pick, hero: str, pages: str, standalone: bool) -> str:
    cards = cards_html(p.hand.hole_cards.get(hero, [])) + " " + cards_html(p.hand.board)
    if p.digest is not None:
        lost = sum(review.loss(d) or 0 for d in p.digest["decisions"] if d["who"] == "H")
        status = f"analysée : {num(lost, 2)} bb perdus" if lost >= 0.005 else "analysée : rien perdu"
        href = f"/explorateur/{quote(p.hand.hand_id, safe='')}"
    else:
        status = "à analyser" if p.spot is not None else ("à revoir à la main" if p.kind == "rec" else "hors solveur")
        href = f"{pages}/spots#hand={quote(p.hand.hand_id, safe='')}"
    link = "" if standalone else f'<a class="open" href="{escape(href)}">Revoir ↗</a>'
    return (f'<tr><td>{escape(p.line)}</td><td>{cards}</td><td class="num">{num(p.pot_bb, 0)} bb</td>'
            f'<td class="num">{num(p.net_bb, 1, sign=True)} bb</td><td class="small">{escape(status)}</td>'
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
    state_total = sum(1 for p in report.picks.get("reg", []) if p.digest is not None or p.spot is not None)
    done = sum(1 for p in report.picks.get("reg", []) if p.digest is not None)
    todo = state_total - done
    eta = 2.5 * todo
    return (f'<div class="rv-head lk-head" data-api="{escape(api)}" data-done="{done}">'
            f'<span><b>{done} / {state_total}</b> mains choisies analysées</span>'
            f'<button type="button" class="go lk-run"{" hidden" if not todo else ""}>Analyser les {todo} mains '
            'choisies</button><button type="button" class="lk-stop" hidden>Arrêter</button><span class="rv-status"></span></div>'
            + (f'<p class="note">Chaque main se résout une fois : compte environ {_duration(eta)} sur 4 cœurs. Le rapport se '
               'complète au fur et à mesure ; tu peux fermer la page.</p>' if todo else ""))


def opponents_html(opponents: list[dict]) -> str:
    """Ses adversaires et leur type, réglable : il décide quelles mains comptent pour les leaks."""
    if not opponents:
        return ""
    names = {"reg": "Régulier", "rec": "Récréatif"}
    rows = []
    for o in opponents:
        auto = f"Auto : {names[o['suggestion']]}" if o.get("suggestion") else "Auto : Régulier"
        options = "".join(f'<option value="{v}"{" selected" if (o.get("source") == "toi" and o["kind"] == v) or (o.get("source") != "toi" and not v) else ""}>{escape(t)}</option>'
                          for v, t in (("", auto), ("reg", "Régulier"), ("rec", "Récréatif")))
        rows.append(f'<tr><td>{escape(o["name"])}</td><td class="num">{o["hands"]}</td>'
                    f'<td class="num">{num(o["net_bb"], 1, sign=True)} bb</td>'
                    f'<td><select class="lk-kind" data-name="{escape(o["name"])}">{options}</select></td></tr>')
    return ('<h2>Ses adversaires</h2><div class="card"><div class="scroll"><table class="stats"><thead><tr><th>Adversaire</th>'
            '<th class="num">Mains</th><th class="num">Son résultat</th><th>Type</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div><p class="note">Le type décide quelles mains comptent pour les leaks '
            '(seulement contre les réguliers). Sans choix, une suggestion d\'après les stats de l\'adversaire.</p></div>')


def build_leaks_page(report: leaks.Report, api: str, pages: str, embed: bool = True, standalone: bool = False,
                     name: Optional[str] = None, opponents: Optional[list[dict]] = None) -> str:
    """api : adresse des actions (analyse de la sélection) ; pages : préfixe des pages du joueur (/moi, /eleve/x)."""
    who = name or report.hero
    r = report.review
    tiles = (
        '<div class="tiles">'
        f'<div class="tile"><div class="label">Mains</div><div class="value">{report.hands}</div>'
        f'<div class="sub">{report.scope_hands["reg"]} contre réguliers, {report.scope_hands["rec"]} contre récréatifs</div></div>'
        f'<div class="tile"><div class="label">Contre réguliers</div><div class="value">{_bb100(report.winrate["reg"])}</div></div>'
        f'<div class="tile"><div class="label">Contre récréatifs</div><div class="value">{_bb100(report.winrate["rec"])}</div></div>'
        f'<div class="tile"><div class="label">Face au solveur</div><div class="value">{r["analyzed"]} mains</div>'
        f'<div class="sub">{num(r["lost"], 1)} bb perdus en {r["decisions"]} décisions</div></div>'
        '</div>')
    download = "" if standalone else (
        f'<p><a class="lk-dl" href="{escape(pages)}/rapport" download>Télécharger le rapport</a> '
        '<span class="muted small">(page autonome à envoyer, sans les boutons de l\'application)</span></p>')
    solver = (_situations_table(r["digests"], "H", False) + "<h3>Ses décisions les plus chères</h3>"
              + _costly_table(r["digests"], "H", True))
    if standalone:  # pas de liens vers l'application dans le rapport envoyé
        solver = re.sub(r'<a class="open"[^>]*>.*?</a>', "", solver)
    rec = "".join(f"<li>{escape(n)}</li>" for n in report.rec_notes)
    families = ", ".join(studyspots.FAMILIES[f]["name"] for f in studyspots.FAMILIES)
    body = f"""
<div class="meta">Leakfinding de {escape(who)} : ce qu'il faut travailler en priorité, d'après ses mains face à la théorie
(solution préflop HU 100 bb, plans de jeu des flops résolus, mains résolues par le solveur).</div>
{download}
{tiles}
<h2>Les leaks à travailler</h2>
<div class="card">{leaks_html(report, pages, standalone)}
<p class="note">Classés par confiance puis par poids (fréquence de la situation multipliée par l'écart, ou EV perdue face au
solveur). Seules les mains contre les réguliers comptent : contre un récréatif, l'exploitation prime sur la théorie.</p></div>
<h2>Ses stats face à la théorie</h2>
<div class="card">{info_html(report)}{stats_html(report)}
<p class="note">Fréquence (et nombre d'occasions) sur toutes ses mains, contre les réguliers et contre les récréatifs, face à
celle du solveur : la solution préflop HU 100 bb, et après le flop la moyenne des plans de jeu des flops résolus ({families}).
« Solide » : le hasard explique mal l'écart (intervalle de confiance à 90 %, 20 occasions au moins) ; « indicatif » : écart
de 8 points ou plus. * : repère fragile (peu de flops résolus).</p></div>
<h2>Face au solveur, contre les réguliers</h2>
<div class="card">{"" if standalone else _head(report, api)}{solver}
<p class="note">Les mains choisies plus bas passent au solveur ; chaque décision y est comparée à la meilleure action pour sa
main exacte (EV perdue en bb). « S'entraîner » ouvre la situation dans l'entraîneur.</p></div>
<h2>Mains à revoir</h2>
<div class="card"><h3>Contre les réguliers</h3>{picks_html(report, "reg", pages, standalone)}
<h3>Contre les récréatifs</h3>{picks_html(report, "rec", pages, standalone)}
<p class="note">Les plus gros pots de chaque ligne (type de pot, position, dernière street jouée), deux par ligne : de quoi
couvrir les spots variés plutôt que dix fois le même. Contre les réguliers, le solveur donne son avis ; contre les récréatifs,
elles sont à revoir à la main.</p></div>
{f'<h2>Contre les récréatifs</h2><div class="card"><ul>{rec}</ul></div>' if rec else ""}
{"" if standalone else opponents_html(opponents or [])}
"""
    style = f"<style>{REVIEW_STYLE}{STYLE}</style>"
    return html_page(f"Leakfinding — {who}", style + body, embed, script="" if standalone else SCRIPT)
