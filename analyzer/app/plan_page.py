"""Page « Plan de jeu suggéré » : les études résolues réduites à des règles simples (voir theory/coach.py)."""
from __future__ import annotations

import json
from html import escape
from urllib.parse import quote

from ..report import cards_html, html_page
from ..theory import coach, studyspots

SIZE_TEXT = {"small": "petite mise", "medium": "mise moyenne", "big": "grosse mise", "overbet": "overbet",
             "raise": "relance", "allin": "tapis"}

STYLE = """
.pl-fams { display: flex; gap: 0; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; width: fit-content; margin: 4px 0 12px; }
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
.pl-summary li { margin: 4px 0; }
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
  show(saved && document.querySelector('.pl-fams button[data-fam="' + saved + '"]') ? saved : first.dataset.fam);

  // « En bref » : un clic ouvre le schéma et y descend
  document.querySelectorAll('a.pl-jump').forEach(function (a) {
    a.addEventListener('click', function () {
      var target = document.querySelector(a.getAttribute('href'));
      if (target) target.open = true;
    });
  });
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


def _pct(x: float) -> str:
    return f"{round(100 * x)} %"


def _size(group: dict) -> str:
    text = SIZE_TEXT.get(group.get("size") or "", "")
    sizes = group.get("sizes") or []
    if sizes:
        text += " (" + " / ".join(f"{s} %" for s in sizes) + " du pot)"
    return text


def _rules(rules: list[tuple[str, str]]) -> str:
    if not rules:
        return '<p class="muted">Pas assez de données.</p>'
    return '<ul class="rules">' + "".join(f"<li><b>{escape(what)}</b> : {escape(who)}</li>" for what, who in rules) + "</ul>"


def _flops(rows: list[dict]) -> str:
    return '<div class="pl-flops">' + "".join(
        f'<a href="/explorateur/{quote(r["id"], safe="")}" target="_blank" rel="noopener" '
        f'title="{escape(r["texture"])} · {escape(r["suit_pattern"])} · c-bet {_pct(r["cbet"])}">{cards_html(r["board"])}</a>'
        for r in rows) + "</div>"


def _cards_table(rows: list[dict], first: str, measure: str, defender: bool = False) -> str:
    if not rows:
        return '<p class="muted">Pas encore de données.</p>'
    body = "".join(
        f'<tr><td>{escape(r["label"])}</td><td class="num">{r["n"]}</td>'
        f'<td class="num">{_pct(r["fold"] if defender else r["aggr"])}</td>'
        f'<td class="rules-cell">{_rules(r["rules"])}</td></tr>' for r in rows)
    return (f'<div class="scroll"><table class="stats pl-cards"><thead><tr><th>{escape(first)}</th>'
            f'<th class="num">Cartes</th><th class="num">{escape(measure)}</th><th>Comment</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _group(plan: dict, g: dict) -> str:
    who, other = plan["who"], plan["other"]
    textures = ", ".join(f"{t} ({n})" if n > 1 else t for t, n in g["textures"].items())
    d = g["defense"]
    defense = []
    if d["vs_cbet"]:
        defense.append(f'<h3>{escape(_cap(other))} face à '
                       f'la c-bet : folde {_pct(d["vs_cbet"]["fold"])}, relance {_pct(d["vs_cbet"]["raise"])}</h3>'
                       + _rules(d["vs_cbet"]["rules"]))
    if d["vs_xr"]:
        defense.append(f'<h3>{escape(_cap(who))} face à la relance : folde {_pct(d["vs_xr"]["fold"])}, '
                       f'sur-relance {_pct(d["vs_xr"]["raise"])}</h3>' + _rules(d["vs_xr"]["rules"]))
    if d["stab"]:
        defense.append(f'<h3>Quand {escape(who)} checke : {escape(other)} mise {_pct(d["stab"]["aggr"])}</h3>'
                       + _rules(d["stab"]["rules"]))
    if d["vs_barrel"]:
        defense.append("<h3>Face au 2e barrel, selon la turn</h3>"
                       + _cards_table(d["vs_barrel"], "Turn", "Fold", defender=True))
    if d["vs_barrel3"]:
        defense.append("<h3>Face au 3e barrel, selon la river</h3>"
                       + _cards_table(d["vs_barrel3"], "River", "Fold", defender=True))
    if d["probe"]:
        defense.append(f"<h3>Après un flop checké : {escape(other)} mène (probe) à la turn</h3>"
                       + _cards_table(d["probe"], "Turn", "Mise"))
    summary = (f'{escape(g["label"])} <span class="sub">· {len(g["flops"])} flop(s) · c-bet {_pct(g["cbet"])}'
               f'{" · " + escape(_size(g)) if g.get("size") else ""} · {escape(textures)}</span>')

    def sub(title: str, content: str) -> str:
        return f'<details class="pl-sub"><summary>{escape(title)}</summary><div>{content}</div></details>'

    return f"""
<details class="pl-group" id="schema-{g["pattern"]}"><summary>{summary}</summary><div class="pl-body">
{_flops(g["flops"])}
<div class="pl-why"><b>Pourquoi ?</b> {escape(g["why"])}</div>
<h3>Au flop : {escape(who)} c-bette {_pct(g["cbet"])}{(" en " + escape(_size(g))) if g.get("size") else ""}</h3>
{_rules(g["flop_rules"])}
{sub("À la turn, après la c-bet payée (2e barrel)", _cards_table(g["turn"], "Turn", "Continue"))}
{sub("À la river, après deux mises payées (3e barrel)", _cards_table(g["river"], "River", "Mise"))}
{sub("Après un flop checké (c-bet retardée à la turn)", _cards_table(g["delayed"], "Turn", "Mise"))}
{sub("En face : " + other, "".join(defense) or '<p class="muted">Pas encore de données.</p>')}
</div></details>"""


def _summary(plan: dict) -> str:
    items = []
    for g in plan["groups"]:
        textures = ", ".join(g["textures"])
        items.append(f'<li><a href="#schema-{g["pattern"]}" class="pl-jump"><b>{escape(g["label"])}</b></a> '
                     f'({len(g["flops"])} flop(s) : {escape(textures)}) : '
                     f'c-bet {_pct(g["cbet"])}{(" en " + escape(_size(g))) if g.get("size") else ""}.</li>')
    return '<ul class="pl-summary">' + "".join(items) + "</ul>"


def _table(plan: dict) -> str:
    rows = "".join(
        f'<tr><td><a href="/explorateur/{quote(r["id"], safe="")}" target="_blank" rel="noopener">{cards_html(r["board"])}</a></td>'
        f'<td>{escape(r["texture"])}</td><td>{escape(r["suit_pattern"])}</td>'
        f'<td>{escape(coach.PATTERN_LABEL[r["pattern"]])}</td><td class="num">{_pct(r["cbet"])}</td>'
        f'<td>{escape(SIZE_TEXT.get(r["size"] or "", "–"))}</td>'
        f'<td class="num">{coach._pts(r["eq_adv"])}</td><td class="num">{coach._pts(r["nut_adv"])}</td></tr>'
        for r in plan["flops"])
    return ('<div class="scroll"><table class="stats"><thead><tr><th>Flop</th><th>Texture</th><th>Couleurs</th>'
            '<th>Schéma</th><th class="num">C-bet</th><th>Taille</th><th class="num">Équité</th>'
            f'<th class="num">Nuts</th></tr></thead><tbody>{rows}</tbody></table></div>')


def _family(plan: dict) -> str:
    if not plan["count"]:
        hint = (f"{plan['missing']} étude(s) attendent leur lecture : clique sur <b>Préparer le plan</b>."
                if plan["missing"] else "Résous des flops de cette série (onglet de la série, ou l'explorateur) : le plan se "
                "construit à partir des études.")
        return f'<div class="card"><p class="muted">Pas encore de plan pour les {escape(plan["name"])}. {hint}</p></div>'
    total = len(studyspots.flop_set(plan["family"]))
    return f"""
<p class="note">{escape(plan["label"])}. D'après <b>{plan["count"]}</b> flop(s) résolu(s) (la série en compte {total}) ;
plus il y en a, plus le plan est précis. Les mains sont regroupées par famille (deux paires et mieux, overpair, top pair,
tirages…), les turns et rivers par effet sur le board (overcard, brique, board pairé, couleur ou quinte possible).</p>
<h2>En bref</h2>
<div class="card">{_summary(plan)}
<p class="note">Range bet : c-bet d'au moins 75 % de la range ; c-bet fréquente : 55 à 75 % ; mixte : 30 à 55 % ;
check fréquent : moins de 30 %. « Mélange » : le solveur joue les deux, selon la main exacte.</p></div>
<h2>Par schéma de flop</h2>
{"".join(_group(plan, g) for g in plan["groups"])}
<h2>Les flops</h2>
<div class="card">{_table(plan)}
<p class="note">Équité : écart d'équité moyenne entre les deux ranges au flop, pour celui qui a l'initiative ; nuts : écart
de part de deux paires et mieux. Un clic ouvre le flop dans l'explorateur.</p></div>
"""


def build_coach_page(state: dict, embed: bool = True, villains: tuple = ()) -> str:
    """state : bilan des plans (Library.plan_state) pour le bandeau de préparation ; villains : adversaires
    proposés dans les questions d'exemple du coach."""
    plans = [coach.family_plan(f) for f in studyspots.FAMILIES]
    default = max(plans, key=lambda p: p["count"])["family"] if plans else "srp"
    tabs = "".join(
        f'<button type="button" data-fam="{p["family"]}"{" data-default" if p["family"] == default else ""}>'
        f'{escape(p["name"])} <span class="muted">({p["count"]})</span></button>' for p in plans)
    missing = state.get("missing", 0)
    head = (f'<div class="card"><div class="pl-head" data-missing="{missing}">'
            f'<span>{"<b>" + str(missing) + "</b> étude(s) pas encore lue(s) par le coach" if missing else "Toutes les études sont lues."}</span>'
            f'<button type="button" class="go pl-run"{" hidden" if not missing else ""}>Préparer le plan</button>'
            '<button type="button" class="pl-stop" hidden>Arrêter</button><span class="pl-status"></span></div>'
            '<p class="note">Chaque étude est ouverte une fois pour lire ses stratégies (quelques secondes en pot 4bet, '
            'environ une minute en SRP sur 4 cœurs) ; une étude résolue à partir de maintenant est lue tout de suite.</p></div>')
    sections = "".join(f'<section class="pl-fam" data-fam="{p["family"]}" hidden>{_family(p)}</section>' for p in plans)
    suggestions = ["Résume-moi le plan de jeu en SRP", "Sur quels flops puis-je c-better toute ma range ?",
                   "Comment continuer à la turn après une c-bet payée ?"]
    suggestions += [f"Comment exploiter {v} ?" for v in villains[:1]]
    chat = (f'<details class="card pl-chat" open><summary><b>Discuter avec le coach</b> '
            '<span class="muted">· il lit tes études, ton plan de jeu et les écarts de tes adversaires</span></summary>'
            f'<div id="coach-box" data-suggestions="{escape(json.dumps(suggestions, ensure_ascii=False))}"></div></details>'
            '<link rel="stylesheet" href="/static/coach.css"><script src="/static/coach.js"></script>')
    body = f"""
<div class="meta">Un plan de jeu simple, du flop à la river, tiré des études résolues : ce que le solveur fait, regroupé
en règles qu'un humain peut appliquer.</div>
{head}
{chat}
<div class="pl-fams" role="group" aria-label="Type de pot">{tabs}</div>
{sections}
"""
    return html_page("Plan de jeu suggéré", f"<style>{STYLE}</style>{body}", embed, script=SCRIPT)
