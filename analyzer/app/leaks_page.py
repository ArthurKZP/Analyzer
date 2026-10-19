"""Page « Leakfinding » : le rapport d'un joueur (toi ou un élève) et ce qu'il doit travailler (voir analyzer/leaks.py,
et analyzer/ring_leaks.py pour les tables à plusieurs). Deux rapports : le heads-up, et les tables à plusieurs (3 à 9 joueurs ensemble).

En version autonome (standalone), la page se télécharge pour être envoyée à l'élève : sans boutons ni liens vers
l'application."""
from __future__ import annotations

import re
from html import escape
from typing import Optional
from urllib.parse import quote

from .. import leaks, replay
from ..report import cards_html, fold, format_query, format_switch, html_page, more, num
from ..selfreport import OPP_SCRIPT, OPP_STYLE, opponent_row_data, opponent_tools
from ..theory import review, studyspots
from .review_page import STYLE as REVIEW_STYLE
from .review_page import PICKER_SCRIPT, _costly_table, _drills, _duration, _situations_table, solve_picker
from .ring_page import _why

STYLE = """
.lk-list { margin: 0; padding-left: 22px; display: flex; flex-direction: column; gap: 10px; }
.lk-list li::marker { font-weight: 700; }
.lk-t { font-weight: 600; }
.lk-h { font-weight: 600; color: var(--ink); }
.lk-h + .lk-t { font-weight: 400; font-size: 12px; color: var(--muted); }
.lk-brief { border: 1px solid var(--border); border-left: 3px solid var(--series-1); border-radius: 8px;
  padding: 10px 14px; margin: 4px 0 14px; background: var(--page); }
.lk-brief b.ttl { display: block; margin-bottom: 4px; }
.lk-brief ol { margin: 0; padding-left: 20px; font-size: 14px; }
.lk-brief li { margin: 3px 0; }
.lk-brief .more { color: var(--muted); font-size: 12px; }
.lk-th { margin: 12px 0 6px; font-size: 13px; color: var(--ink-2); text-transform: none; }
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
.lk-exp { margin: 10px 0 0; color: var(--muted); }
button.lk-solve { display: block; font: inherit; font-size: 12px; margin-top: 4px; padding: 2px 10px;
  border-radius: 6px; cursor: pointer; border: 1px solid var(--series-1); background: var(--surface);
  color: var(--series-1); }
button.lk-solve:disabled { opacity: .5; cursor: default; }
button.lk-follow { display: block; font: inherit; font-size: 12px; margin-top: 4px; padding: 2px 10px;
  border-radius: 6px; cursor: pointer; border: 1px solid var(--border); background: var(--surface);
  color: var(--ink-2); }
a.lk-track { display: inline-block; font-size: 12px; margin-top: 4px; font-weight: 600; }
.rv-pick select { font: inherit; font-size: 13px; }
.rv-eta { font-size: 12px; color: var(--muted); }
details.lk-all { margin: 14px 0 0; }
details.lk-all > summary { font-weight: 600; }
details.lk-all > .inner-body { padding: 12px 16px; }
.lk-hint { border-left: 3px solid var(--series-4); padding: 6px 10px; margin: 12px 0 0; font-size: 13px; color: var(--ink-2);
  background: var(--surface); border-radius: 0 6px 6px 0; }
.lk-v { font-size: 13px; margin-top: 4px; color: var(--ink-2); }
.lk-v.fuite { color: var(--alert); font-weight: 600; }
.lk-v.exploit { color: var(--good); font-weight: 600; }
.lk-v.plan { color: var(--series-1); font-weight: 600; }
.lk-v.lk-n { font-style: italic; }
.lk-l { font-size: 13px; margin-top: 4px; color: var(--series-1); }
.lk-p { margin: 2px 0 0; padding-left: 18px; font-size: 12px; color: var(--ink-2); }
.lk-p li { margin: 1px 0; }
.lk-b { display: inline-block; font-size: 11px; border-radius: 999px; padding: 0 7px; border: 1px solid var(--axis);
  white-space: nowrap; color: var(--muted); border-style: dashed; }
.lk-b.fuite { color: var(--alert); background: var(--alert-bg); border: 1px solid var(--alert); }
.lk-b.exploit { color: var(--good); border: 1px solid var(--good); }
.lk-b.plan { color: var(--series-1); border: 1px solid var(--series-1); }
.lk-b.chance { color: var(--good); border: 1px dashed var(--good); }
.lk-v.chance { color: var(--good); font-weight: 600; }
.lk-cats { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; margin: 4px 0 16px; }
.lk-cat { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--page); }
.lk-cat a.nm { font-weight: 700; font-size: 15px; color: var(--ink); text-decoration: none; }
.lk-cat a.nm:hover { color: var(--series-1); }
.lk-cat .ct { font-size: 13px; color: var(--ink-2); margin-top: 2px; }
.lk-cat .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
ol.lk-mini { margin: 6px 0 0; padding-left: 22px; font-size: 13px; }
ol.lk-mini li { margin: 2px 0; }
ol.lk-mini li::marker { color: var(--muted); font-weight: 600; }
.lk-sec { margin-top: 18px; }
.lk-sec > h3 { margin: 0 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--border); }
.lk-sec > h3 .n { font-size: 13px; font-weight: 400; color: var(--muted); }
.lk-sec:target > h3, .lk-list li:target > .lk-t { color: var(--series-1); }
details.lk-det { margin: 6px 0 0; background: none; border: 0; border-radius: 0; }
details.lk-det > summary { font-size: 13px; font-weight: 600; color: var(--series-1); cursor: pointer; padding: 0;
  border: 0; }
details.lk-det > .lk-det-body { margin: 6px 0 4px; padding: 8px 12px; border-left: 2px solid var(--border);
  background: var(--surface); border-radius: 0 8px 8px 0; }
.lk-fq { display: grid; grid-template-columns: max-content minmax(120px, 260px) max-content; justify-content: start;
  gap: 4px 10px; align-items: center; font-size: 13px; margin-bottom: 8px; }
.lk-fq .lb { color: var(--ink-2); }
.lk-fq .tr { position: relative; height: 8px; border-radius: 4px; background: var(--grid); overflow: hidden; }
.lk-fq .tr i { position: absolute; top: 0; bottom: 0; border-radius: 4px; }
.lk-fq .tr i.me { background: var(--series-1); }
.lk-fq .tr i.th { background: var(--axis); }
.lk-fq .v { font-variant-numeric: tabular-nums; font-weight: 600; }
.lk-fq .v .n { font-weight: 400; color: var(--muted); font-size: 12px; }
.lk-xi { font-size: 13px; color: var(--ink-2); margin: 4px 0; }
table.lk-x td { vertical-align: middle; }
table.lk-x td.res.neg { color: var(--alert); }
table.lk-x td.go, table.lk-x td.num { white-space: nowrap; }
"""

SCRIPT = PICKER_SCRIPT + """
(function () {
  var box = document.querySelector('.lk-head');
  if (!box) return;
  var api = box.dataset.api, query = box.dataset.query || '', done = Number(box.dataset.done), timer = null;
  var run = box.querySelector('.lk-run'), stop = box.querySelector('.lk-stop'), status = box.querySelector('.rv-status');
  var picker = rvPicker(box), solves = document.querySelectorAll('.lk-solve');
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}) }).then(function (r) { return r.json(); });
  }
  function show(s) {
    if (s.done !== done && (!s.busy || s.done > done + 2)) { location.reload(); return; }
    status.textContent = '';
    stop.hidden = !s.busy;
    run.disabled = !s.ready;
    run.hidden = !!s.busy || s.done >= s.total;
    if (picker) picker.el.hidden = run.hidden;
    solves.forEach(function (b) { b.disabled = !s.ready || !!s.busy; });
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
  run.addEventListener('click', function () {
    run.disabled = true;
    post(api + '/lancer' + query, picker ? picker.choice() : null).then(show);
  });
  stop.addEventListener('click', function () { post(api + '/arreter' + query).then(show); });
  document.querySelectorAll('.lk-follow').forEach(function (b) {  // suivre un leak dans le temps, dès aujourd'hui
    b.addEventListener('click', function () {
      b.disabled = true;
      var today = new Date();
      var iso = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      post(api.replace(/\/leaks$/, '/suivi') + query, { leak: b.dataset.leak, depuis: iso, titre: b.dataset.title })
        .then(function () { location.reload(); });
    });
  });
  solves.forEach(function (b) {  // les mains d'un leak, les plus chères d'abord
    b.addEventListener('click', function () {
      solves.forEach(function (x) { x.disabled = true; });
      post(api + '/lancer' + query, { leak: b.dataset.leak, limite: Number(b.dataset.limit) }).then(show);
      var section = document.querySelector('details.fold[data-fold="solveur"]');
      if (section) { section.open = true; box.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    });
  });
  refresh();
})();
"""

# Un leak choisi dans l'aperçu (ou dans l'adresse) : son détail s'ouvre.
DETAIL_SCRIPT = """
(function () {
  function open(hash) {
    var el = hash && hash.length > 1 ? document.getElementById(hash.slice(1)) : null;
    var d = el ? el.querySelector('details.lk-det') : null;
    if (d) d.open = true;
  }
  document.querySelectorAll('ol.lk-mini a').forEach(function (a) {
    a.addEventListener('click', function () { open(a.getAttribute('href')); });
  });
  open(location.hash);
})();
"""

# Le type d'un adversaire, réglé dans la liste : la page se recalcule.
KIND_SCRIPT = """
document.querySelectorAll('select.lk-kind').forEach(function (sel) {
  sel.addEventListener('change', function () {
    fetch('/api/joueurs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: sel.dataset.name, kind: sel.value || null }) }).then(function () {
      // le menu de l'application (les « réc. » des adversaires) suit
      if (window.parent !== window) window.parent.postMessage({ type: 'analyzer-refresh' }, location.origin);
      location.reload();
    });
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
        text += (f' <a class="open" href="/explorateur/{quote(hand, safe="")}#d={index}" target="_blank" '
                 'rel="noopener">Revoir ↗</a>')
    return f'<div class="lk-e">{text}</div>'


def _verified(leak: leaks.Leak) -> str:
    """Ce que la vérification en jeu en dit (leakcheck), ses preuves, une éventuelle réserve et pourquoi l'écart est lié
    à un autre : le détail d'un leak, replié sous « Pourquoi ? »."""
    out = ""
    if leak.check is not None:
        out += f'<div class="lk-v {_class(leak.check)}">{escape(leak.check.summary)}</div>'
        if leak.check.proofs:
            out += '<ul class="lk-p">' + "".join(f"<li>{escape(p)}</li>" for p in leak.check.proofs) + "</ul>"
    if leak.note:
        out += f'<div class="lk-v lk-n">{escape(leak.note)}</div>'
    for link in leak.causes:
        out += f'<div class="lk-v">{escape(link.text())}</div>'
    return out


def _related(leak: leaks.Leak) -> str:
    """Les leaks liés, en une ligne chacun : ce qui l'explique sans doute, ce qu'il entraîne sans doute."""
    out = ""
    for link in leak.causes:
        kept = link.cause.check is not None and link.cause.check.verdict in leaks.KEPT
        then = "ce n'est sans doute pas une fuite à part" if kept else "corrige d'abord celui-là"
        out += f'<div class="lk-l">↳ Sans doute une conséquence de « {escape(link.short())} » : {then}.</div>'
    if leak.effects:
        out += ('<div class="lk-l">Entraîne sans doute aussi : '
                + escape(", ".join(f"« {x} »" for x in leak.effects)) + '.</div>')
    return out


def _solve_button(leak: leaks.Leak) -> str:
    """Passer au solveur les mains (pas encore analysées) où se joue ce leak, les plus chères d'abord."""
    if not leak.targets:
        return ""
    n = min(len(leak.targets), leaks.LEAK_SOLVES)
    minutes = sum(m for _, m in leak.targets[:n])
    more_hands = f" sur {len(leak.targets)}" if len(leak.targets) > n else ""
    return (f'<button type="button" class="lk-solve" data-leak="{escape(leak.key)}" data-limit="{n}">Passer ses {n} '
            f'main{"s" if n > 1 else ""} les plus chères{more_hands} au solveur (≈ {_duration(minutes)})</button>')


def _follow(leak: leaks.Leak, follows: Optional[dict], track: str) -> str:
    """Le suivi d'un leak dans le temps : depuis quand il est suivi (lien vers le Suivi, à l'adresse track), ou le
    bouton pour le suivre."""
    if follows is None or not leak.key or not leak.direction or leak.key.startswith("solveur:"):
        return ""
    found = follows.get(leak.key)
    if found:
        since = found.get("since", "")
        shown = "/".join(reversed(since.split("-"))) if since else ""
        return f'<a class="lk-track" href="{escape(track)}" target="_self">Suivi depuis le {escape(shown)} ↗</a>'
    return (f'<button type="button" class="lk-follow" data-leak="{escape(leak.key)}" data-title="{escape(leak.title)}">'
            "Suivre ce leak à partir d'aujourd'hui</button>")


def _freq(leak: leaks.Leak) -> str:
    """Sa fréquence face à celle de la théorie (ou à sa fourchette), en deux barres."""
    if not leak.freq:
        return ""
    p, reference, band, n = leak.freq

    def bar(lo: float, hi: float, cls: str) -> str:
        width = max(100 * (hi - lo), 0.8)
        return f'<span class="tr"><i class="{cls}" style="left:{100 * lo:.1f}%;width:{width:.1f}%"></i></span>'

    out = (f'<span class="lb">Joué</span>{bar(0.0, p, "me")}'
           f'<span class="v">{round(100 * p)} % <span class="n">sur {n} occasion{"s" if n > 1 else ""}</span></span>')
    if reference is not None:
        out += (f'<span class="lb">Théorie</span>{bar(0.0, reference, "th")}'
                f'<span class="v">{round(100 * reference)} %</span>')
    elif band is not None:
        out += (f'<span class="lb">Repère</span>{bar(band[0], band[1], "th")}'
                f'<span class="v">{round(100 * band[0])}–{round(100 * band[1])} %</span>')
    return f'<div class="lk-fq">{out}</div>'


def _examples(leak: leaks.Leak, api: str, standalone: bool) -> str:
    """Ses décisions où se joue l'écart (leak_examples), les plus parlantes d'abord, chacune à revoir."""
    ex = leak.examples
    if ex is None or not ex.shown:
        return ""
    boards = any(e.board for e in ex.shown)
    rows = []
    for e in ex.shown:
        link = ""
        if not standalone:
            if e.solver is not None:
                href, text = f"/explorateur/{quote(e.hand, safe='')}#d={e.solver}", "Au solveur ↗"
            else:
                href, text = replay.href(e.hand, api, e.at), "Rejouer ↗"
            link = (f'<a class="open" href="{escape(href)}" target="_blank" rel="noopener" '
                    f'title="Main {escape(e.hand)}">{text}</a>')
        value = "–" if e.value is None else f"{num(e.value, 1, sign=True)} bb"
        neg = " neg" if e.value is not None and e.value < -0.005 else ""
        rows.append(f'<tr><td>{cards_html(e.cards)}</td>{f"<td>{cards_html(e.board)}</td>" if boards else ""}'
                    f'<td>{escape(e.did)}</td><td class="small">{escape(e.why) or "–"}</td>'
                    f'<td class="num res{neg}">{value}</td>'
                    + (f'<td class="small">{escape(e.hand)}</td>' if standalone else f'<td class="go">{link}</td>')
                    + "</tr>")
    shown = len(ex.shown)
    times = f"{ex.total} fois"
    intro = f"{times} ; ici les {shown} plus parlantes : {ex.order}." if ex.total > shown else f"{times} : {ex.order}."
    head = ("<th>Main</th>" + ("<th>Board</th>" if boards else "") + "<th>Joué</th><th>Pourquoi cet exemple</th>"
            f'<th class="num">{escape(ex.value_label)}</th><th>{"Coup" if standalone else ""}</th>')
    return (f'<div class="lk-xi">{escape(intro)}</div><div class="scroll"><table class="stats lk-x"><thead><tr>{head}'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _items(found: list[leaks.Leak], pages: str, standalone: bool, badge: bool = True, solve: bool = False,
           follows: Optional[dict] = None, track: str = "", ranks: Optional[list[int]] = None) -> str:
    """Les leaks, l'essentiel visible (le titre, la preuve en une ligne, le conseil) ; leur détail replié : sa
    fréquence face à la théorie, ses mains (les exemples), la vérification en jeu et ses preuves ; solve : le bouton
    qui passe ses mains au solveur ; follows : les leaks suivis dans le temps (tracking.followed), pour les suivre
    d'ici ; ranks : leur rang parmi tous les leaks (numéro et ancre « lk-<rang> »)."""
    drills = {} if standalone else _drills()
    api = replay.api_of(pages)
    out = []
    for k, x in enumerate(found):
        proofs = _verified(x) + _example(x, standalone)
        examples = _examples(x, api, standalone)
        detail = _freq(x) + examples + (f'<div class="lk-v"><b>Les preuves</b></div>{proofs}'
                                        if proofs and (examples or x.freq) else proofs)
        if x.examples is not None and x.examples.total:
            label = f"Le détail : fréquence et mains ({x.examples.total})"
        else:
            label = "Le détail : fréquence et preuves" if x.freq else "Pourquoi ? Les preuves"
        action = _solve_button(x) if solve and not standalone else ""
        action += "" if standalone else _follow(x, follows, track)
        rank = f' value="{ranks[k]}" id="lk-{ranks[k]}"' if ranks else ""
        folded = (f'<details class="lk-det"><summary>{escape(label)}</summary><div class="lk-det-body">{detail}'
                  "</div></details>") if detail else ""
        marks = f'{_conf(x.confidence)}{f" {_badge(x.check)}" if badge and x.check else ""}'
        head = (f'<div class="lk-h">{escape(x.headline)}</div><div class="lk-t">{escape(x.title)} {marks}</div>'
                if x.headline else f'<div class="lk-t">{escape(x.title)} {marks}</div>')
        out.append(f'<li{rank}>{head}'
                   f'<div class="lk-e">{escape(x.evidence)}</div>'
                   f'<div class="lk-a">→ {escape(x.advice)}{_leak_link(x, pages, drills, standalone)}</div>'
                   f'{_related(x)}{folded}{action}</li>')
    return "".join(out)


def _solidity(solid: int, n: int) -> str:
    """« dont 2 solides », « tous solides », « indicatif » : la confiance des leaks d'une catégorie."""
    if solid == n:
        return "solide" if n == 1 else "tous solides"
    if not solid:
        return "indicatif" if n == 1 else "tous indicatifs"
    return f"dont {solid} solide{'s' if solid > 1 else ''}"


def _short(title: str, category: str) -> str:
    """Le titre d'un leak dans sa catégorie (« Préflop · Bouton : open » -> « Bouton : open »)."""
    return title.split(" · ", 1)[1] if category == "preflop" and title.startswith("Préflop · ") else title


def leaks_html(report: leaks.Report, pages: str, standalone: bool, follows: Optional[dict] = None) -> str:
    """Les leaks par catégorie : d'abord un aperçu (une carte par catégorie, ses leaks en une ligne), puis chaque
    catégorie et ses leaks, leur détail (fréquence, mains) à déplier."""
    if not report.leaks:
        return ('<p class="note">Pas de leak net pour l\'instant : pas assez de mains contre les réguliers, ou un jeu '
                'proche de la théorie dans les situations mesurées (ou des écarts qui rapportent, plus bas). Les mains '
                'analysées par le solveur affinent le rapport.</p>')
    track = f"{pages}/suivi{format_query(report.table_format)}"
    cards, sections = [], []
    for (key, name, what), items in leaks.by_category(report.leaks):
        n = len(items)
        solid = sum(x.confidence == "solide" for _, x in items)
        s_ = "s" if n > 1 else ""
        mini = "".join(f'<li value="{k}"><a href="#lk-{k}" title="{escape(x.title)}">{escape(_short(x.summary, key))}'
                       "</a></li>" for k, x in items)
        cards.append(f'<div class="lk-cat"><a class="nm" href="#lk-c-{key}">{escape(name)}</a>'
                     f'<div class="ct">{n} leak{s_}, {_solidity(solid, n)}</div>'
                     f'<div class="sub">{escape(what)}</div><ol class="lk-mini">{mini}</ol></div>')
        groups = leaks.themes(items)
        body = ""
        for street, group in groups:  # par street : les leaks d'un même moment du coup ensemble
            heading = (f'<h4 class="lk-th">{escape(leaks.STREET_NAMES.get(street, "Après le flop"))}</h4>'
                       if len(groups) > 1 and street != "preflop" else "")
            body += heading + '<ol class="lk-list">' + _items(
                [x for _, x in group], pages, standalone, solve=True, follows=follows, track=track,
                ranks=[k for k, _ in group]) + "</ol>"
        sections.append(f'<section class="lk-sec" id="lk-c-{key}"><h3>{escape(name)} <span class="n">{n} leak{s_}'
                        f'</span></h3>{body}</section>')
    return (f'{brief_html(report.leaks)}<p class="lk-xi">Par catégorie, puis par street, numérotés du plus important '
            "au moins important : chaque leak en une phrase, puis où il se joue et ses chiffres ; choisis-en un pour "
            f'son détail (sa fréquence, ses mains).</p><div class="lk-cats">{"".join(cards)}</div>{"".join(sections)}')


def brief_html(found: list[leaks.Leak]) -> str:
    """En bref : les thèmes (moment du coup) où se jouent les leaks les plus importants, chacun en une phrase."""
    themes = leaks.overview(found)
    if not themes:
        return ""
    items = []
    for name, group in themes:
        k, best = group[0]
        others = len(group) - 1
        more = (f' <span class="more">(et {others} autre{"s" if others > 1 else ""} : '
                + ", ".join(f'<a href="#lk-{r}">n° {r}</a>' for r, _ in group[1:]) + ")</span>") if others else ""
        text = leaks.lower_first(best.headline) if best.headline else best.title
        items.append(f'<li><b>{escape(name)}</b> — <a href="#lk-{k}">{escape(text)}</a>{more}</li>')
    return f'<div class="lk-brief"><b class="ttl">En bref</b><ol>{"".join(items)}</ol></div>'



def kept_html(report: leaks.Report, pages: str, standalone: bool) -> str:
    """Les écarts à la théorie justifiés en jeu : ils exploitent ses adversaires, ou suivent le plan de jeu."""
    if not report.exploits:
        return ""
    note = ("Des écarts à la théorie qui, vérifiés sur ses mains contre les réguliers, rapportent (leur fold face à "
            "ses mises, ce que ses mains jouées en plus ont rapporté), que les réponses de ses adversaires justifient, "
            "ou qui suivent le plan de jeu suggéré : ce ne sont pas des fuites. À surveiller : si ses adversaires "
            "s'adaptent, la théorie redevient la référence.")
    n = len(report.exploits)
    how = more("Comment c'est vérifié", escape(note))
    items = _items(report.exploits, pages, standalone)
    return fold("Les écarts justifiés", f'<div class="card"><ul class="lk-list">{items}</ul>{how}</div>',
                f"{n} écart{'s' if n > 1 else ''} à la théorie qui rapporte{'nt' if n > 1 else ''} ou suit le plan "
                "de jeu : à garder", "justifies")


def chances_html(report: leaks.Report, pages: str, standalone: bool) -> str:
    """Les exploitations possibles : là où il joue comme la théorie, un écart qui rapporterait contre ses adversaires."""
    if not report.opportunities:
        return ""
    note = ("Là où il joue comme la théorie, mais où ses adversaires réagissent mal : leur fold face à ses mises ou "
            "relances, comparé à celui de la théorie, rend un écart rentable. Le gain est chiffré pour 10 points de "
            "fréquence en plus (ou en moins) ; il baisse à mesure qu'on s'écarte, les mains ajoutées étant les plus "
            "faibles.")
    n = len(report.opportunities)
    how = more("Comment c'est chiffré", escape(note))
    items = _items(report.opportunities, pages, standalone)
    return fold("Les exploitations possibles", f'<div class="card"><ul class="lk-list">{items}</ul>{how}</div>',
                f"{n} situation{'s' if n > 1 else ''} où ses adversaires réagissent mal à son jeu théorique",
                "exploitations")


CLASSES = {"exploit": "exploit", "fuite": "fuite", "plan": "plan", "opportunité": "chance"}


def _class(check) -> str:
    """La classe CSS d'une vérification en jeu."""
    return CLASSES.get(check.verdict, "incertain")


def _badge(check) -> str:
    """La vérification en jeu d'un écart, en un mot (leakcheck.Check.badge)."""
    return f'<span class="lk-b {_class(check)}" title="{escape(check.summary)}">{escape(check.badge)}</span>'


def _reference(s: leaks.Stat) -> str:
    if s.reference is not None:
        if s.sizing:  # ramené à sa taille : le détail au survol
            return (f'{round(100 * s.reference)} % <span class="n" title="{escape(s.size_note())}">'
                    f'{escape(s.size_text())}</span>')
        return f"{round(100 * s.reference)} %"
    if s.band is not None:
        return f"{round(100 * s.band[0])}–{round(100 * s.band[1])} %"
    return "–"


def _shows_verdict(report: leaks.Report, scope: str) -> bool:
    """Les colonnes où un écart se colore : contre les réguliers et contre les récréatifs (toutes ses mains seulement si
    c'est la portée comparée à la théorie)."""
    return scope != "all" or report.solver_scope == "all"


def gaps_table(found: list, scope: str, who: str, related: Optional[dict] = None) -> str:
    """Le tableau des écarts les plus importants (leaks.top_stat_gaps) : sa fréquence, la théorie, l'écart, la
    confiance, ce qu'en dit la vérification en jeu (quand elle a été faite) et ce qu'il faut faire ; who : l'en-tête de
    sa colonne ; related : les écarts qui en expliquent d'autres (leaks.links)."""
    checked = any(leaks._checked(s, scope) is not None for s, _, _ in found)
    rows = []
    for s, direction, confidence in found:
        r = s.ratios[scope]
        gap = round(100 * s.gap(scope))
        check = leaks._checked(s, scope)
        advice = leaks.gap_advice(s, direction, scope)
        causes = (related or {}).get(s.key)
        if causes:
            advice += " — lié à « " + " », « ".join(x.short() for x in causes) + " » : corrige d'abord celui-là"
        note = f' <span class="n" title="{escape(s.note)}">*</span>' if s.note else ""
        played = f'<td>{_badge(check) if check is not None else ""}</td>' if checked else ""
        rows.append(f'<tr><td>{escape(s.label)}<span class="where">{escape(s.section)}</span></td>'
                    f'<td class="num {direction}">{_pct(r)} <span class="n">({r.opps})</span></td>'
                    f'<td class="num">{_reference(s)}{note}</td>'
                    f'<td class="num">{"+" if direction == "plus" else "−"}{gap} pts</td>'
                    f'<td>{_conf(confidence)}</td>{played}<td class="small">{escape(advice)}</td></tr>')
    head = "<th>En jeu</th>" if checked else ""
    return ('<div class="scroll"><table class="stats lk"><thead><tr><th>Situation</th>'
            f'<th class="num">{escape(who)}</th><th class="num">Théorie</th><th class="num">Écart</th><th>Confiance</th>'
            f'{head}<th>À faire</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def gaps_html(report: leaks.Report) -> str:
    """Les écarts à la théorie les plus importants, avant le détail de toutes les stats."""
    found = leaks.top_gaps(report)
    if not found:
        return ('<p class="muted">Aucun écart net à la théorie pour l\'instant : pas assez d\'occasions, ou des fréquences '
                'proches de la théorie. Le détail de ses stats est plus bas.</p>')
    who = "Contre réguliers" if report.solver_scope == "reg" else "Ses mains"
    related = leaks.links(report.stats, report.solver_scope)
    return f'<h3>Les écarts les plus importants</h3>{gaps_table(found, report.solver_scope, who, related)}'


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
        check = leaks._checked(s, report.solver_scope)
        rows.append(f'<tr><td>{escape(s.label)}</td>{"".join(cells)}<td class="num">{_reference(s)}{note}</td>'
                    f'<td>{_conf(v[1]) if v else ""}{" " + _badge(check) if check is not None else ""}</td></tr>')
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
        # une main heads-up se rejoue dans Mes spots ; celle d'une table à plusieurs s'ouvre au solveur (au flop à
        # deux), sinon au replayer
        href = (f"{pages}/spots#hand={quote(p.hand.hand_id, safe='')}" if heads_up
                else f"/explorateur/{quote(p.hand.hand_id, safe='')}" if p.spot is not None
                else replay.href(p.hand.hand_id, replay.api_of(pages)))
    # le replayer et le solveur dans un nouvel onglet ; Mes spots, dans l'application
    blank = ' target="_blank" rel="noopener"' if href.startswith(("/rejouer/", "/explorateur/")) else ""
    link = "" if standalone else f'<a class="open" href="{escape(href)}"{blank}>Revoir ↗</a>'
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
    """Les mains de ses pots contre les réguliers passées au solveur : combien sont analysées, et le choix des
    suivantes (liées à ses leaks, les plus chères, une par ligne), avec la durée estimée."""
    orders = report.solve_orders
    done, todo = report.review["analyzed"], len(orders.get("pertes", []))
    eta = sum(m for _, m in orders.get("pertes", []))
    picker = solve_picker([(key, orders.get(key) or []) for key, _ in leaks.SOLVE_ORDERS],
                          "leaks" if orders.get("leaks") else "pertes")
    return (f'<div class="rv-head lk-head" data-api="{escape(api)}" data-query="{escape(format_query(report.table_format))}" '
            f'data-done="{done}"><span><b>{done} / {done + todo}</b> mains analysées</span>{picker}'
            f'<button type="button" class="go lk-run"{" hidden" if not todo else ""}>Analyser</button>'
            '<button type="button" class="lk-stop" hidden>Arrêter</button><span class="rv-status"></span></div>'
            + (f'<p class="note">Les {todo} mains qui restent prendraient environ {_duration(eta)} sur 4 cœurs : '
               "choisis-en une partie, celles où se jouent ses leaks ou les plus chères d'abord. Chaque main se résout "
               'une fois ; le rapport se complète au fur et à mesure, tu peux fermer la page.</p>' if todo else ""))


def opponents_html(opponents: list[dict], ring: bool = False, title: str = "Tes adversaires",
                   result: str = "Ton résultat", pick: bool = False,
                   groups: Optional[dict[str, list[str]]] = None, folded: bool = False) -> str:
    """Les adversaires du joueur et leur type, réglable : il décide quelles mains comptent pour les leaks. Recherche par
    nom, filtre par type, tri par mains, résultat ou date. ring : ceux des tables à plusieurs (mains à la même table,
    résultat dans les pots disputés ensemble) ; result : la colonne du résultat du joueur contre chacun (« Ton
    résultat », « Résultat de Paul ») ; pick : une case à cocher par joueur (pour les regrouper sous un alias) ;
    groups : les alias et leurs pseudos ({alias: [pseudos]}), signalés à côté du nom ; folded : une section repliée."""
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
    card = (f'<div class="card opp-box">{tools}<div class="scroll"><table class="stats opp-table">'
            f'<thead><tr>{first}<th data-sort="name">Adversaire</th>{head}<th>Type</th></tr></thead><tbody>'
            + "".join(rows) + f'</tbody></table></div><p class="note">{escape(note)}</p></div>')
    if folded:
        recs = sum(o.get("kind") == "rec" for o in opponents)
        return fold(title, card, f"{len(opponents)} adversaire{'s' if len(opponents) > 1 else ''}, dont {recs} "
                    f"récréatif{'s' if recs > 1 else ''} : leur type décide quelles mains comptent", "adversaires")
    return f'<h2>{escape(title)}</h2>{card}'


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
    """Les charts qui jugent son préflop selon ses limites ; aux tables à plusieurs, ce qui manque pour comparer à la
    théorie (charts, flops 6-max résolus)."""
    out = [report.context["stake_charts"]] if report.context.get("stake_charts") else []
    if not _ring(report):
        return "".join(f'<p class="lk-hint">{escape(text)}</p>' for text in out)
    if not report.context.get("charts"):
        where = "" if standalone else " (Paramètres › Ranges préflop : « Charger les charts 6-max »)"
        out.append("Ses charts ne sont pas encore là : le préflop n'est comparé qu'aux repères indicatifs de l'open d'un "
                   f"régulier 6-max. Avec les charts{where}, chaque décision est comparée main par main.")
    if not report.context.get("plans"):
        where = "" if standalone else " (Études du solveur › 6-max : « Résoudre tous les flops 6-max manquants »)"
        out.append(f"Pas encore de flop 6-max résolu : l'après-flop n'a pas de repère du solveur{where}.")
    return "".join(f'<p class="lk-hint">{escape(text)}</p>' for text in out)


LEAKS_NOTE = (
    "Rangés par catégorie (préflop, après le flop à l'initiative, en défense), numérotés dans l'ordre d'importance. "
    "Les grosses erreurs d'abord : les pertes confirmées (mesurées sur ses mains, face au fold ou au solveur, ou "
    "confirmées par les réponses de ses adversaires), puis les écarts encore à vérifier ; ensuite par confiance et par "
    "poids (ce qu'elles coûtent en bb/100 ; à défaut, fréquence de la situation × écart × ce que la décision met en "
    "jeu). Chaque écart à la théorie est vérifié en jeu : ce qu'il rapporte ou coûte sur ses mains (leur fold face à "
    "ses mises comparé à celui de la théorie, le résultat des mains jouées en plus ou en moins), la réponse de ses "
    "adversaires, le plan de jeu suggéré ; ceux qui rapportent sont à part (« Les écarts justifiés »). Seules les mains "
    "contre les réguliers comptent : contre un récréatif, l'exploitation prime sur la théorie. Le détail de chaque "
    "leak montre sa fréquence et ses mains : ses décisions dans le sens de l'écart, les plus parlantes d'abord (avant "
    "le flop, celles que la théorie joue le moins ainsi ; après le flop, ses mains les plus faibles quand il en fait "
    "trop, les plus fortes quand il n'en fait pas assez ; face au solveur, les plus chères).")


def build_leaks_page(report: leaks.Report, api: str, pages: str, embed: bool = True, standalone: bool = False,
                     name: Optional[str] = None, opponents: Optional[list[dict]] = None,
                     formats: Optional[list[tuple[str, int]]] = None, period: str = "",
                     follows: Optional[dict] = None) -> str:
    """api : adresse des actions (analyse de la sélection) ; pages : préfixe des pages du joueur (/moi, /eleve/x) ;
    formats : les formats de table de ses mains, avec leur nombre (le choix en haut de la page) ; period : la période
    analysée, en une phrase (rien pour toutes les mains)."""
    who = name or report.hero
    ring = _ring(report)
    fmt = report.table_format
    query = escape(format_query(fmt))
    download = "" if standalone else (
        '<p class="lk-exp small">Exporter : '
        f'<a class="lk-dl" href="{escape(pages)}/rapport{query}" download '
        'title="Page autonome à envoyer, sans les boutons de l\'application">le rapport</a> · '
        f'<a class="lk-dl" href="{escape(pages)}/rapport.pdf{query}" download '
        'title="Deux pages : chiffres, leaks, écarts, mains à revoir">la synthèse PDF</a>'
        + (f' · <a class="lk-dl" href="{escape(pages)}/presentation.pptx{query}" download '
           'title="Pour la séance avec l\'élève, avec les notes du coach">la présentation PowerPoint</a>'
           if name is not None else "") + '</p>')
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
        leaks_note = LEAKS_NOTE
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
        leaks_note = LEAKS_NOTE
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
    gaps = leaks.top_gaps(report)
    solid = sum(conf == "solide" for _, _, conf in gaps)
    rv = report.review
    reviewed = {scope: len(report.picks.get(scope, [])) for scope in ("reg", "rec")}
    s_ = lambda n: "s" if n > 1 else ""  # noqa: E731  (le pluriel)
    stats_part = fold(
        "Ses stats face à la théorie",
        f'<div class="card">{gaps_html(report)}<details class="lk-all"><summary>Le détail : toutes ses stats '
        f'({count} situation{s_(count)})</summary><div class="inner-body">{info_html(report)}{stats_html(report)}'
        f'</div></details><p class="note">{theory_note}</p></div>',
        f"{len(gaps)} écart{s_(len(gaps))} net{s_(len(gaps))}, dont {solid} solide{s_(solid)}, sur {count} "
        f"situation{s_(count)} mesurée{s_(count)}", "stats")
    solver_part = fold(
        solver_title,
        f'<div class="card">{"" if standalone else _head(report, api)}{solver}<p class="note">{solver_note}</p></div>',
        f"{rv['analyzed']} main{s_(rv['analyzed'])} analysée{s_(rv['analyzed'])}, {escape(num(rv['lost'], 1))} bb "
        f"perdus en {rv['decisions']} décision{s_(rv['decisions'])}", "solveur")
    picks_part = fold("Mains à revoir", picks, f"{reviewed['reg']} contre les réguliers, {reviewed['rec']} contre les "
                      "récréatifs : les plus gros pots de chaque ligne", "mains")
    notes = len(report.rec_notes)
    rec_part = fold("Contre les récréatifs", f'<div class="card"><ul>{rec}</ul></div>',
                    f"{notes} piste{s_(notes)} d'exploitation", "recreatifs") if rec else ""
    opponents_part = "" if standalone else opponents_html(
        opponents or [], ring, *(("Tes adversaires", "Ton résultat") if name is None else
                                 ("Ses adversaires", f"Résultat de {who}")), folded=True)
    body = f"""
<div class="meta">{meta}{f" <b>{escape(period)}</b>" if period else ""}</div>
{"" if standalone else format_switch(formats or [], fmt)}
{_tiles(report)}
{_hints(report, standalone)}
<h2>Les leaks à travailler</h2>
<div class="card">{leaks_html(report, pages, standalone, follows)}
{more("Comment ils sont classés et vérifiés", escape(leaks_note))}</div>
{download}
{kept_html(report, pages, standalone)}
{chances_html(report, pages, standalone)}
{stats_part}
{solver_part}
{picks_part}
{rec_part}
{opponents_part}
"""
    style = f"<style>{REVIEW_STYLE}{STYLE}{OPP_STYLE}</style>"
    title = f"Leakfinding tables à plusieurs — {who}" if ring else f"Leakfinding — {who}"
    return html_page(title, style + body, embed,
                     script=DETAIL_SCRIPT + ("" if standalone else SCRIPT + KIND_SCRIPT + OPP_SCRIPT))
