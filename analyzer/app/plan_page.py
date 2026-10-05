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
.pl-grid { display: grid; grid-template-columns: minmax(90px, auto) repeat(3, minmax(170px, 1fr)); gap: 6px; min-width: 640px; }
.pl-gh, .pl-rh { font-weight: 600; font-size: 14px; display: flex; flex-direction: column; justify-content: center; }
.pl-gh small, .pl-rh small { font-weight: 400; font-size: 11px; color: var(--muted); }
.pl-gh { padding: 2px 4px; }
.pl-cell { display: flex; flex-direction: column; gap: 3px; padding: 8px 10px; border-radius: 8px; text-decoration: none;
  color: var(--ink); border: 1px solid var(--border); min-height: 74px; }
a.pl-cell:hover { border-color: var(--series-1); }
.pl-cell .lvl { font-weight: 700; font-size: 14px; }
.pl-cell.empty { background: var(--page); justify-content: center; font-size: 12px; }
.lvl-range { background: var(--hi-bg); }
.lvl-often { background: color-mix(in srgb, var(--hi-bg) 45%, transparent); }
.lvl-check { background: var(--lo-bg); }
.pl-ex .cards { margin-right: 6px; }
.pl-specials { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.pl-special { flex: 1 1 260px; display: grid; grid-template-columns: minmax(90px, auto) 1fr; gap: 6px; }
.pl-lvl { font-size: 12px; font-weight: 600; border-radius: 6px; padding: 1px 8px; }
.pl-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 8px; }
.pl-chip { font-size: 13px; border-radius: 999px; padding: 3px 10px; border: 1px solid var(--border); white-space: nowrap; }
.pl-chip b { font-weight: 600; margin-right: 4px; }
.pl-chip.up { background: var(--hi-bg); border-color: transparent; }
.pl-chip.down { background: var(--lo-bg); border-color: transparent; }
.pl-chip.mix { border-style: dashed; }
table.pl-matrix td { vertical-align: middle; }
table.pl-matrix .pl-chip { font-size: 12px; padding: 2px 8px; }
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
    return SIZE_TEXT.get(group.get("size") or "", "")


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
        cls = {"bet": "up", "check": "down"}.get(kind, "mix")
    name = f'<b>{escape(rule["label"])}</b> ' if label else ""
    return (f'<span class="pl-chip {cls}" title="{escape(rule["label"])} : {_pct(rule["share"])} de la range">'
            f'{name}{escape(text)}</span>')


def _chips(rules: list[dict], defender: bool = False) -> str:
    if not rules:
        return '<p class="muted">Pas assez de données.</p>'
    return '<div class="pl-chips">' + "".join(_chip(r, defender) for r in rules) + "</div>"


def _matrix(rows: list[dict], street: str, measure: str, defender: bool = False) -> str:
    """Selon la carte de turn ou de river : la fréquence de mise (ou de fold) et ce que fait chaque famille."""
    if not rows:
        return '<p class="muted">Pas encore de données.</p>'
    groups = [(k, label) for k, label, _, _ in coach.GROUPS if any(r["key"] == k for row in rows for r in row["rules"])]
    head = "".join(f"<th>{escape(label)}</th>" for _, label in groups)
    body = []
    for row in rows:
        by = {r["key"]: r for r in row["rules"]}
        cells = "".join(f"<td>{_chip(by[k], defender, label=False) if k in by else ''}</td>" for k, _ in groups)
        value = row["fold"] if defender else row["aggr"]
        body.append(f'<tr><td><b>{escape(row["label"])}</b><div class="small muted">{escape(row["text"])}</div></td>'
                    f'<td class="num">{_pct(value)}</td>{cells}</tr>')
    return (f'<div class="scroll"><table class="stats pl-matrix"><thead><tr><th>{escape(street)}</th>'
            f'<th class="num">{escape(measure)}</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def _details_rules(rules: list[tuple[str, str]]) -> str:
    if not rules:
        return '<p class="muted">Pas assez de données.</p>'
    return '<ul class="rules">' + "".join(f"<li><b>{escape(what)}</b> : {escape(who)}</li>" for what, who in rules) + "</ul>"


def _boards(rows: list[dict], links: bool = True) -> str:
    if not links:
        return '<span class="pl-ex">' + "".join(cards_html(r["board"]) for r in rows[:3]) + "</span>"
    return '<div class="pl-flops">' + "".join(
        f'<a href="/explorateur/{quote(r["id"], safe="")}" target="_blank" rel="noopener" '
        f'title="c-bet {_pct(r["cbet"])} · ouvrir dans l\'explorateur">{cards_html(r["board"])}</a>' for r in rows) + "</div>"


def _cell(plan: dict, category: str) -> str:
    g = next((g for g in plan["groups"] if g["category"] == category), None)
    label = coach.CATEGORY_LABEL[category]
    if g is None:
        return f'<div class="pl-cell empty"><span class="muted">{escape(label)} : pas encore de flop résolu</span></div>'
    size = f" · {escape(_size(g))}" if g.get("size") else ""
    return (f'<a class="pl-cell lvl-{g["pattern"]} pl-jump" href="#cat-{plan["family"]}-{category}">'
            f'<span class="lvl">{escape(g["level"])}</span><span class="small">c-bet {_pct(g["cbet"])}{size}</span>'
            f'{_boards(g["flops"], links=False)}</a>')


def _grid(plan: dict) -> str:
    head = '<div></div>' + "".join(f'<div class="pl-gh">{escape(label)}<small>{escape(text)}</small></div>'
                                   for _, label, text in coach.SHAPES)
    rows = "".join(f'<div class="pl-rh">{escape(label)}<small>{escape(text)}</small></div>'
                   + "".join(_cell(plan, f"{h}-{s}") for s, _, _ in coach.SHAPES) for h, label, text in coach.HEIGHTS)
    specials = "".join(f'<div class="pl-special"><div class="pl-gh">{escape(label)}<small>{escape(text)}</small></div>'
                       f'{_cell(plan, key)}</div>' for key, label, text in coach.SPECIALS)
    return f'<div class="scroll"><div class="pl-grid">{head}{rows}</div></div><div class="pl-specials">{specials}</div>'


def _group(plan: dict, g: dict) -> str:
    who, other = plan["who"], plan["other"]
    d = g["defense"]
    defense = []
    if d["vs_cbet"]:
        defense.append(f'<h3>Face à la c-bet : {escape(other)} folde {_pct(d["vs_cbet"]["fold"])}, '
                       f'relance {_pct(d["vs_cbet"]["raise"])}</h3>' + _chips(d["vs_cbet"]["rules"], defender=True))
    if d["vs_barrel"]:
        defense.append("<h3>Face au 2e barrel, selon la turn</h3>" + _matrix(d["vs_barrel"], "Turn", "Folde", True))
    if d["vs_barrel3"]:
        defense.append("<h3>Face au 3e barrel, selon la river</h3>" + _matrix(d["vs_barrel3"], "River", "Folde", True))
    others = []
    if g["delayed"]:
        others.append(f"<h3>Flop checké par {escape(who)} : c-bet retardée à la turn</h3>"
                      + _matrix(g["delayed"], "Turn", "Mise"))
    if d["probe"]:
        others.append(f"<h3>Flop checké : {escape(other)} mène à la turn (probe)</h3>" + _matrix(d["probe"], "Turn", "Mise"))
    if d["stab"]:
        others.append(f'<h3>Quand {escape(who)} checke : {escape(other)} mise {_pct(d["stab"]["aggr"])}</h3>'
                      + _chips(d["stab"]["rules"]))
    if d["vs_xr"]:
        others.append(f'<h3>{escape(_cap(who))} face à la relance : folde {_pct(d["vs_xr"]["fold"])}, '
                      f'sur-relance {_pct(d["vs_xr"]["raise"])}</h3>' + _chips(d["vs_xr"]["rules"], defender=True))

    def sub(title: str, content: str) -> str:
        return f'<details class="pl-sub"><summary>{escape(title)}</summary><div>{content}</div></details>'

    size = f" en {escape(_size(g))}" if g.get("size") else ""
    summary = (f'{escape(g["label"])} <span class="pl-lvl lvl-{g["pattern"]}">{escape(g["level"])}</span>'
               f'<span class="sub">c-bet {_pct(g["cbet"])}{size} · {len(g["flops"])} flop(s)</span>')
    return f"""
<details class="pl-group" id="cat-{plan["family"]}-{g["category"]}"><summary>{summary}</summary><div class="pl-body">
{_boards(g["flops"])}
<div class="pl-why">{escape(g["why"])}</div>
<h3>Au flop, {escape(who)} c-bette {_pct(g["cbet"])}{size}</h3>
{_chips(g["flop"])}
<h3>À la turn, si la c-bet est payée</h3>
{_matrix(g["turn"], "Turn", "Continue")}
<h3>À la river, après deux mises payées</h3>
{_matrix(g["river"], "River", "Mise")}
{sub("En face : " + other, "".join(defense) or '<p class="muted">Pas encore de données.</p>')}
{sub("Autres lignes", "".join(others) or '<p class="muted">Pas encore de données.</p>')}
{sub("Détail par famille de mains (onze familles)", _details_rules(g["flop_detail"]))}
</div></details>"""


def _table(plan: dict) -> str:
    rows = "".join(
        f'<tr><td><a href="/explorateur/{quote(r["id"], safe="")}" target="_blank" rel="noopener">{cards_html(r["board"])}</a></td>'
        f'<td>{escape(coach.CATEGORY_LABEL[r["category"]])}</td>'
        f'<td>{escape(coach.PATTERN_LABEL[r["pattern"]])}</td><td class="num">{_pct(r["cbet"])}</td>'
        f'<td>{escape(SIZE_TEXT.get(r["size"] or "", "–"))}</td>'
        f'<td class="num">{coach._pts(r["eq_adv"])}</td><td class="num">{coach._pts(r["nut_adv"])}</td></tr>'
        for r in sorted(plan["flops"], key=lambda r: [c for c, _ in coach.CATEGORIES].index(r["category"])))
    return ('<div class="scroll"><table class="stats"><thead><tr><th>Flop</th><th>Catégorie</th><th>Niveau</th>'
            '<th class="num">C-bet</th><th>Taille</th><th class="num">Équité</th>'
            f'<th class="num">Nuts</th></tr></thead><tbody>{rows}</tbody></table></div>')


def _legend() -> str:
    groups = " ; ".join(f"<b>{escape(label)}</b> : {escape(text)}" for _, label, _, text in coach.GROUPS)
    return (f'<p class="note">Niveaux : <b>mise presque tout</b> (c-bet d\'au moins 75 %, en général petite), '
            f'<b>mise souvent</b> (50 à 75 %), <b>checke souvent</b> (moins de 50 %). Les mains en quatre familles : {groups}. '
            '« Mélange » : le solveur joue les deux selon la main exacte ; le pourcentage est sa fréquence de mise.</p>')


def _family(plan: dict) -> str:
    if not plan["count"]:
        hint = (f"{plan['missing']} étude(s) attendent leur lecture : clique sur <b>Préparer le plan</b>."
                if plan["missing"] else "Résous des flops de cette série (onglet de la série, ou l'explorateur) : le plan se "
                "construit à partir des études.")
        return f'<div class="card"><p class="muted">Pas encore de plan pour les {escape(plan["name"])}. {hint}</p></div>'
    total = len(studyspots.flop_set(plan["family"]))
    return f"""
<p class="note">{escape(plan["label"])}. D'après <b>{plan["count"]}</b> flop(s) résolu(s) sur {total} dans la série ; plus
il y en a, plus le plan est précis. {escape(_cap(plan["who"]))} a l'initiative.</p>
<h2>Selon le flop</h2>
<div class="card">{_grid(plan)}{_legend()}</div>
<h2>Catégorie par catégorie</h2>
{"".join(_group(plan, g) for g in plan["groups"])}
<h2>Les flops résolus</h2>
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
    suggestions += [q.format(v=v) for v in villains[:1] for q in ("Comment exploiter {v} ?",
                                                                    "Dans quelles lignes {v} bluffe-t-il ?")]
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
