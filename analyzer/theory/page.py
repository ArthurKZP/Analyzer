"""Page « Préflop vs solveur » : tes décisions (et ses fréquences) comparées à la solution ; aux tables à plusieurs,
tes décisions comparées aux charts (build_ring_preflop_page, theory/ring_preflop.py)."""
from __future__ import annotations

from collections import Counter
from html import escape
from statistics import median
from typing import TYPE_CHECKING, Callable, Optional
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

if TYPE_CHECKING:
    from .ring_preflop import RingPreflop

GAP_ALERT = 5.0  # écart (points de %) à partir duquel une fréquence est signalée
MIN_DECISIONS = 15  # décisions minimum dans un nœud pour en tirer une synthèse
# La théorie comparée : la solution heads-up, ou les charts des tables à plusieurs.
REFS = {
    "solveur": {"the": "le solveur", "of": "du solveur", "col": "Solveur",
                "most": "ce qu'il fait le plus souvent avec ta main", "sometimes": "il le fait parfois (10 à 50 %)",
                "rarely": "décisions qu'il prend moins de 10 % du temps"},
    "charts": {"the": "les charts", "of": "des charts", "col": "Charts",
               "most": "ce qu'ils font le plus souvent avec ta main", "sometimes": "ils le font parfois (10 à 50 %)",
               "rarely": "décisions qu'ils prennent moins de 10 % du temps"},
}

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
.brief { list-style: none; margin: 0; padding: 0; }
.brief > li { margin: 0 0 12px; }
.brief-row { display: flex; gap: 8px; align-items: baseline; margin-top: 4px; }
.brief-tag { flex: 0 0 100px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; }
.brief-tag.leak { color: var(--alert); }
.brief-tag.fix { color: var(--good); }
details.brief-more { margin: 6px 0 0; border: none; background: none; font-size: 13px; }
details.brief-more summary { padding: 0; color: var(--muted); border: none; }
details.brief-more .note { margin: 6px 0 0; }
details.rp-node { margin: 8px 0; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
details.rp-node > summary { cursor: pointer; padding: 10px 14px; }
details.rp-node > .rp-body { padding: 0 14px 14px; }
details.rp-node .card { background: var(--page); }
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


def grid_html(node: Node, solution: Solution, by_hand: dict[str, list[Decision]], ref: str = "solveur") -> str:
    cells = []
    for i in range(13):
        for j in range(13):
            hand = hand_at(i, j)
            strategy = node.strategy(hand)
            weight = solution.weight(node.key, hand)
            played = by_hand.get(hand, [])
            bad = [d for d in played if d.verdict in (DEVIATION, OUT_OF_RANGE)]
            title = f"{hand} — {ref} : {_mix(node, strategy)}"
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
    return (f'<div class="rgrid-wrap"><div class="rgrid" role="img" aria-label="Stratégie {REFS[ref]["of"]}, {escape(node.label)}">'
            f'{"".join(cells)}</div></div><div class="alegend">{legend}'
            '<span>hauteur = part de la main qui arrive ici</span>'
            '<span><span class="badge-demo">✕2</span>tes décisions avec cette main (✕ = écart)</span></div>')


def frequency_table(summary: NodeSummary, ref: str = "solveur") -> str:
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
    col = REFS[ref]["col"]
    return ('<div class="scroll"><table class="stats"><thead><tr><th></th><th class="num">Toi</th>'
            f'<th class="num">{col},<br>mêmes mains</th><th class="num">{col},<br>range complète</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def deviations_html(summary: NodeSummary, spots_href: str, ref: str = "solveur",
                    replay: Optional[Callable[[Hand], str]] = None) -> str:
    """Tes écarts groupés par action ; replay : le lien pour revoir une main (sinon, Mes spots)."""
    node = summary.node
    groups = summary.deviations()
    replay = replay or (lambda hand: _replay(spots_href, hand))
    if not groups:
        return f'<p class="muted">Aucun écart net : toutes tes décisions font partie de la stratégie {REFS[ref]["of"]}.</p>'
    out = []
    for (action, best), items in groups:
        combos = ", ".join(_sort_combos([d.combo for d in items]))
        rows = "".join(
            f'<tr><td class="nowrap">{d.hand.date:%d/%m %H:%M}</td><td>{cards_html(d.hand.hole_cards[d.player])}</td>'
            f'<td class="muted">{escape(_mix(node, d.strategy))}</td>'
            f'<td class="num">{num(d.effective_bb, 0)}&nbsp;bb</td>'
            f'<td>{replay(d.hand)}</td></tr>'
            for d in sorted(items, key=lambda d: d.hand.date)
        )
        out.append(
            f'<details class="dev-group"><summary><b>{escape(node.word(action).capitalize())} au lieu de '
            f'{escape(node.word(best))}</b> — {len(items)} fois : {escape(combos)}</summary>'
            f'<table class="stats"><thead><tr><th>Date</th><th>Main</th><th>{REFS[ref]["col"]}</th><th class="num">Tapis eff.</th>'
            f'<th></th></tr></thead><tbody>{rows}</tbody></table></details>'
        )
    return "".join(out)


def _replay(spots_href: str, hand: Hand) -> str:
    if not spots_href:
        return ""
    return f'<a class="spots-link" href="{escape(spots_href)}#hand={quote(hand.hand_id, safe="")}">rejouer</a>'


MAX_BRIEF = 3  # situations dans « En bref » (le détail de chacune suit, nœud par nœud)


def _of(word: str) -> str:
    """« de 3bet », « d'open »."""
    return f"d'{word}" if word[:1].lower() in "aeiouyh" else f"de {word}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def brief_points(summaries: list[NodeSummary], limit: int = MAX_BRIEF, ref: str = "solveur") -> tuple[list[str], int]:
    """« En bref » : une entrée (HTML) par situation qui s'écarte de la théorie, les plus importantes d'abord (mains
    jouées autrement : l'écart de fréquence rapporté aux décisions, ou les écarts main par main), au plus limit ; et
    le nombre d'autres situations qui s'en écartent.

    Chaque entrée sépare le leak (ce que tu joues : ta fréquence face à celle de la théorie avec les mêmes mains, et
    l'écart main par main le plus courant) du correctif (ce qu'il faut jouer à la place, en commençant par ces
    mains)."""
    theory = "le solveur" if ref == "solveur" else "les charts"
    found = []
    for s in summaries:
        n = len(s.in_range)
        if n < MIN_DECISIONS:
            continue
        node = s.node
        actions = node.actions[:1] if len(node.actions) == 2 else node.actions  # le fold est le complément
        gaps = [(a, s.actual.get(a, 0.0), s.expected.get(a, 0.0)) for a in actions]
        gaps = [g for g in gaps if abs(g[1] - g[2]) >= GAP_ALERT]
        groups = [(k, g) for k, g in s.deviations() if len(g) >= 3]
        if not gaps and not groups:
            continue
        leak, fix = [], []
        weight = 0.0
        gap = max(gaps, key=lambda g: abs(g[1] - g[2])) if gaps else None
        if gap:
            a, act, exp = gap
            word = escape(node.word(a))
            leak.append(f"{'Trop' if act > exp else 'Pas assez'} {_of(word)} : {num(act, 0)}&nbsp;% de tes décisions "
                        f"ici, contre {num(exp, 0)}&nbsp;% pour {theory} avec les mêmes mains.")
            fix.append(f"{'Plus' if act < exp else 'Moins'} {_of(word)}, vers {num(exp, 0)}&nbsp;% ici")
            weight = abs(act - exp) * n / 100
        if groups:
            (action, best), items = groups[0]
            combos = _sort_combos([d.combo for d in items])
            shown = escape(", ".join(combos[:3]) + ("…" if len(combos) > 3 else ""))
            played, wanted = escape(node.word(action)), escape(node.word(best))
            leak.append(f"{'Surtout : ' if gap else ''}{played if gap else _cap(played)} au lieu de {wanted} avec "
                        f"{shown} ({len(items)} fois).")
            same_way = gap and ((gap[1] < gap[2] and best == gap[0]) or (gap[1] > gap[2] and action == gap[0]))
            if same_way:  # ces mains sont le premier pas du correctif
                fix[0] += f" : commence par {shown} ({wanted} plutôt que {played})"
            else:
                fix.append(f"avec {shown} : {wanted} plutôt que {played}")
            weight = max(weight, len(items))
        elif gap:
            fix[0] += " (la grille de la situation, plus bas, montre quelles mains)"
        entry = (f'<b>{escape(node.label)}</b> <span class="muted">· {n} décision{"s" if n > 1 else ""}</span>'
                 f'<div class="brief-row"><span class="brief-tag leak">Le leak</span><span>{" ".join(leak)}</span></div>'
                 f'<div class="brief-row"><span class="brief-tag fix">Le correctif</span>'
                 f'<span>{_cap(" ; ".join(fix))}.</span></div>')
        found.append((weight, entry))
    found.sort(key=lambda x: -x[0])
    return [line for _, line in found[:limit]], max(0, len(found) - limit)


def _brief(summaries: list[NodeSummary], ref: str, facts: str, more: str) -> str:
    """La section « En bref » : les écarts principaux, une ligne de repères (facts) et le détail de la comparaison
    (more), replié."""
    points, rest = brief_points(summaries, ref=ref)
    if points:
        items = "".join(f"<li>{p}</li>" for p in points)
        if rest:
            items += (f'<li class="muted">Et {rest} autre{"s" if rest > 1 else ""} situation{"s" if rest > 1 else ""} '
                      "à revoir, nœud par nœud plus bas.</li>")
    else:
        items = f"<li>Pas d'écart marqué par rapport {'au solveur' if ref == 'solveur' else 'aux charts'}.</li>"
    return (f'<h2>En bref</h2>\n<div class="card"><ul class="brief">{items}</ul>'
            f'<p class="note" style="margin-bottom:0">{facts} Un écart à la théorie n\'est pas toujours une fuite : '
            "le Leakfinding vérifie ce qu'il coûte (ou rapporte) en jeu.</p>"
            f'<details class="brief-more"><summary>Comment c\'est comparé</summary><p class="note">{more}</p></details>'
            "</div>")


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


def _tiles(hero_decisions: list[Decision], hands: int, ref: str = "solveur") -> str:
    """Tes décisions comparées : la part jouée comme l'action principale de la théorie, secondaire, ou en écart."""
    verdicts = Counter(d.verdict for d in hero_decisions)
    total = len(hero_decisions) or 1
    r = REFS[ref]
    return "".join(
        f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div><div class="sub">{sub}</div></div>'
        for label, value, sub in (
            ("Décisions comparées", str(len(hero_decisions)), f"{hands} mains"),
            (f"Action principale {r['of']}", _pct(100 * verdicts[MAIN] / total), r["most"]),
            ("Action secondaire", _pct(100 * verdicts[MIXED] / total), r["sometimes"]),
            ("Écarts", _pct(100 * verdicts[DEVIATION] / total),
             f"{verdicts[DEVIATION]} {r['rarely']} · {verdicts[OUT_OF_RANGE]} hors range"),
        )
    )


def build_preflop_page(hands: list[Hand], hero: str, villain: Optional[str] = None,
                       stats: Optional[dict[str, PlayerStats]] = None, embed: bool = False,
                       spots_href: str = "spots", solution: Optional[Solution] = None, report_href: str = "",
                       compare_hero: bool = True, note: str = "", switch: str = "") -> str:
    """compare_hero=False (adversaire récréatif) : seulement ses fréquences, pas tes décisions face au solveur ;
    switch : le choix heads-up / tables à plusieurs (report.format_switch)."""
    solution = solution or load_solution()
    hero_decisions = decisions(hands, hero, solution) if compare_hero else []
    summaries = summarize(hero_decisions, solution)
    depth = median(d.effective_bb for d in hero_decisions) if hero_decisions else 0
    villain_stats = stats.get(villain) if stats and villain else None

    heading = "" if embed else f"<h1>Préflop vs solveur — {escape(villain or 'tous tes adversaires')}</h1>"
    links = "" if embed else "".join(f' · <a href="{escape(href)}">{text}</a>' for href, text in
                                     ((report_href, "Rapport"), (spots_href, "Spots")) if href)
    tiles = _tiles(hero_decisions, len(hands))
    sizes = _sizes_note(solution, summaries, villain_stats)
    facts = (f"Solution à {num(solution.stack_bb, 0)}&nbsp;bb, ta profondeur médiane ici : {num(depth, 0)}&nbsp;bb"
             + (f" · {sizes}" if sizes else "") + ".")
    more = ("Chaque décision est comparée à ce que fait le solveur avec exactement ta main (« mêmes mains » : les mains "
            "qu'il n'amène jamais à ce nœud sont exclues). Plus tes tailles et ta profondeur s'éloignent de la solution, "
            f"plus la comparaison est indicative. {escape(solution.source)}")

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

    note_html = f'<div class="card"><p class="note" style="margin:0">{escape(note)}</p></div>' if note else ""
    if not compare_hero:
        body = f"""{heading}
<div class="meta">{escape(solution.name)} · {escape(solution.description)}{links}</div>
{note_html}
{villain_block or '<p class="muted">Pas assez de mains pour lire ses fréquences.</p>'}
"""
        return html_page(f"Préflop vs solveur — {villain or hero}", f"<style>{PAGE_STYLE}</style>{body}", embed)
    body = f"""{heading}
<div class="meta">{escape(solution.name)} · {escape(solution.description)}{links}</div>
{switch}
{note_html}
<div class="tiles">{tiles}</div>

{_brief(summaries, "solveur", facts, more)}
{villain_block}
{''.join(nodes_html) or '<p class="muted">Aucune décision préflop comparable.</p>'}
"""
    return html_page(f"Préflop vs solveur — {villain or hero}", f"<style>{PAGE_STYLE}</style>{body}", embed)


def _seen_note(node: Node) -> str:
    """D'où viennent les décisions d'un nœud quand elles ne sont pas toutes de sa table et de sa place (le LJ d'une
    table de 9 joueurs jugé avec les charts de l'UTG du 6-max…)."""
    seen = getattr(node, "seen", {})
    me = node.key.split("|")[2]
    chart = getattr(node, "chart", "")
    if not seen or set(seen) == {(chart, me)}:
        return ""
    parts = ", ".join(f"{pos} en {fmt} ({n})" for (fmt, pos), n in sorted(seen.items(), key=lambda kv: -kv[1]))
    return f'<p class="note" style="margin:0 0 8px">Tes décisions jugées avec ces charts : {escape(parts)}.</p>'


def _ring_node(summary: NodeSummary, solution: Solution, replay: Optional[Callable[[Hand], str]], open_: bool) -> str:
    node = summary.node
    n = len(summary.decisions)
    bad = summary.verdicts[DEVIATION]
    count = f"{n} décision{'s' if n > 1 else ''}" + (f", {bad} écart{'s' if bad > 1 else ''}" if bad else "")
    return (f'<details class="rp-node"{" open" if open_ else ""}><summary><b>{escape(node.label)}</b> '
            f'<span class="muted">— {count}</span></summary><div class="rp-body">{_seen_note(node)}'
            f'<div class="node-grid"><div class="card">{grid_html(node, solution, summary.by_hand(), "charts")}</div>'
            f'<div class="card"><h3>Fréquences</h3>{frequency_table(summary, "charts")}'
            '<p class="note">« Mêmes mains » : ce qu\'auraient joué les charts avec exactement les mains que tu avais ici. '
            'Les mains hors range (que les charts n\'amènent jamais ici) sont exclues.</p>'
            f'<h3 style="margin-top:16px">Écarts</h3>{deviations_html(summary, "", "charts", replay)}</div></div>'
            '</div></details>')


def build_ring_preflop_page(found: "RingPreflop", hero: str, embed: bool = False, note: str = "", switch: str = "",
                            replay: Optional[Callable[[Hand], str]] = None, load_hint: str = "") -> str:
    """Mon préflop aux tables à plusieurs (3 à 9 joueurs ensemble) : tes décisions face aux charts, avec les mêmes
    cartes, nœud par nœud (theory/ring_preflop.py) ; replay : le lien pour revoir une main."""
    from .ring_preflop import SITUATIONS
    solution = found.solution
    note_html = f'<div class="card"><p class="note" style="margin:0">{escape(note)}</p></div>' if note else ""
    title = f"Préflop face aux charts — {hero}"
    if not found.charts:
        body = (f'<div class="meta">Tables à plusieurs · tes décisions préflop face aux charts</div>{switch}'
                '<div class="card"><p style="margin:0">Pas encore de charts : tes décisions préflop aux tables à plusieurs '
                f'se comparent aux charts (open, défense, 3bet, 4bet par position).{load_hint}</p></div>')
        return html_page(title, f"<style>{PAGE_STYLE}</style>{body}", embed)
    hero_decisions = found.decisions
    summaries = [s for s in summarize(hero_decisions, solution) if s.decisions]
    depth = median(d.effective_bb for d in hero_decisions) if hero_decisions else 0
    facts = f"Charts à 100&nbsp;bb, ta profondeur médiane ici : {num(depth, 0)}&nbsp;bb."
    more = ("Chaque décision est comparée à ce que jouent les charts avec exactement ta main (« mêmes mains »), avec les "
            "charts de sa table, sinon ceux du 6-max à même nombre de joueurs derrière (le LJ d'une table de 7 à 9 "
            "joueurs comme l'UTG). Ne sont pas comparées : les places sans chart (UTG à UTG+2 à 7-9 joueurs) et les "
            "situations que les charts ne couvrent pas (après un limp, squeeze, face au 4bet). Un tapis compte comme une "
            "relance.")
    sections = []
    for situation, heading in SITUATIONS:
        group = [s for s in summaries if getattr(s.node, "situation", "") == situation]
        if not group:
            continue
        top = max(group, key=lambda s: len(s.decisions))
        sections.append(f"<h2>{escape(heading)}</h2>" + "".join(_ring_node(s, solution, replay, s is top) for s in group))
    source = f" · {escape(solution.source)}" if solution.source else ""
    body = f"""<div class="meta">Tables à plusieurs · charts {escape(solution.description)}{source}</div>
{switch}
{note_html}
<div class="tiles">{_tiles(hero_decisions, found.hands, "charts")}</div>

{_brief(summaries, "charts", facts, more)}
{''.join(sections) or '<p class="muted">Aucune décision préflop comparable aux charts.</p>'}
"""
    return html_page(title, f"<style>{PAGE_STYLE}</style>{body}", embed)
