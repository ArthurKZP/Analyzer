"""Page « Études du solveur » : les spots d'étude (SRP par texture de flop) et les coups résolus avec GTOpen,
gardés sur disque pour être réexplorés."""
from __future__ import annotations

from html import escape
from urllib.parse import quote

from ..report import cards_html, html_page, num
from ..theory import postflop, sizing, studyspots

STYLE = """
.studies td.actions { white-space: nowrap; text-align: right; }
.studies a.open { font-weight: 600; text-decoration: none; }
.studies button.del { background: none; border: 1px solid var(--border, rgba(0,0,0,0.1)); border-radius: 6px; padding: 2px 8px;
  cursor: pointer; color: var(--muted, #898781); margin-left: 8px; font: inherit; font-size: 12px; }
.studies button.del:hover { color: var(--loss, #d03b3b); border-color: currentColor; }
.spots td { vertical-align: middle; }
.spots td.flop { white-space: nowrap; }
.spots td.bars { min-width: 170px; }
.spots tr.tex th { font-size: 14px; font-weight: 600; color: var(--ink); padding-top: 18px; }
.spots tr.tex th span { font-weight: 400; color: var(--muted); font-size: 12px; margin-left: 6px; }
.spots tr.avg td { background: var(--hi-bg, rgba(0,0,0,0.04)); }
.spots tr.avg td.flop { font-weight: 600; font-size: 12px; }
.spots tr.todo td { color: var(--muted); }
.spots .ref { font-size: 10px; color: var(--muted); border: 1px solid var(--border); border-radius: 4px; padding: 0 4px;
  margin-left: 4px; cursor: help; }
.sbar { display: flex; height: 12px; border-radius: 3px; overflow: hidden; background: var(--grid); }
.sbar span { display: block; height: 100%; }
.stxt { font-size: 11px; color: var(--ink-2); margin-top: 2px; white-space: nowrap; }
.k-fold { background: #2a78d6; } .k-pass { background: #1baf7a; } .k-bet { background: #eb6834; }
.k-raise { background: #c4441c; } .k-allin { background: #4a3aa7; }
.spot-head { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin: 4px 0 6px; }
.spot-head button, .spot-other button { font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.spot-head button.go { background: var(--series-1); border-color: var(--series-1); color: #fff; font-weight: 600; }
.spot-head button:disabled { opacity: .5; cursor: default; }
.spot-status { font-size: 12px; color: var(--ink-2); }
.spot-status .bar { display: inline-block; vertical-align: middle; width: 120px; height: 6px; border-radius: 3px;
  background: var(--grid); overflow: hidden; margin-left: 6px; }
.spot-status .bar span { display: block; height: 100%; background: var(--series-1); }
.spot-other { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 12px; font-size: 13px; }
.spot-other input { font: inherit; font-size: 13px; padding: 4px 8px; width: 9em; border-radius: 6px;
  border: 1px solid var(--border); background: var(--page); color: var(--ink); }
.spots tr.sizes td { border-top: none; padding-top: 0; font-size: 12px; color: var(--ink-2); }
.spots tr.sizes summary { cursor: pointer; }
.spots tr.sizes table { border-collapse: collapse; margin: 6px 0 4px; font-size: 12px; }
.spots tr.sizes table td, .spots tr.sizes table th { padding: 2px 10px 2px 0; border: none; text-align: left; vertical-align: top; }
.spots tr.sizes table th { color: var(--muted); font-weight: 500; }
.spots tr.sizes .opt b { color: var(--ink); }
.stale { margin-top: 14px; font-size: 13px; }
.stale ul { margin: 6px 0 0; padding-left: 18px; }
.spot-legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--muted); margin: 8px 0 0; }
.spot-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
"""

SCRIPT = """
function post(url, body) {
  return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
    .then(function (r) { return r.json(); });
}
document.querySelectorAll('button.del').forEach(function (b) {
  b.addEventListener('click', function () {
    if (!confirm('Supprimer cette étude ? Il faudra la résoudre à nouveau pour l\\'explorer.')) return;
    post('/api/etudes/supprimer', { key: b.dataset.key }).then(function () { location.reload(); });
  });
});
(function () {
  var box = document.getElementById('spot-head');
  if (!box) return;
  var family = box.dataset.family, run = document.getElementById('spot-run'), stop = document.getElementById('spot-stop');
  var status = document.getElementById('spot-status'), done = Number(box.dataset.done), timer = null;
  function show(s) {
    if (s.done !== done) { location.reload(); return; }
    var current = s.rows.filter(function (r) { return r.state === 'running'; })[0];
    var waiting = s.rows.filter(function (r) { return r.state === 'waiting'; }).length;
    status.textContent = '';
    stop.hidden = !s.busy;
    run.disabled = !s.ready;
    if (!s.ready) { status.append('Installe d\\'abord le solveur : python -m analyzer gtopen --installer'); return; }
    run.hidden = !!s.busy || s.done === s.total;
    if (current && current.mode === 'choose') {
      status.append('Choix des tailles de ' + current.board.join('') + ' (' + current.texture + ') : '
        + ((current.progress || {}).stage || 'démarrage') + (waiting ? ' · ' + waiting + ' en attente' : ''));
    } else if (current) {
      var p = current.progress || {}, frac = p.iteration ? Math.min(1, p.iteration / current.max_iterations) : 0;
      status.append('En cours : ' + current.board.join('') + ' (' + current.texture + ')'
        + (p.iteration ? ', itération ' + p.iteration + ' / ' + current.max_iterations : ', construction de l\\'arbre')
        + (waiting ? ' · ' + waiting + ' en attente' : ''));
      var bar = document.createElement('span'), fill = document.createElement('span');
      bar.className = 'bar'; fill.style.width = (100 * frac).toFixed(1) + '%'; bar.append(fill); status.append(bar);
    } else if (waiting) status.append(waiting + ' flop(s) en attente (une autre résolution passe d\\'abord)');
    clearTimeout(timer);
    if (s.busy) timer = setTimeout(refresh, 3000);
  }
  function refresh() { fetch('/api/spots/' + family).then(function (r) { return r.json(); }).then(show); }
  run.addEventListener('click', function () {
    run.disabled = true;
    post('/api/spots/' + family + '/resoudre').then(show);
  });
  stop.addEventListener('click', function () { post('/api/spots/' + family + '/arreter').then(show); });
  refresh();  // résolutions déjà lancées, solveur installé ou non
  var form = document.getElementById('spot-other');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var raw = form.elements.flop.value.replace(/[\\s,]/g, ''), cards = raw.match(/[2-9tjqka][cdhs]/gi) || [];
    var board = cards.map(function (c) { return c[0].toUpperCase() + c[1].toLowerCase(); });
    var msg = document.getElementById('spot-other-msg');
    if (cards.join('').length !== raw.length || board.length !== 3 || new Set(board).size !== 3) {
      msg.textContent = 'Trois cartes différentes, par exemple Ah7d2c.';
      return;
    }
    msg.textContent = '';
    window.open('/explorateur/spot:' + family + ':' + board.join(''), '_blank', 'noopener');
  });
})();
"""

KIND_CLASS = {"fold": "k-fold", "check": "k-pass", "call": "k-pass", "bet": "k-bet", "raise": "k-raise"}
SHORT = {"check": "check", "call": "call", "fold": "fold", "bet": "mise", "raise": "relance"}


def _size(n: int) -> str:
    return f"{num(n / 1e6, 0)} Mo" if n < 1e9 else f"{num(n / 1e9, 1)} Go"


def _short(actions: list[dict]) -> list[str]:
    """Libellé court de chaque action ; le libellé complet si plusieurs actions sont de la même sorte."""
    kinds = [(a["kind"], bool(a.get("allin"))) for a in actions]
    return ["tapis" if a.get("allin") else a["label"] if kinds.count((a["kind"], False)) > 1
            else SHORT.get(a["kind"], a["kind"]) for a in actions]


def _bar(actions: list[dict], freqs: list[float]) -> str:
    segments = "".join(
        f'<span class="{"k-allin" if a.get("allin") else KIND_CLASS.get(a["kind"], "k-bet")}" '
        f'style="width:{100 * f:.1f}%" title="{escape(a["label"])} : {round(100 * f)} %"></span>'
        for a, f in zip(actions, freqs) if f > 0)
    text = " · ".join(f"{escape(label)} {round(100 * f)} %" for label, f in zip(_short(actions), freqs) if f >= 0.005)
    return f'<div class="sbar">{segments}</div><div class="stxt">{text}</div>'


def _average(entries: list[dict]) -> tuple[list[dict], list[float]]:
    """Moyenne des fréquences de plusieurs flops, action par action (même libellé)."""
    actions: dict[str, dict] = {}
    totals: dict[str, float] = {}
    for e in entries:
        for a, f in zip(e["actions"], e["freqs"]):
            actions.setdefault(a["label"], a)
            totals[a["label"]] = totals.get(a["label"], 0.0) + f
    return list(actions.values()), [totals[k] / len(entries) for k in actions]


def _columns(metas: list[dict]) -> list[str]:
    titles = {e["title"] for m in metas for e in m.get("summary", [])}
    order = ["BB au flop"] + [title for _, title in studyspots.SUMMARY_STEPS]
    return [t for t in order if t in titles] or [title for _, title in studyspots.SUMMARY_STEPS]


def _sizes_row(spot, width: int, series: bool) -> str:
    """Sous la ligne d'un flop : ses tailles et, dépliable, le détail des comparaisons."""
    chosen = studyspots.load_selection(spot.family, "".join(spot.board)) if spot.plan else None
    if not chosen:
        later = " (pas encore choisies : elles le seront avant la résolution)" if series else ""
        return f'<tr class="sizes"><td></td><td colspan="{width - 1}">Tailles par défaut{later}.</td></tr>'

    report = chosen.get("report", {})
    order = {k: i for i, k in enumerate(s.key for s in sizing.situations())}
    shown = sorted((k for k, e in report.items() if e.get("evs")),
                   key=lambda k: (sizing.Situation(k, []).street, -report[k].get("reach", 0), order.get(k, 0)))
    lines = []
    for key in shown:
        e = report[key]
        opts = " · ".join(
            (f"<b>{escape(sizing.sizes_text(o))} {num(ev, 3)}</b>" if o == e["chosen"] else
             f"{escape(sizing.sizes_text(o))} {num(ev, 3)}") for o, ev in zip(e["options"], e["evs"]))
        reach = "flop" if e.get("method") == "arbre complet" else f'{num(100 * e.get("reach", 0), 1)} %'
        lines.append(f'<tr><td>{escape(e["label"])}</td><td>{reach}</td><td class="opt">{opts}</td></tr>')
    rare = sum(1 for e in report.values() if e.get("method") == "rare")
    source = "livrées avec Analyzer" if chosen.get("source") == "livré" else f'choisies le {escape(chosen.get("created", ""))}'
    detail = (f'<table><thead><tr><th>Situation</th><th>Atteinte</th><th>EV de celui qui mise (bb), par taille'
              f'</th></tr></thead><tbody>{"".join(lines)}</tbody></table>'
              f'<div class="muted">{len(shown)} situations comparées ({source}) ; {rare} situation(s) presque jamais '
              'atteinte(s) : la plus petite taille. EV au début du flop (arbre complet) ou moyenne au début de la '
              'turn (sous-jeux) ; à moins de 0,02 bb de la meilleure, la plus petite taille l\'emporte.</div>')
    return (f'<tr class="sizes"><td></td><td colspan="{width - 1}"><details><summary>Tailles : '
            f'{escape(sizing.plan_text(spot.plan))}</summary>{detail}</details></td></tr>')


def _stale_section() -> str:
    stale = studyspots.stale_spot_studies()
    if not stale:
        return ""
    items = "".join(
        f'<li>{cards_html(m.get("board", []))} · {escape(m.get("created", ""))} · {_size(m["size"])}'
        f'<button type="button" class="del" data-key="{escape(m["key"])}">Supprimer</button></li>' for m in stale)
    return (f'<div class="stale"><b>Anciennes études de spots</b> ({len(stale)}, {_size(sum(m["size"] for m in stale))}) '
            f': faites avec un autre arbre (avant le choix des tailles), elles ne s\'ouvrent plus.<ul>{items}</ul></div>')


def _spot_section(family: str = "srp") -> str:
    info = studyspots.FAMILIES[family]
    studies = studyspots.spot_studies()
    boards = studyspots.family_boards(family)
    spots = [studyspots.StudySpot(family, studyspots.cards_of(b)) for b in boards]
    metas = {s.ident: studies[s.ident] for s in spots if s.ident in studies and studies[s.ident].get("summary")}
    series = set(studyspots.flop_set(family))
    refs = {i: r for i, r in studyspots.reference(family).items() if i not in metas}
    shown = {**refs, **metas}  # une étude de cet ordinateur passe avant la référence
    columns = _columns(list(shown.values()))
    width = len(columns) + 3
    rows = []
    for texture in studyspots.TEXTURES:
        group = [s for s in spots if s.texture == texture]
        if not group:
            continue
        known = [shown[s.ident] for s in group if s.ident in shown]
        solved = sum(s.ident in metas for s in group)
        rows.append(f'<tr class="tex"><th colspan="{width}">{escape(texture)}'
                    f'<span>{solved} / {len(group)} flop(s) résolu(s) ici</span></th></tr>')
        if len(known) > 1:
            cells = []
            for title in columns:
                entries = [e for m in known for e in m["summary"] if e["title"] == title]
                cells.append(f'<td class="bars">{_bar(*_average(entries)) if entries else ""}</td>')
            rows.append(f'<tr class="avg"><td class="flop">Moyenne ({len(known)})</td>{"".join(cells)}'
                        '<td></td><td></td></tr>')
        for spot in group:
            link = (f'<a class="open" href="/explorateur/{quote(spot.ident, safe=":")}" target="_blank" '
                    f'rel="noopener">Explorer ↗</a>')
            data = shown.get(spot.ident)
            if data is None:
                rows.append(f'<tr class="todo"><td class="flop">{cards_html(spot.board)}</td>'
                            f'<td colspan="{len(columns)}">pas encore résolu</td><td></td>'
                            f'<td class="actions">{link}</td></tr>')
                rows.append(_sizes_row(spot, width, ''.join(spot.board) in series))
                continue
            by_title = {e["title"]: e for e in data["summary"]}
            cells = "".join(f'<td class="bars">{_bar(by_title[t]["actions"], by_title[t]["freqs"]) if t in by_title else ""}'
                            f'</td>' for t in columns)
            exploit = data.get("exploit_pct")
            local = spot.ident in metas
            tag = "" if local else (' <span class="ref" title="Synthèse de référence livrée avec Analyzer : résous ce '
                                    'flop pour l\'explorer">réf.</span>')
            delete = (f'<button type="button" class="del" data-key="{escape(data["key"])}">Supprimer</button>'
                      if local else "")
            rows.append(
                f'<tr><td class="flop">{cards_html(spot.board)}{tag}</td>{cells}'
                f'<td class="num">{num(exploit, 2) + " %" if exploit is not None else "–"}</td>'
                f'<td class="actions">{link}{delete}</td></tr>')
            rows.append(_sizes_row(spot, width, ''.join(spot.board) in series))
    head = "".join(f"<th>{escape(t)}</th>" for t in columns)
    done, total = len(metas), len(spots)
    size = sum(m["size"] for m in metas.values())
    missing = total - done
    legend = "".join(f'<span><i class="{c}"></i>{label}</span>' for c, label in
                     (("k-pass", "check / call"), ("k-bet", "mise"), ("k-raise", "relance"), ("k-fold", "fold"),
                      ("k-allin", "tapis")))
    return f"""
<h2>Spots d'étude · {escape(info["name"])}</h2>
<div class="card">
<p class="note" style="margin-top:0">{escape(info["label"])}. Ranges de la solution préflop ; la BB ne mène pas
(pas de donk). Trois flops par texture : pairé, monotone, puis selon la plus haute carte. Les tailles de mise
sont choisies flop par flop, une par situation (deux à la river) : c-bet 33 / 75 % / géo, 2e barrel 50 % / pot
/ géo, c-bet retardée 33 / 66 % / géo, probe turn 33 / 75 % / pot / géo, river deux parmi 50 / 75 % / pot /
150 % / tapis, relances 33 / 66 % / géo (tapis à la river) ; la meilleure EV pour celui qui mise l'emporte.
Chaque ligne donne la stratégie de toute la range : la c-bet du bouton après le check de la BB, la réponse de
la BB, puis celle du bouton face au check-raise. <b>Explorer ↗</b> ouvre le spot dans l'explorateur (turn et
river comprises).{" Les flops marqués <b>réf.</b> montrent la synthèse livrée avec Analyzer (même arbre, calculée "
"à l'avance) : résous-les ici pour les explorer." if refs else ""}</p>
<div class="spot-head" id="spot-head" data-family="{escape(family)}" data-done="{done}">
  <span><b>{done} / {total}</b> flops résolus{" · " + _size(size) if size else ""}</span>
  <button type="button" class="go" id="spot-run"{" hidden" if not missing else ""}>Résoudre les {missing} flops manquants</button>
  <button type="button" id="spot-stop" hidden>Arrêter</button>
  <span class="spot-status" id="spot-status"></span>
</div>
<p class="note">Un flop sans tailles choisies passe d'abord par leur choix (de l'ordre de 45 minutes sur 4 cœurs,
moins avec plus de cœurs), puis par sa résolution (quelques minutes, 50 à 150 Mo sur le disque). Les flops se
traitent l'un après l'autre en arrière-plan, tant que l'application reste ouverte ; tu peux fermer cette page. En ligne de commande : <code>python -m analyzer gtopen --spots {escape(family)}</code>.</p>
<div class="scroll"><table class="stats studies spots"><thead><tr><th>Flop</th>{head}<th class="num">Précision</th>
<th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<div class="spot-legend">{legend}</div>
<form class="spot-other" id="spot-other"><label for="spot-flop">Étudier un autre flop :</label>
<input id="spot-flop" name="flop" placeholder="ex. Ah7d2c" autocomplete="off" spellcheck="false">
<button type="submit">Ouvrir ↗</button><span class="muted" id="spot-other-msg"></span></form>
{_stale_section()}
</div>
"""


def _hand_section() -> str:
    studies = [s for s in postflop.list_studies() if s.get("kind") != "spot"]
    rows = []
    for s in studies:
        exploit = s.get("exploit_pct")
        rows.append(
            "<tr>"
            f"<td>{escape(s['date'])}</td><td>{escape(s['villain'])}</td>"
            f"<td>{cards_html(s.get('hero_cards', []))}</td><td>{cards_html(s['board'])}</td>"
            f"<td>{escape(s['pot_type'])}</td><td>{'BB' if s['hero_position'] == 'BB' else 'Bouton'}</td>"
            f'<td class="num">{num(s["net"], 1, sign=True)} bb</td>'
            f'<td class="num">{num(exploit, 2) + " %" if exploit is not None else "–"}</td>'
            f'<td class="num">{_size(s["size"])}</td>'
            f'<td class="actions"><a class="open" href="/explorateur/{quote(s["hand"], safe="")}" target="_blank" '
            f'rel="noopener">Ouvrir ↗</a><button type="button" class="del" data-key="{escape(s["key"])}">Supprimer</button></td>'
            "</tr>"
        )
    if rows:
        table = ('<div class="scroll"><table class="stats studies"><thead><tr><th>Coup</th><th>Adversaire</th>'
                 '<th>Ta main</th><th>Board</th><th>Pot</th><th>Position</th><th class="num">Résultat</th>'
                 '<th class="num">Précision</th><th class="num">Taille</th><th></th></tr></thead><tbody>'
                 + "".join(rows) + "</tbody></table></div>")
    else:
        table = ('<p class="muted">Aucun coup résolu pour l\'instant. Ouvre un coup dans <b>Spots</b> et clique sur '
                 '<b>Résoudre ce coup</b> : la résolution est gardée ici.</p>')
    return f"""
<h2>Coups joués</h2>
<div class="card"><p class="note" style="margin-top:0">Chaque coup résolu est gardé : l'explorateur le rouvre en
quelques secondes, sans recalculer.</p>{table}</div>
"""


def build_studies_page(embed: bool = True) -> str:
    studies = postflop.list_studies()
    total = sum(s["size"] for s in studies)
    body = f"""
<div class="meta">{len(studies)} étude(s) · {_size(total)} sur le disque · {escape(str(postflop.studies_dir()))}</div>
<p class="note">Précision : exploitabilité de la solution, en % du pot (plus c'est bas, plus elle est proche de
l'équilibre).</p>
{_spot_section("srp")}
{_hand_section()}
"""
    return html_page("Études du solveur", f"<style>{STYLE}</style>{body}", embed, script=SCRIPT)
