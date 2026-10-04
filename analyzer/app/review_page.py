"""Page « Face au solveur » : les mains jouées comparées à la théorie (voir theory/review.py)."""
from __future__ import annotations

from html import escape
from typing import Optional
from urllib.parse import quote

from ..models import Hand
from ..report import cards_html, html_page, num
from ..theory import review

FAMILY_NAME = {"srp": "SRP", "3bet": "3bet", "4bet": "4bet"}
MINUTES = {"SRP": 3, "pot 3bet": 2, "pot 4bet": 0.3}  # durée d'une résolution sur 4 cœurs (mesurée)

STYLE = """
.rv-head { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin: 4px 0 6px; }
.rv-head button { font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.rv-head button.go { background: var(--series-1); border-color: var(--series-1); color: #fff; font-weight: 600; }
.rv-head button:disabled { opacity: .5; cursor: default; }
.rv-status { font-size: 12px; color: var(--ink-2); }
.rv-status .bar { display: inline-block; vertical-align: middle; width: 120px; height: 6px; border-radius: 3px;
  background: var(--grid); overflow: hidden; margin-left: 6px; }
.rv-status .bar span { display: block; height: 100%; background: var(--series-1); }
table.review td { vertical-align: middle; }
table.review td.loss { font-weight: 600; color: var(--alert); white-space: nowrap; }
table.review td.small { font-size: 12px; color: var(--ink-2); }
table.review .fam { font-size: 10px; color: var(--muted); border: 1px solid var(--border); border-radius: 4px;
  padding: 0 4px; margin-left: 4px; }
table.review a.open { font-weight: 600; text-decoration: none; white-space: nowrap; }
.freq { font-variant-numeric: tabular-nums; white-space: nowrap; }
.freq .th { color: var(--muted); }
.conf { font-size: 11px; border-radius: 4px; padding: 0 5px; }
.conf.solide { background: var(--hi-bg); }
.conf.indicatif { background: var(--lo-bg); }
"""

SCRIPT = """
(function () {
  var box = document.querySelector('.rv-head');
  if (!box) return;
  var who = box.dataset.villain, done = Number(box.dataset.done), timer = null;
  var run = box.querySelector('.rv-run'), stop = box.querySelector('.rv-stop'), status = box.querySelector('.rv-status');
  var query = who ? '?adversaire=' + encodeURIComponent(who) : '';
  function post(url) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adversaire: who || null }) }).then(function (r) { return r.json(); });
  }
  function show(s) {
    if (s.done > done + 4 || (s.done !== done && !s.busy)) { location.reload(); return; }
    status.textContent = '';
    stop.hidden = !s.busy;
    run.disabled = !s.ready;
    run.hidden = !!s.busy || s.done === s.total;
    if (!s.ready) { status.append('Installe d\\'abord le solveur : python -m analyzer gtopen --installer'); return; }
    if (s.current) {
      var p = s.current.progress || {}, frac = p.iteration ? Math.min(1, p.iteration / s.current.max_iterations) : 0;
      status.append('Main ' + s.current.hand + ' : ' + (p.iteration ? 'itération ' + p.iteration : 'construction de l\\'arbre')
        + ' · ' + s.done + ' / ' + s.total + ' analysées · ' + (s.busy - 1) + ' en attente');
      var bar = document.createElement('span'), fill = document.createElement('span');
      bar.className = 'bar'; fill.style.width = (100 * frac).toFixed(1) + '%'; bar.append(fill); status.append(bar);
    } else if (s.busy) status.append(s.busy + ' main(s) en attente (une autre résolution passe d\\'abord)');
    clearTimeout(timer);
    if (s.busy) timer = setTimeout(refresh, 3000);
  }
  function refresh() { fetch('/api/revue' + query).then(function (r) { return r.json(); }).then(show); }
  run.addEventListener('click', function () { run.disabled = true; post('/api/revue/lancer').then(show); });
  stop.addEventListener('click', function () { post('/api/revue/arreter').then(show); });
  refresh();
})();
"""


def _loss(value: float, unit: str = " bb") -> str:
    """EV perdue (en rouge) ; « 0 » en clair quand il n'y a rien à perdre."""
    if value < 0.005:
        return f'<td class="num">0{unit}</td>'
    return f'<td class="num loss">−{num(value, 2)}{unit}</td>'


def _duration(minutes: float) -> str:
    if minutes < 90:
        return f"{max(1, round(minutes))} minutes"
    hours = minutes / 60
    return f"{round(hours)} heures" if hours < 48 else f"{num(hours / 24, 1)} jours"


def _theory(d: dict) -> str:
    """Ce que le solveur joue avec cette main : les actions à plus de 5 %, de la plus jouée à la moins jouée."""
    if not d["strategy"]:
        return "–"
    parts = sorted(zip(d["strategy"], (a["label"] for a in d["actions"])), reverse=True)
    return " · ".join(f"{escape(label)} {round(100 * f)} %" for f, label in parts if f >= 0.05)


def _freqs(group: dict) -> str:
    n = group["n"]
    played = " / ".join(f"{round(100 * group['observed'][c] / n)}" for c, _ in review.CATEGORIES)
    theory = " / ".join(f"{round(100 * group['expected'][c] / n)}" for c, _ in review.CATEGORIES)
    return f'<span class="freq">{played} <span class="th">(théorie {theory})</span></span>'


def _costly_table(digests: list[dict], who: str, show_villain: bool) -> str:
    rows = []
    for g, d in review.costly(digests, who):
        link = f'/explorateur/{quote(g["hand"], safe="")}#d={d["d"]}'
        cards = [d["combo"][:2], d["combo"][2:]] if d["combo"] else []
        rows.append(
            f'<tr><td class="small">{escape(g["date"])}</td>'
            + (f'<td>{escape(g["villain"])}</td>' if show_villain else "")
            + f'<td>{escape(d["label"])}<span class="fam">{FAMILY_NAME[g["family"]]}</span></td>'
            f'<td>{cards_html(cards)}</td><td>{cards_html(d["board"])}</td>'
            f'<td>{escape(d["played"])}{" ≈" if d["approx"] else ""}</td><td class="small">{_theory(d)}</td>'
            + _loss(review.loss(d)) +
            f'<td><a class="open" href="{escape(link)}" target="_blank" rel="noopener">Revoir ↗</a></td></tr>')
    if not rows:
        return '<p class="muted">Aucune erreur de plus de 0,25 bb dans les mains analysées.</p>'
    head = ("<th>Coup</th>" + ("<th>Adversaire</th>" if show_villain else "") +
            "<th>Situation</th><th>Main</th><th>Board</th><th>Joué</th><th>Le solveur, avec cette main</th>"
            '<th class="num">EV perdue</th><th></th>')
    return (f'<div class="scroll"><table class="stats review"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _drills() -> dict[str, set]:
    """Situations qu'on peut travailler dans l'entraîneur : familles qui ont au moins un spot résolu."""
    from ..theory import studyspots
    from .trainer import situation_labels
    families = {m.get("family") for m in studyspots.spot_studies().values()}
    return {f: set(situation_labels(f)) for f in studyspots.FAMILIES if f in families}


def _drill_link(group: dict, drills: dict[str, set]) -> str:
    if group["key"] not in drills.get(group["family"], ()):
        return ""
    href = f'/entraineur?famille={quote(group["family"])}&situation={quote(group["key"], safe="")}'
    return f'<a class="open" href="{escape(href)}" title="Travailler cette situation dans l\'entraîneur">S\'entraîner</a>'


def _situations_table(digests: list[dict], who: str, villain: bool) -> str:
    rows = []
    drills = {} if villain else _drills()
    for group in review.by_situation(digests, who)[:20]:
        devs = review.deviations(group)
        advice = "<br>".join(
            f'<span class="conf {dev["confidence"]}">{dev["confidence"]}</span> {escape(review.exploit(group, dev, villain))}'
            for dev in devs)
        lost = _loss(group["lost"]) if group["known"] else '<td class="num">–</td>'
        mean = _loss(group["lost"] / group["known"], "") if group["known"] else '<td class="num">–</td>'
        rows.append(
            f'<tr><td>{escape(group["label"])}<span class="fam">{FAMILY_NAME[group["family"]]}</span></td>'
            f'<td class="num">{group["n"]}</td>'
            + ("" if villain else f'<td class="num">{group["errors"]}</td>{lost}{mean}')
            + f'<td>{_freqs(group)}</td><td class="small">{advice}</td>'
            + ("" if villain else f"<td>{_drill_link(group, drills)}</td>") + "</tr>")
    if not rows:
        return '<p class="muted">Pas encore de main analysée.</p>'
    head = ("<th>Situation</th><th class=\"num\">Fois</th>"
            + ("" if villain else '<th class="num">Erreurs</th><th class="num">EV perdue</th><th class="num">Par fois</th>')
            + "<th>Fold / passif / agressif (%)</th><th>À retenir</th>" + ("" if villain else "<th></th>"))
    return (f'<div class="scroll"><table class="stats review"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _exploits_table(digests: list[dict]) -> str:
    rows = []
    for group in review.by_situation(digests, "V"):
        for dev in review.deviations(group):
            rows.append((abs(dev["z"]), group, dev))
    rows.sort(key=lambda r: -r[0])
    html_rows = [
        f'<tr><td>{escape(g["label"])}<span class="fam">{FAMILY_NAME[g["family"]]}</span></td>'
        f'<td class="num">{g["n"]}</td><td>{escape(dev["label"])}</td>'
        f'<td class="num">{round(100 * dev["observed"])} %</td><td class="num">{round(100 * dev["expected"])} %</td>'
        f'<td><span class="conf {dev["confidence"]}">{dev["confidence"]}</span></td>'
        f'<td>{escape(review.exploit(g, dev, True))}</td></tr>' for _, g, dev in rows[:20]]
    if not html_rows:
        return ('<p class="muted">Aucun écart net pour l\'instant (il faut au moins 8 occurrences d\'une situation et '
                'un écart de 10 points à la théorie).</p>')
    return ('<div class="scroll"><table class="stats review"><thead><tr><th>Situation</th><th class="num">Fois</th>'
            '<th>Action</th><th class="num">Lui</th><th class="num">Théorie</th><th>Confiance</th><th>À exploiter</th>'
            f'</tr></thead><tbody>{"".join(html_rows)}</tbody></table></div>')


def build_review_page(hands: list[Hand], hero: str, villain: Optional[str] = None, embed: bool = True) -> str:
    digests, todo = review.collect(hands, hero)
    total = len(digests) + len(todo)
    mine = review.decisions_of(digests, "H")
    known = [review.loss(d) for _, d in mine if d["ev_loss"] is not None]
    lost = sum(known)
    errors = sum(x >= review.ERROR for x in known)
    tiles = (
        '<div class="tiles">'
        f'<div class="tile"><div class="label">Mains analysées</div><div class="value">{len(digests)}</div>'
        f'<div class="sub">sur {total} allées au flop</div></div>'
        f'<div class="tile"><div class="label">Tes décisions</div><div class="value">{len(known)}</div>'
        f'<div class="sub">{errors} erreur(s) de plus de 0,25 bb</div></div>'
        f'<div class="tile"><div class="label">EV perdue</div><div class="value">−{num(lost, 1)} bb</div>'
        f'<div class="sub">{num(lost / len(digests), 2) if digests else "–"} bb par main analysée</div></div>'
        '</div>')
    eta = sum(MINUTES.get(s.pot_type, 3) for s in todo)
    head = (f'<div class="rv-head" data-villain="{escape(villain or "")}" data-done="{len(digests)}">'
            f'<span><b>{len(digests)} / {total}</b> mains analysées</span>'
            f'<button type="button" class="go rv-run"{" hidden" if not todo else ""}>Analyser les {len(todo)} mains '
            'restantes</button><button type="button" class="rv-stop" hidden>Arrêter</button>'
            '<span class="rv-status"></span></div>'
            + (f'<p class="note">Chaque main se résout une fois (les plus gros pots d\'abord) : compte environ {_duration(eta)} '
               'sur 4 cœurs pour les restantes. La page se complète au fur et à mesure ; tu peux la fermer. '
               'En ligne de commande : <code>python -m analyzer gtopen --analyser</code>.</p>' if todo else ""))
    who = f"face à {escape(villain)}" if villain else "toutes tables confondues"
    body = f"""
<div class="meta">Tes mains allées au flop {who}, comparées au solveur (pots simples, 3bet et 4bet).</div>
<div class="card">{head}</div>
{tiles}
<h2>Les erreurs qui coûtent le plus</h2>
<div class="card">{_costly_table(digests, "H", villain is None)}
<p class="note">EV perdue : ce que l'action jouée rapporte de moins que la meilleure action pour ta main, à ce moment
du coup (en bb). Une action que le solveur joue au moins 10 % du temps avec cette main ne coûte rien. « ≈ » : la taille jouée diffère de celle de
l'arbre. <b>Revoir ↗</b> ouvre la main dans l'explorateur, à cette décision.</p></div>
<h2>Les erreurs récurrentes</h2>
<div class="card">{_situations_table(digests, "H", False)}
<p class="note">Par situation de la ligne : combien de fois tu l'as jouée, tes erreurs et l'EV perdue, puis tes
fréquences (fold / check ou call / mise ou relance) face à celles qu'aurait eues le solveur avec toute sa range
dans les mêmes coups. Un écart s'affiche dès 8 occurrences et 10 points d'écart ; « solide » quand le hasard
l'explique très mal.</p></div>
"""
    if villain:
        body += f"""
<h2>Ses écarts à exploiter</h2>
<div class="card">{_exploits_table(digests)}
<p class="note">Ses fréquences dans chaque situation, face à celles du solveur dans les mêmes coups : pas besoin de
voir ses cartes, toutes ses décisions comptent.</p></div>
<h2>Ses erreurs connues</h2>
<div class="card">{_costly_table(digests, "V", False)}
<p class="note">Quand ses cartes ont été montrées : l'EV qu'il a laissée, décision par décision.</p></div>
"""
    title = f"Face au solveur — {villain}" if villain else "Face au solveur"
    return html_page(title, f"<style>{STYLE}</style>{body}", embed, script=SCRIPT)
