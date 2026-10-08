"""« Mon jeu » : ton bilan contre tous tes adversaires."""
from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Optional
from urllib.parse import quote

from .insights import SECTIONS, Finding, findings
from .leaks import street_stake
from .lines import hero_fold_holdings
from .models import Hand
from .players import KINDS
from .report import SCRIPT, chart_svg, findings_html, html_page, legend, nonshowdown_html, num, pct_cell, tiles
from .stats import PlayerStats, allin_ev

TOP_FINDINGS = 5  # écarts mis en avant ; les autres viennent ensuite, repliés
# Stats qui disent la même chose, la plus parlante d'abord : une seule prend place parmi les premiers écarts.
SAME = (("sb_first.raise", "sb_first.fold", "vpip_sb"), ("bb_vs_open.fold", "vpip_bb"))


def opponent_results(hands: list[Hand], hero: str) -> list[dict]:
    """Résultat contre chaque adversaire, du plus joué au moins joué."""
    rows: dict[str, dict] = defaultdict(lambda: {"hands": 0, "net_bb": 0.0, "net": 0.0, "ev_bb": 0.0,
                                                 "first": None, "last": None})
    for h in hands:
        if hero not in h.seats or len(h.seats) != 2 or not h.bb:
            continue
        opp = h.opponent_of(hero)
        row = rows[opp]
        net = h.net(hero)
        ev = allin_ev(h)
        row["hands"] += 1
        row["net"] += net
        row["net_bb"] += net / h.bb
        row["ev_bb"] += (ev[hero] if ev else net) / h.bb
        row["first"] = min(row["first"] or h.date, h.date)
        row["last"] = max(row["last"] or h.date, h.date)
    out = []
    for name, row in rows.items():
        row["name"] = name
        row["bb100"] = 100 * row["net_bb"] / row["hands"] if row["hands"] else 0.0
        out.append(row)
    return sorted(out, key=lambda r: (-r["hands"], r["name"]))


def by_kind_html(results: list[dict], kinds: dict) -> str:
    """Résultats contre les réguliers et contre les récréatifs (rien si un seul type est présent)."""
    groups: dict[str, list[dict]] = {}
    for r in results:
        groups.setdefault(kinds.get(r["name"], {}).get("kind", "reg"), []).append(r)
    if len(groups) < 2:
        return ""
    cells = []
    for kind in ("reg", "rec"):
        rows = groups.get(kind, [])
        n = sum(r["hands"] for r in rows)
        net = sum(r["net_bb"] for r in rows)
        cells.append(f'<div class="tile"><div class="label">Contre les {KINDS[kind].lower()}s</div>'
                     f'<div class="value">{num(100 * net / n, 1, sign=True) if n else "–"} bb/100</div>'
                     f'<div class="sub">{n} mains · {len(rows)} joueur(s) · {num(net, 1, sign=True)} bb</div></div>')
    return f'<div class="tiles">{"".join(cells)}</div>'


# --- Liste des adversaires : recherche, type, tri ------------------------------------------------------------------
# Un tableau class="opp-table" dans un bloc class="opp-box" (lignes data-name, data-kind, data-hands, data-net,
# data-bb100, data-last), avec la barre opponent_tools() ; OPP_SCRIPT filtre et trie dans la page, et garde le tri et
# le type choisis (navigateur).

OPP_STYLE = """
.opp-tools { display: flex; flex-wrap: wrap; gap: 8px 10px; align-items: center; margin: 0 0 10px; }
.opp-tools input { font: inherit; font-size: 13px; padding: 5px 9px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--page); color: var(--ink); flex: 1 1 180px; max-width: 300px; min-width: 0; }
.opp-tools select, .opp-tools button { font: inherit; font-size: 13px; padding: 4px 8px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.opp-tools button { cursor: pointer; min-width: 34px; }
.opp-tools .opp-count { font-size: 12px; color: var(--muted); margin-left: auto; }
.opp-tools ~ .scroll th[data-sort] { cursor: pointer; user-select: none; white-space: nowrap; }
.opp-tools ~ .scroll th[data-sort]:hover { color: var(--ink); }
table.opp-table th.sorted { color: var(--ink); }
table.opp-table th.sorted::after { content: " ▼"; font-size: 9px; }
table.opp-table th.sorted.asc::after { content: " ▲"; }
"""

OPP_SCRIPT = """
document.querySelectorAll('.opp-box').forEach(function (box) {
  var body = box.querySelector('table.opp-table tbody'), rows = Array.prototype.slice.call(body.rows);
  var q = box.querySelector('.opp-q'), kind = box.querySelector('.opp-kind'), sort = box.querySelector('.opp-sort');
  var flip = box.querySelector('.opp-dir'), count = box.querySelector('.opp-count'), heads = box.querySelectorAll('th[data-sort]');
  if (!sort) return;  // un seul adversaire : pas de barre
  var key = 'analyzer-adversaires', saved = {};
  try { saved = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (e) { saved = {}; }
  var state = { sort: saved.sort || 'hands', asc: !!saved.asc, kind: saved.kind || '' };
  if (!sort.querySelector('option[value="' + state.sort + '"]')) state = { sort: 'hands', asc: false, kind: state.kind };
  sort.value = state.sort;
  if (kind) kind.value = state.kind;
  function plain(s) { return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }  // sans accents
  function val(row, k) { return k === 'name' ? plain(row.dataset.name) : k === 'last' ? row.dataset.last : Number(row.dataset[k]); }
  function apply() {
    var text = plain(q.value.trim()), k = state.sort, dir = state.asc ? 1 : -1, shown = 0;
    if (k === 'name') dir = -dir;  // le nom : de A à Z d'abord
    rows.sort(function (a, b) {
      var x = val(a, k), y = val(b, k);
      return (x < y ? -1 : x > y ? 1 : 0) * dir || Number(b.dataset.hands) - Number(a.dataset.hands);
    });
    rows.forEach(function (row) {
      var ok = (!text || plain(row.dataset.name).indexOf(text) >= 0) && (!state.kind || row.dataset.kind === state.kind);
      row.hidden = !ok;
      if (ok) shown++;
      body.appendChild(row);
    });
    count.textContent = shown === rows.length ? rows.length + ' adversaire(s)' : shown + ' sur ' + rows.length + ' adversaire(s)';
    heads.forEach(function (th) {
      th.classList.toggle('sorted', th.dataset.sort === k);
      th.classList.toggle('asc', th.dataset.sort === k && (k === 'name' ? !state.asc : state.asc));
    });
    flip.textContent = (k === 'name' ? !state.asc : state.asc) ? '▲' : '▼';
    try { localStorage.setItem(key, JSON.stringify({ sort: state.sort, asc: state.asc, kind: state.kind })); } catch (e) {}
  }
  q.addEventListener('input', apply);
  if (kind) kind.addEventListener('change', function () { state.kind = kind.value; apply(); });
  sort.addEventListener('change', function () { state.sort = sort.value; state.asc = false; apply(); });
  flip.addEventListener('click', function () { state.asc = !state.asc; apply(); });
  heads.forEach(function (th) {
    th.addEventListener('click', function () {
      if (state.sort === th.dataset.sort) state.asc = !state.asc;
      else { state.sort = th.dataset.sort; state.asc = false; }
      sort.value = state.sort;
      apply();
    });
  });
  apply();
});
"""

SORTS = (("hands", "Mains jouées"), ("net", "Résultat (bb)"), ("bb100", "bb/100"), ("last", "Dernière main"),
         ("name", "Nom"))


def opponent_tools(with_kind: bool = True) -> str:
    """La barre au-dessus d'une liste d'adversaires : recherche par nom, type, tri (et son sens)."""
    kinds = ('<select class="opp-kind" aria-label="Type d\'adversaire"><option value="">Tous les types</option>'
             '<option value="reg">Réguliers</option><option value="rec">Récréatifs</option></select>') if with_kind else ""
    sorts = "".join(f'<option value="{k}">{escape(label)}</option>' for k, label in SORTS)
    return ('<div class="opp-tools"><input class="opp-q" type="search" placeholder="Rechercher un adversaire" '
            f'aria-label="Rechercher un adversaire">{kinds}<label class="small">Trier par <select class="opp-sort">'
            f'{sorts}</select></label><button type="button" class="opp-dir" title="Inverser l\'ordre" '
            'aria-label="Inverser l\'ordre">▼</button><span class="opp-count"></span></div>')


def opponent_row_data(name: str, kind: str, hands: int, net_bb: float, bb100: float, last) -> str:
    """Les attributs d'une ligne de la liste, pour la recherche et le tri (last : date de sa dernière main, ou
    « jj/mm/aaaa »)."""
    when = "-".join(reversed(last.split("/"))) if isinstance(last, str) else f"{last:%Y-%m-%d %H:%M}"
    return (f'data-name="{escape(name)}" data-kind="{escape(kind)}" data-hands="{hands}" data-net="{net_bb:.2f}" '
            f'data-bb100="{bb100:.2f}" data-last="{escape(when)}"')


def opponent_link(name: str) -> str:
    """Lien vers la fiche d'un adversaire dans l'application."""
    return f'<a href="/#/adversaire/{quote(name, safe="")}" target="_top">{escape(name)}</a>'


def finding_weight(f: Finding, hands: int) -> float:
    """Ce que pèse un écart : les points hors du repère × la fréquence de la situation × ce que la décision met en jeu
    (le pot grossit à chaque street, et dans les pots 3bet ou 4bet)."""
    lo, hi = f.stat.ref
    gap = f.ratio.pct - hi if f.direction == "haut" else lo - f.ratio.pct
    stake = street_stake(f.stat.key) * (2.0 if "3bet" in f.stat.key or "4bet" in f.stat.key else 1.0)
    return gap * f.ratio.opps / max(hands, 1) * stake


def priorities(ps: PlayerStats, hands: int) -> list[Finding]:
    """Les écarts aux repères, les plus importants d'abord : les nets (le hasard ne les explique pas), puis du plus
    lourd au plus léger (finding_weight). Deux stats qui disent la même chose (l'open et le fold d'entrée) ne prennent
    pas deux places parmi les premiers : la plus parlante y va, l'autre passe après."""
    items = sorted(findings(ps), key=lambda f: (not f.strong, -finding_weight(f, hands)))
    by_key = {f.stat.key: f for f in items}
    top, rest, seen, placed = [], [], set(), set()
    for f in items:
        if f.stat.key in placed:
            continue
        group = next((k for k, keys in enumerate(SAME) if f.stat.key in keys), None)
        if len(top) < TOP_FINDINGS and (group is None or group not in seen):
            if group is not None:
                seen.add(group)
                best = next(by_key[k] for k in SAME[group] if k in by_key)
                if best is not f:
                    rest.append(f)
                    placed.add(f.stat.key)
                    f = best
            top.append(f)
        else:
            rest.append(f)
        placed.add(f.stat.key)
    return top + rest


def priorities_html(ps: PlayerStats, hands: int, folds: Optional[dict] = None) -> str:
    items = priorities(ps, hands)
    if not items:
        return '<p class="muted">Aucun écart marqué par rapport aux repères (ou échantillon trop faible).</p>'
    top, rest = items[:TOP_FINDINGS], items[TOP_FINDINGS:]
    out = findings_html(ps, hero_mode=True, folds=folds, items=top)
    if rest:
        out += (f'<details class="inner"><summary>Les autres écarts ({len(rest)})</summary><div class="inner-body">'
                f'{findings_html(ps, hero_mode=True, folds=folds, items=rest)}</div></details>')
    return out


def self_stat_tables(ps: PlayerStats) -> str:
    out = []
    for title, defs in SECTIONS:
        rows = []
        for d in defs:
            r = ps.r(d.key)
            if not r.opps:
                continue
            ref = f"{d.ref[0]:.0f}–{d.ref[1]:.0f}&nbsp;%" if d.ref else ""
            rows.append(f'<tr><td>{escape(d.label)}</td>{pct_cell(r, stat=d)}<td class="num muted">{ref}</td></tr>')
        if rows:
            out.append(
                f'<div class="card"><h3>{title}</h3><table class="stats"><thead><tr><th></th>'
                '<th class="num">Toi</th><th class="num">Repère</th></tr></thead>'
                f"<tbody>{''.join(rows)}</tbody></table></div>"
            )
    return '<div class="grid2">' + "".join(out) + "</div>"


def build_self_report(hands: list[Hand], stats: dict[str, PlayerStats], hero: str,
                      embed: bool = False, spots_href: str = "spots", kinds: Optional[dict] = None,
                      head: str = "") -> str:
    """kinds : type de chaque adversaire (players.classify), pour séparer réguliers et récréatifs ; head : en tête,
    le choix du format et tes résultats tous formats confondus (l'application)."""
    h = stats[hero]
    results = opponent_results(hands, hero)
    kinds = kinds or {}

    def kind_cell(name: str) -> str:
        info = kinds.get(name)
        if not info:
            return "<td></td>"
        source = "" if info["source"] == "toi" else f' <span class="muted">({info["source"]})</span>'
        return f"<td>{escape(KINDS[info['kind']])}{source}</td>"
    period = f"{hands[0].date:%d/%m/%Y} → {hands[-1].date:%d/%m/%Y}" if hands else ""
    rows = "".join(
        f"<tr {opponent_row_data(r['name'], kinds.get(r['name'], {}).get('kind', 'reg'), r['hands'], r['net_bb'], r['bb100'], r['last'])}>"
        f"<td>{opponent_link(r['name'])}</td>{kind_cell(r['name'])}<td class=\"num\">{r['hands']}</td>"
        f"<td class=\"num\">{num(r['net_bb'], 1, sign=True)}</td><td class=\"num\">{num(r['bb100'], 1, sign=True)}</td>"
        f"<td class=\"num\">{num(r['ev_bb'], 1, sign=True)}</td><td class=\"num\">{num(r['net'], 2, sign=True)}&nbsp;€</td>"
        f"<td class=\"muted nowrap\">{r['first']:%d/%m/%Y} → {r['last']:%d/%m/%Y}</td></tr>"
        for r in results
    )
    heading = "" if embed else f"<h1>Mon jeu — {escape(hero)}</h1>"
    spots = f' · <a href="{escape(spots_href)}">Mes spots</a>' if spots_href else ""
    tools = opponent_tools(with_kind=bool(kinds)) if len(results) > 1 else ""
    body = f"""{heading}<style>{OPP_STYLE}</style>
<div class="meta">{len(hands)} mains · {len(results)} adversaire(s) · {period}{spots}</div>
{head}
{tiles(h)}

<h2>Résultat cumulé (bb)</h2>
<div class="card">{legend(h.curve)}{chart_svg(h.curve)}
<p class="note">« EV all-in » remplace le résultat réel des all-in payés avant la river par ton espérance : l'écart mesure la chance.</p></div>

<h2>Tes écarts les plus importants</h2>
<div class="card">{priorities_html(h, len(hands), folds=hero_fold_holdings(hands, hero))}
<p class="note">Les {TOP_FINDINGS} écarts aux repères qui comptent le plus : les nets d'abord (le hasard ne les explique pas),
puis selon la taille de l'écart, la fréquence de la situation et ce qu'elle met en jeu (le pot grossit à chaque street).
Le détail de toutes tes stats suit.</p></div>

<h2>Tes statistiques</h2>
<div class="legend-dev"><span><i style="background:var(--hi-bg)"></i>au-dessus du repère</span><span><i style="background:var(--lo-bg)"></i>en dessous du repère</span><span><b>gras</b> = écart net</span></div>
{self_stat_tables(h)}

<h2>Résultats par adversaire</h2>
{by_kind_html(results, kinds)}
<div class="card opp-box">{tools}<div class="scroll"><table class="stats opp-table"><thead><tr><th data-sort="name">Adversaire</th><th>Type</th>
<th class="num" data-sort="hands">Mains</th><th class="num" data-sort="net">Résultat (bb)</th><th class="num" data-sort="bb100">bb/100</th>
<th class="num">EV all-in (bb)</th><th class="num">€</th><th data-sort="last">Période</th></tr></thead><tbody>{rows}</tbody></table></div></div>

<h2>Où partent tes bb sans abattage</h2>
{nonshowdown_html(hands, hero)}

<p class="note">Tous adversaires confondus. Repères indicatifs pour un régulier HU solide à 100bb+.</p>
"""
    return html_page(f"Mon jeu — {hero}", body, embed, script=SCRIPT + (OPP_SCRIPT if tools else ""))
