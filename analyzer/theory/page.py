"""Page « Préflop vs solveur » : tes décisions (et ses fréquences) comparées à la solution."""
from __future__ import annotations

from collections import Counter
from html import escape
from statistics import median
from typing import Optional
from urllib.parse import quote

from ..models import Hand
from ..report import cards_html, html_page, num
from ..stats import PlayerStats
from .extract import RANKS, hand_at
from .preflop import (
    DEVIATION,
    MAIN,
    MIXED,
    OUT_OF_RANGE,
    RANGE_STATS,
    Decision,
    Node,
    NodeSummary,
    Solution,
    decisions,
    load_solution,
    summarize,
)

GAP_ALERT = 5.0  # écart (points de %) à partir duquel une fréquence est signalée
MIN_DECISIONS = 15  # décisions minimum dans un nœud pour en tirer une synthèse

PAGE_STYLE = """
:root { --a-allin: #4a3aa7; --a-raise: #eb6834; --a-call: #1baf7a; --a-fold: #2a78d6; --cell-empty: #ecebe6; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --a-allin: #9085e9; --a-raise: #d95926; --a-call: #199e70; --a-fold: #3987e5; --cell-empty: #242423; }
}
:root[data-theme="dark"] { --a-allin: #9085e9; --a-raise: #d95926; --a-call: #199e70; --a-fold: #3987e5; --cell-empty: #242423; }
.rgrid-wrap { overflow-x: auto; }
.rgrid { display: grid; grid-template-columns: repeat(13, minmax(34px, 1fr)); gap: 2px; min-width: 470px; }
.rc { position: relative; aspect-ratio: 1.35; border-radius: 3px; background-color: var(--cell-empty); font-size: 11px; line-height: 1.1; overflow: hidden; }
.rc .hl { position: absolute; left: 3px; top: 2px; color: #fff; font-weight: 600; text-shadow: 0 0 2px rgba(0,0,0,0.75); }
.rc.out .hl { color: var(--muted); text-shadow: none; font-weight: 400; }
.rc .cnt { position: absolute; right: 2px; bottom: 2px; font-size: 10px; font-weight: 700; background: var(--surface); color: var(--ink); border-radius: 999px; padding: 0 4px; border: 1px solid var(--border); }
.rc .cnt.bad { border: 2px solid var(--alert); color: var(--alert); }
.alegend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 12px; color: var(--ink-2); margin: 10px 0 0; }
.alegend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -2px; margin-right: 6px; }
.alegend .badge-demo { font-size: 10px; font-weight: 700; border: 2px solid var(--alert); color: var(--alert); border-radius: 999px; padding: 0 4px; margin-right: 6px; }
.node-grid { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .node-grid { grid-template-columns: minmax(0, 1fr); } }
.gaps li { margin-bottom: 6px; }
.dev-group summary { cursor: pointer; }
.dev-group { margin: 6px 0; }
.dev-group table { margin-top: 6px; }
.bullets { margin: 0; padding-left: 18px; }
.bullets li { margin-bottom: 6px; }
"""


def _pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{num(x, 0)}&nbsp;%"


def _mix(node: Node, strategy: Optional[dict[str, float]]) -> str:
    if not strategy:
        return "hors range"
    return ", ".join(f"{node.word(a)} {100 * f:.0f} %" for a, f in strategy.items())


def _sort_combos(combos: list[str]) -> list[str]:
    def key(c: str) -> tuple:
        pair = len(c) == 2
        return (not pair, RANKS.index(c[0]), RANKS.index(c[1]), c[-1] if not pair else "")
    return sorted(set(combos), key=key)


def grid_html(node: Node, solution: Solution, by_hand: dict[str, list[Decision]]) -> str:
    cells = []
    for i in range(13):
        for j in range(13):
            hand = hand_at(i, j)
            strategy = node.strategy(hand)
            weight = solution.weight(node.key, hand)
            played = by_hand.get(hand, [])
            bad = [d for d in played if d.verdict in (DEVIATION, OUT_OF_RANGE)]
            title = f"{hand} — solveur : {_mix(node, strategy)}"
            if played:
                acts = Counter(node.word(d.action) for d in played)
                title += " · toi : " + ", ".join(f"{a} ×{n}" for a, n in acts.most_common())
            if strategy and weight > 0.005:
                stops, start = [], 0.0
                for action, freq in strategy.items():
                    end = start + 100 * freq
                    stops.append(f"var(--a-{action}) {start:.1f}% {end:.1f}%")
                    start = end
                height = max(8.0, 100 * weight)
                style = f'background-image:linear-gradient(to right,{",".join(stops)});' \
                        f"background-size:100% {height:.0f}%;background-position:bottom;background-repeat:no-repeat"
                cls = "rc"
            else:
                style, cls = "", "rc out"
            badge = (f'<span class="cnt{" bad" if bad else ""}">{"✕" if bad else ""}{len(played)}</span>'
                     if played else "")
            cells.append(f'<div class="{cls}" style="{style}" title="{escape(title)}"><span class="hl">{hand}</span>{badge}</div>')
    legend = "".join(f'<span><i style="background:var(--a-{a})"></i>{escape(node.word(a))}</span>' for a in node.actions)
    return (f'<div class="rgrid-wrap"><div class="rgrid" role="img" aria-label="Stratégie du solveur, {escape(node.label)}">'
            f'{"".join(cells)}</div></div><div class="alegend">{legend}'
            '<span>hauteur = part de la main qui arrive ici</span>'
            '<span><span class="badge-demo">✕2</span>tes décisions avec cette main (✕ = écart)</span></div>')


def frequency_table(summary: NodeSummary) -> str:
    node = summary.node
    actual, expected = summary.actual, summary.expected
    rows = []
    for a in node.actions:
        act, exp = actual.get(a), expected.get(a)
        cls = ""
        if act is not None and exp is not None and abs(act - exp) >= GAP_ALERT:
            cls = " dev-haut strong" if act > exp else " dev-bas strong"
        rows.append(f"<tr><td>{escape(node.word(a).capitalize())}</td><td class=\"num{cls}\"><span class=\"v\">{_pct(act)}</span></td>"
                    f'<td class="num">{_pct(exp)}</td><td class="num muted">{_pct(node.total(a))}</td></tr>')
    return ('<div class="scroll"><table class="stats"><thead><tr><th></th><th class="num">Toi</th><th class="num">Solveur, mêmes mains</th>'
            f'<th class="num">Solveur, range complète</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def deviations_html(summary: NodeSummary, spots_href: str) -> str:
    node = summary.node
    groups = summary.deviations()
    if not groups:
        return '<p class="muted">Aucun écart net : toutes tes décisions font partie de la stratégie du solveur.</p>'
    out = []
    for (action, best), items in groups:
        combos = ", ".join(_sort_combos([d.combo for d in items]))
        rows = "".join(
            f'<tr><td class="nowrap">{d.hand.date:%d/%m %H:%M}</td><td>{cards_html(d.hand.hole_cards[d.player])}</td>'
            f'<td class="muted">{escape(_mix(node, d.strategy))}</td>'
            f'<td class="num">{num(d.effective_bb, 0)}&nbsp;bb</td>'
            f'<td>{_replay(spots_href, d.hand)}</td></tr>'
            for d in sorted(items, key=lambda d: d.hand.date)
        )
        out.append(
            f'<details class="dev-group"><summary><b>{escape(node.word(action).capitalize())} au lieu de '
            f'{escape(node.word(best))}</b> — {len(items)} fois : {escape(combos)}</summary>'
            '<table class="stats"><thead><tr><th>Date</th><th>Main</th><th>Solveur</th><th class="num">Tapis eff.</th>'
            f'<th></th></tr></thead><tbody>{rows}</tbody></table></details>'
        )
    return "".join(out)


def _replay(spots_href: str, hand: Hand) -> str:
    if not spots_href:
        return ""
    return f'<a class="spots-link" href="{escape(spots_href)}#hand={quote(hand.hand_id, safe="")}">rejouer</a>'


def key_points(summaries: list[NodeSummary], who: str = "tu") -> list[str]:
    """Phrases de synthèse : fréquences qui s'écartent du solveur et écarts les plus fréquents."""
    points = []
    for s in summaries:
        n = len(s.in_range)
        if n < MIN_DECISIONS:
            continue
        node = s.node
        actions = node.actions[:1] if len(node.actions) == 2 else node.actions  # le fold est le complément
        for a in actions:
            act, exp = s.actual.get(a, 0.0), s.expected.get(a, 0.0)
            if abs(act - exp) >= GAP_ALERT:
                points.append(f"{node.label} : {node.word(a)} {num(act, 0)} % contre {num(exp, 0)} % pour le solveur "
                              f"avec les mêmes mains ({n} décisions).")
        groups = [(k, g) for k, g in s.deviations() if len(g) >= 3]
        if groups:
            (action, best), items = groups[0]
            combos = ", ".join(_sort_combos([d.combo for d in items])[:8])
            points.append(f"{node.label} : {len(items)} fois {node.word(action)} au lieu de {node.word(best)} ({combos}).")
    return points


def villain_table(solution: Solution, ps: PlayerStats) -> str:
    rows = []
    for key, mapping in RANGE_STATS.items():
        node = solution.nodes[key]
        for action, stat in mapping.items():
            r = ps.r(stat)
            if not r.opps:
                continue
            gto = solution.aggressive_total(key) if action == "raise" else node.total(action)
            cls = ""
            if r.opps >= 15 and abs(r.pct - gto) >= GAP_ALERT:
                cls = " dev-haut strong" if r.pct > gto else " dev-bas strong"
            label = node.word("allin" if key == "bb_vs_4bet" and action == "raise" else action)
            rows.append(f'<tr><td>{escape(node.label)}</td><td>{escape(label)}</td>'
                        f'<td class="num{cls}"><span class="v">{_pct(r.pct)}</span><span class="n">{r.hits}/{r.opps}</span></td>'
                        f'<td class="num">{_pct(gto)}</td></tr>')
    return ('<div class="scroll"><table class="stats"><thead><tr><th>Situation</th><th>Action</th><th class="num">Lui</th>'
            f'<th class="num">Solveur</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _sizes_note(solution: Solution, hero_summaries: list[NodeSummary], villain: Optional[PlayerStats]) -> str:
    parts = []
    names = {"sb_open": "open", "bb_vs_open": "3bet", "sb_vs_3bet": "4bet"}
    for s in hero_summaries:
        if s.node.key in names and "raise" in s.node.sizes:
            size = s.sizes()
            if size:
                parts.append(f"ton {names[s.node.key]} {num(size, 1)}&nbsp;bb (solveur {num(s.node.sizes['raise'], 1)})")
    if villain:
        for key, label, stat in (("sb_open", "open", "pf_open_bb"), ("bb_vs_open", "3bet", "pf_3bet_bb"),
                                 ("sb_vs_3bet", "4bet", "pf_4bet_bb")):
            values = villain.sizes.get(stat)
            if values:
                parts.append(f"son {label} {num(median(values), 1)}&nbsp;bb (solveur {num(solution.nodes[key].sizes['raise'], 1)})")
    return " · ".join(parts)


def build_preflop_page(hands: list[Hand], hero: str, villain: Optional[str] = None,
                       stats: Optional[dict[str, PlayerStats]] = None, embed: bool = False,
                       spots_href: str = "spots", solution: Optional[Solution] = None, report_href: str = "") -> str:
    solution = solution or load_solution()
    hero_decisions = decisions(hands, hero, solution)
    summaries = summarize(hero_decisions, solution)
    verdicts = Counter(d.verdict for d in hero_decisions)
    total = len(hero_decisions) or 1
    depth = median(d.effective_bb for d in hero_decisions) if hero_decisions else 0
    villain_stats = stats.get(villain) if stats and villain else None

    heading = "" if embed else f"<h1>Préflop vs solveur — {escape(villain or 'tous tes adversaires')}</h1>"
    links = "" if embed else "".join(f' · <a href="{escape(href)}">{text}</a>' for href, text in
                                     ((report_href, "Rapport"), (spots_href, "Spots")) if href)
    tiles = "".join(
        f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div><div class="sub">{sub}</div></div>'
        for label, value, sub in (
            ("Décisions comparées", str(len(hero_decisions)), f"{len(hands)} mains"),
            ("Action principale du solveur", _pct(100 * verdicts[MAIN] / total), "ce qu'il fait le plus souvent avec ta main"),
            ("Action secondaire", _pct(100 * verdicts[MIXED] / total), "il le fait parfois (10 à 50 %)"),
            ("Écarts", _pct(100 * verdicts[DEVIATION] / total),
             f"{verdicts[DEVIATION]} décisions qu'il prend moins de 10 % du temps · {verdicts[OUT_OF_RANGE]} hors range"),
        )
    )
    points = key_points(summaries)
    bullets = "".join(f"<li>{escape(p)}</li>" for p in points) or "<li>Pas d'écart marqué par rapport au solveur.</li>"
    sizes = _sizes_note(solution, summaries, villain_stats)

    villain_block = ""
    if villain and villain_stats:
        shown = summarize(decisions(hands, villain, solution), solution)
        shown_points = []
        for s in shown:
            for (action, best), items in s.deviations():
                combos = ", ".join(_sort_combos([d.combo for d in items]))
                shown_points.append(f"<li><b>{escape(s.node.label)}</b> : {escape(s.node.word(action))} au lieu de "
                                    f"{escape(s.node.word(best))} — {len(items)} fois ({escape(combos)})</li>")
        shown_html = (f'<ul class="bullets">{"".join(shown_points)}</ul>' if shown_points
                      else '<p class="muted">Rien d\'anormal dans les mains qu\'il a montrées.</p>')
        villain_block = f"""
<h2>Lui face au solveur</h2>
<div class="grid2">
  <div class="card"><h3>Ses fréquences (toutes ses mains)</h3>{villain_table(solution, villain_stats)}
    <p class="note">Surligné : écart d'au moins {num(GAP_ALERT, 0)} points avec au moins 15 occasions.</p></div>
  <div class="card"><h3>Mains montrées que le solveur ne joue pas ainsi</h3>{shown_html}
    <p class="note">Seules les mains allées à l'abattage sont connues : cette liste prouve qu'il joue ces mains, pas qu'il n'en joue pas d'autres.</p></div>
</div>"""

    nodes_html = []
    for s in summaries:
        if not s.decisions:
            continue
        nodes_html.append(f"""
<h2>{escape(s.node.label)} <span class="muted" style="font-size:14px;font-weight:400">— {len(s.decisions)} décisions</span></h2>
<div class="node-grid">
  <div class="card">{grid_html(s.node, solution, s.by_hand())}</div>
  <div class="card"><h3>Fréquences</h3>{frequency_table(s)}
    <p class="note">« Mêmes mains » : ce qu'aurait fait le solveur avec exactement les mains que tu avais ici.
    Les mains hors range (que le solveur n'amène jamais ici) sont exclues.</p>
    <h3 style="margin-top:16px">Écarts</h3>{deviations_html(s, spots_href)}</div>
</div>""")

    body = f"""{heading}
<div class="meta">{escape(solution.name)} · {escape(solution.description)}{links}</div>
<div class="tiles">{tiles}</div>

<h2>En bref</h2>
<div class="card"><ul class="bullets">{bullets}</ul>
<p class="note">Solution à {num(solution.stack_bb, 0)}&nbsp;bb ; ta profondeur effective médiane ici est de {num(depth, 0)}&nbsp;bb.
{('Tailles observées : ' + sizes + '. ') if sizes else ''}Plus les tailles et la profondeur s'éloignent de la solution, plus la comparaison est indicative.
{escape(solution.source)}</p></div>
{villain_block}
{''.join(nodes_html) or '<p class="muted">Aucune décision préflop comparable.</p>'}
"""
    return html_page(f"Préflop vs solveur — {villain or hero}", f"<style>{PAGE_STYLE}</style>{body}", embed)
