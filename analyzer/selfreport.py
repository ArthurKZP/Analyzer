"""« Mon jeu » : ton bilan contre tous tes adversaires."""
from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Optional
from urllib.parse import quote

from .insights import SECTIONS
from .lines import hero_fold_holdings
from .models import Hand
from .players import KINDS
from .report import SCRIPT, chart_svg, findings_html, html_page, legend, nonshowdown_html, num, pct_cell, tiles
from .stats import PlayerStats, allin_ev


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


def opponent_link(name: str) -> str:
    """Lien vers la fiche d'un adversaire dans l'application."""
    return f'<a href="/#/adversaire/{quote(name, safe="")}" target="_top">{escape(name)}</a>'


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
                      embed: bool = False, spots_href: str = "spots", kinds: Optional[dict] = None) -> str:
    """kinds : type de chaque adversaire (players.classify), pour séparer réguliers et récréatifs."""
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
        f"<tr><td>{opponent_link(r['name'])}</td>{kind_cell(r['name'])}<td class=\"num\">{r['hands']}</td>"
        f"<td class=\"num\">{num(r['net_bb'], 1, sign=True)}</td><td class=\"num\">{num(r['bb100'], 1, sign=True)}</td>"
        f"<td class=\"num\">{num(r['ev_bb'], 1, sign=True)}</td><td class=\"num\">{num(r['net'], 2, sign=True)}&nbsp;€</td>"
        f"<td class=\"muted nowrap\">{r['first']:%d/%m/%Y} → {r['last']:%d/%m/%Y}</td></tr>"
        for r in results
    )
    heading = "" if embed else f"<h1>Mon jeu — {escape(hero)}</h1>"
    spots = f' · <a href="{escape(spots_href)}">Mes spots</a>' if spots_href else ""
    body = f"""{heading}
<div class="meta">{len(hands)} mains · {len(results)} adversaire(s) · {period}{spots}</div>
{tiles(h)}

<h2>Résultat cumulé (bb)</h2>
<div class="card">{legend(h.curve)}{chart_svg(h.curve)}
<p class="note">« EV all-in » remplace le résultat réel des all-in payés avant la river par ton espérance : l'écart mesure la chance.</p></div>

<h2>Résultats par adversaire</h2>
{by_kind_html(results, kinds)}
<div class="card scroll"><table class="stats"><thead><tr><th>Adversaire</th><th>Type</th><th class="num">Mains</th>
<th class="num">Résultat (bb)</th><th class="num">bb/100</th><th class="num">EV all-in (bb)</th><th class="num">€</th>
<th>Période</th></tr></thead><tbody>{rows}</tbody></table></div>

<h2>Tes écarts aux repères</h2>
<div class="card">{findings_html(h, hero_mode=True, folds=hero_fold_holdings(hands, hero))}</div>

<h2>Tes statistiques</h2>
<div class="legend-dev"><span><i style="background:var(--hi-bg)"></i>au-dessus du repère</span><span><i style="background:var(--lo-bg)"></i>en dessous du repère</span><span><b>gras</b> = écart net</span></div>
{self_stat_tables(h)}

<h2>Où partent tes bb sans abattage</h2>
{nonshowdown_html(hands, hero)}

<p class="note">Tous adversaires confondus. Repères indicatifs pour un régulier HU solide à 100bb+.</p>
"""
    return html_page(f"Mon jeu — {hero}", body, embed, script=SCRIPT)
