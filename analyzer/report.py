"""Rapport HTML autonome (un seul fichier, sans dépendance) pour un adversaire."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from html import escape
from statistics import median
from typing import Optional

from .cards import describe_holding, equity
from .insights import (
    DUELS,
    SECTIONS,
    SIZE_BUCKETS,
    STRENGTH_ORDER,
    bluff_break_even,
    combined,
    deviation,
    duel_verdict,
    findings,
    raise_showdowns,
    responses_by_size,
    sizing_profile,
    strength_class,
)
from .models import CALL, FOLD, POSTFLOP, RAISE, STREETS, VOLUNTARY, Hand
from .stats import PlayerStats, Ratio, allin_ev, think_times

SUIT_SYMBOL = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
KIND_LABEL = {"fold": "fold", "check": "check", "call": "call", "bet": "bet", "raise": "raise"}


# --- Formatage -------------------------------------------------------------------

def num(x: Optional[float], digits: int = 1, sign: bool = False) -> str:
    if x is None:
        return "–"
    text = f"{x:+,.{digits}f}" if sign else f"{x:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",").replace("-", "−")


def pct_cell(r: Ratio, stat=None) -> str:
    if not r.opps:
        return '<td class="num muted">–</td>'
    cls = "num"
    if stat is not None:
        dev = deviation(stat, r)
        if dev:
            cls += " dev-" + dev[0] + (" strong" if dev[1] else "")
    small = "small" if r.opps < 15 else ""
    return (
        f'<td class="{cls}"><span class="v">{num(r.pct, 0)}&nbsp;%</span>'
        f'<span class="n {small}">{r.hits}/{r.opps}</span></td>'
    )


def cards_html(cards: list[str]) -> str:
    out = []
    for c in cards:
        rank, suit = c[0].upper(), c[1].lower()
        out.append(f'<span class="pc s{suit}">{escape(rank)}{SUIT_SYMBOL.get(suit, suit)}</span>')
    return '<span class="cards">' + "".join(out) + "</span>"


def bb(x: float) -> str:
    return num(x, 1).replace(",0", "")


def hand_line(hand: Hand, tags: dict[str, str], villain: str) -> list[tuple[str, str]]:
    """[(street, html)] : actions de chaque street en bb, avec sizing en % du pot."""
    times = think_times(hand)
    rows = []
    board_slices = {"preflop": [], "flop": hand.board[:3], "turn": hand.board[3:4], "river": hand.board[4:5]}
    for street in STREETS:
        if street != "preflop" and not board_slices[street]:
            break
        acts = [(a, t) for a, t in zip(hand.actions, times) if a.street == street and a.kind in VOLUNTARY]
        parts = []
        for a, t in acts:
            tag = f'<b class="tag {"tv" if a.player == villain else "th"}">{tags[a.player]}</b>'
            if a.kind == "bet":
                pct = 100 * a.amount / a.pot_before if a.pot_before else 0
                verb = f"bet {bb(a.amount / hand.bb)} ({num(pct, 0)}&nbsp;%)"
            elif a.kind == "raise":
                verb = f"raise {bb(a.to / hand.bb)}"
            else:
                verb = KIND_LABEL[a.kind]
            if a.all_in:
                verb += " <em>tapis</em>"
            timer = f' <span class="t">{t:.0f}s</span>' if t is not None and a.player == villain else ""
            parts.append(f'<span class="act">{tag} {verb}{timer}</span>')
        head = cards_html(board_slices[street]) if street != "preflop" else ""
        rows.append((street, head + " " + " · ".join(parts)))
    return rows


# --- Graphique -------------------------------------------------------------------

SERIES = [
    ("Résultat réel", "--series-1"),
    ("EV all-in", "--series-2"),
    ("À l'abattage", "--series-3"),
    ("Sans abattage", "--series-4"),
]


def chart_svg(curve: list[tuple[float, ...]]) -> str:
    if not curve:
        return ""
    width, height, pad_l, pad_r, pad_t, pad_b = 880, 300, 56, 16, 16, 32
    values = [v for point in curve for v in point] + [0.0]
    lo, hi = min(values), max(values)
    step = _nice_step((hi - lo) / 5 or 1)
    lo, hi = step * (lo // step), step * -(-hi // step)
    n = len(curve)

    def x(i: int) -> float:
        return pad_l + (width - pad_l - pad_r) * (i / max(n - 1, 1))

    def y(v: float) -> float:
        return pad_t + (height - pad_t - pad_b) * (1 - (v - lo) / (hi - lo or 1))

    grid = []
    tick = lo
    while tick <= hi + 1e-9:
        cls = "axis" if abs(tick) < 1e-9 else "grid"
        grid.append(
            f'<line class="{cls}" x1="{pad_l}" x2="{width - pad_r}" y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>'
            f'<text class="tick" x="{pad_l - 8}" y="{y(tick) + 4:.1f}" text-anchor="end">{num(tick, 0)}</text>'
        )
        tick += step
    xstep = _nice_step(n / 6)
    for i in range(0, n, int(xstep) or 1):
        grid.append(f'<text class="tick" x="{x(i):.1f}" y="{height - 10}" text-anchor="middle">{i}</text>')
    lines = []
    for s, (_, var) in enumerate(SERIES):
        pts = " ".join(f"{x(i):.1f},{y(p[s]):.1f}" for i, p in enumerate(curve))
        lines.append(f'<polyline class="line" style="stroke:var({var})" points="{pts}"/>')
    data = json.dumps([[round(v, 1) for v in p] for p in curve])
    return f"""
<div class="chart-wrap"><div class="chart" data-points='{data}' data-x0="{pad_l}" data-x1="{width - pad_r}">
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="Résultat cumulé en big blinds, main par main">
    {''.join(grid)}
    {''.join(lines)}
    <line class="cross" x1="0" x2="0" y1="{pad_t}" y2="{height - pad_b}" visibility="hidden"/>
    <rect class="hit" x="{pad_l}" y="{pad_t}" width="{width - pad_l - pad_r}" height="{height - pad_t - pad_b}"/>
  </svg>
  <div class="tooltip" hidden></div>
</div></div>"""


def _nice_step(raw: float) -> float:
    raw = max(raw, 1e-9)
    magnitude = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * magnitude:
            return m * magnitude
    return 10 * magnitude


# --- Sections ----------------------------------------------------------------------

def tiles(hero: PlayerStats) -> str:
    ev_total = hero.net_bb + hero.ev_adjust_bb
    items = [
        ("Ton résultat", f"{num(hero.net_bb, 1, sign=True)} bb", f"{num(hero.net, 2, sign=True)} €"),
        ("Winrate", f"{num(hero.bb_per_100, 1, sign=True)}", "bb/100 mains"),
        ("Résultat EV all-in", f"{num(ev_total, 1, sign=True)} bb",
         f"écart chance : {num(-hero.ev_adjust_bb, 1, sign=True)} bb sur {hero.allin_hands} all-in"),
        ("Avec / sans abattage", f"{num(hero.net_bb_showdown, 0, sign=True)} / {num(hero.net_bb_no_showdown, 0, sign=True)}",
         "bb"),
    ]
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div><div class="sub">{sub}</div></div>'
        for label, value, sub in items
    ) + "</div>"


def legend(curve) -> str:
    last = curve[-1] if curve else (0, 0, 0, 0)
    return '<div class="legend">' + "".join(
        f'<span><i style="background:var({var})"></i>{name} <b>{num(last[i], 0, sign=True)}&nbsp;bb</b></span>'
        for i, (name, var) in enumerate(SERIES)
    ) + "</div>"


def findings_html(ps: PlayerStats, hero_mode: bool = False) -> str:
    items = findings(ps)
    if not items:
        return '<p class="muted">Aucun écart marqué par rapport aux repères (ou échantillon trop faible).</p>'
    rows = []
    for f in items[:12]:
        lo, hi = f.stat.ref
        badge = '<span class="pill strong">net</span>' if f.strong else '<span class="pill">tendance</span>'
        arrow = "▲" if f.direction == "haut" else "▼"
        if hero_mode:
            text = f"{f.reading.fix[0].upper()}{f.reading.fix[1:]}."
        else:
            text = f"{f.reading.fact} → {f.reading.exploit}."
        rows.append(
            f"<li>{badge}<div><b>{escape(f.stat.label)} {arrow} {num(f.ratio.pct, 0)}&nbsp;%</b>"
            f' <span class="muted">({f.ratio.hits}/{f.ratio.opps} · repère {lo:.0f}–{hi:.0f}&nbsp;%)</span>'
            f"<br>{escape(text)}</div></li>"
        )
    return '<ul class="findings">' + "".join(rows) + "</ul>"


def duels_html(villain: PlayerStats, hero: PlayerStats) -> str:
    rows = []
    for d in DUELS:
        v, h = combined(villain, d.villain_key), combined(hero, d.hero_key)
        level, text = duel_verdict(d, v, h)
        icon = {"alerte": "⚠", "ok": "✓", "info": "·"}[level]
        rows.append(
            f'<tr class="{level}"><td>{escape(d.title)}</td>'
            f"{pct_cell(v)}{pct_cell(h)}"
            f'<td><span class="lvl">{icon}</span> {escape(text)}</td></tr>'
        )
    return (
        '<table class="stats duel"><thead><tr><th>Situation</th><th class="num">Lui</th>'
        '<th class="num">Toi</th><th>Lecture</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"
    )


def stat_tables(villain: PlayerStats, hero: PlayerStats) -> str:
    out = []
    for title, defs in SECTIONS:
        rows = []
        for d in defs:
            v, h = villain.r(d.key), hero.r(d.key)
            if not v.opps and not h.opps:
                continue
            ref = f"{d.ref[0]:.0f}–{d.ref[1]:.0f}&nbsp;%" if d.ref else ""
            rows.append(
                f"<tr><td>{escape(d.label)}</td>{pct_cell(v, stat=d)}{pct_cell(h, stat=d)}"
                f'<td class="num muted">{ref}</td></tr>'
            )
        if rows:
            out.append(
                f'<div class="card"><h3>{title}</h3><table class="stats"><thead><tr><th></th>'
                '<th class="num">Lui</th><th class="num">Toi</th><th class="num">Repère</th></tr></thead>'
                f"<tbody>{''.join(rows)}</tbody></table></div>"
            )
    # Agression
    rows = []
    for label, street in (("Flop", "flop"), ("Turn", "turn"), ("River", "river"), ("Total postflop", None)):
        vaf, vafq = villain.aggression(street)
        haf, hafq = hero.aggression(street)
        rows.append(
            f'<tr><td>{label}</td><td class="num">{num(vaf, 1)}</td><td class="num">{num(vafq, 0)}&nbsp;%</td>'
            f'<td class="num">{num(haf, 1)}</td><td class="num">{num(hafq, 0)}&nbsp;%</td></tr>'
        )
    out.append(
        '<div class="card"><h3>Agression postflop</h3><table class="stats"><thead><tr><th></th>'
        '<th class="num">AF lui</th><th class="num">AFq lui</th><th class="num">AF toi</th>'
        f'<th class="num">AFq toi</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
        '<p class="note">AF = (bets + raises) / calls · AFq = (bets + raises) / (bets + raises + calls + folds).</p></div>'
    )
    return '<div class="grid2">' + "".join(out) + "</div>"


def preflop_sizes(villain: PlayerStats, hero: PlayerStats) -> str:
    rows = []
    for key, label in (("pf_open_bb", "Open-raise (bb)"), ("pf_iso_bb", "Iso-raise vs limp (bb)"),
                       ("pf_3bet_bb", "3bet (bb)"), ("pf_3bet_x", "3bet (× l'open)"),
                       ("pf_4bet_bb", "4bet (bb)"), ("pf_4bet_x", "4bet (× le 3bet)")):
        cells = []
        for ps in (villain, hero):
            values = ps.sizes.get(key, [])
            if values:
                common = ", ".join(f"{num(v, 1).replace(',0', '')}&nbsp;({c})" for v, c in
                                   Counter(round(v, 1) for v in values).most_common(3))
                cells.append(f'<td class="num">{num(median(values), 1)}</td><td class="muted">{common}</td>')
            else:
                cells.append('<td class="num muted">–</td><td></td>')
        if any(ps.sizes.get(key) for ps in (villain, hero)):
            rows.append(f"<tr><td>{label}</td>{''.join(cells)}</tr>")
    return (
        '<table class="stats"><thead><tr><th></th><th class="num">Lui (médiane)</th><th>Fréquents</th>'
        f'<th class="num">Toi (médiane)</th><th>Fréquents</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def sizing_html(hands: list[Hand], villain: str) -> str:
    profile = sizing_profile(hands, villain)
    raises = raise_showdowns(hands, villain)
    blocks = []
    for street in ("flop", "turn", "river"):
        counts = profile["counts"][street]
        total = sum(counts.values())
        if not total:
            continue
        rows = []
        for _, bucket in SIZE_BUCKETS:
            c = counts.get(bucket, 0)
            if not c:
                continue
            shown = profile["shown"][street].get(bucket, [])
            classes = Counter(strength_class(desc) for desc, _ in shown)
            chips = " ".join(
                f'<span class="chip k{STRENGTH_ORDER.index(k)}">{escape(k)}&nbsp;{classes[k]}</span>'
                for k in STRENGTH_ORDER if classes.get(k)
            )
            detail = "; ".join(f"{h.hole_cards[villain][0]}{h.hole_cards[villain][1]} : {d}" for d, h in shown)
            share = 100 * c / total
            rows.append(
                f'<tr><td>{bucket}</td><td class="num">{c}</td>'
                f'<td class="barcell"><span class="bar" style="width:{share:.0f}%"></span>'
                f'<span class="barv">{num(share, 0)}&nbsp;%</span></td>'
                f'<td title="{escape(detail)}">{chips or "<span class=muted>—</span>"}</td></tr>'
            )
        blocks.append(
            f'<div class="card"><h3>{street.capitalize()} — {total} mises</h3><table class="stats sizing"><thead><tr>'
            '<th>Taille (% du pot)</th><th class="num">Nb</th><th>Part</th><th>Mains montrées à l\'abattage</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        )
    raise_rows = []
    for street in POSTFLOP:
        shown = raises.get(street, [])
        if shown:
            classes = Counter(strength_class(d) for d, _ in shown)
            chips = " ".join(
                f'<span class="chip k{STRENGTH_ORDER.index(k)}">{escape(k)}&nbsp;{classes[k]}</span>'
                for k in STRENGTH_ORDER if classes.get(k)
            )
            raise_rows.append(f"<tr><td>Raise {street}</td><td>{chips}</td></tr>")
    if raise_rows:
        blocks.append(
            '<div class="card"><h3>Ses relances postflop montrées</h3><table class="stats"><tbody>'
            + "".join(raise_rows) + "</tbody></table></div>"
        )
    return '<div class="grid2">' + "".join(blocks) + "</div>"


BLUFF_MARGIN = 8  # points de % au-dessus du seuil avant de signaler


def responses_html(hands: list[Hand], bettor: str, responder: str, you_respond: bool) -> str:
    table = responses_by_size(hands, bettor, responder)
    rows = []
    for street in ("flop", "turn", "river"):
        for _, bucket in SIZE_BUCKETS:
            cell = table[street].get(bucket)
            if not cell:
                continue
            c = cell["counts"]
            n = sum(c.values())
            fold = 100.0 * c[FOLD] / n
            threshold = bluff_break_even(median(cell["sizes"]))
            over = n >= 8 and fold > threshold + BLUFF_MARGIN
            verdict = ("ses bluffs sont rentables" if you_respond else "tes bluffs sont rentables") if over else ""
            cls = ' class="alerte"' if over and you_respond else ""
            rows.append(
                f"<tr{cls}><td>{street.capitalize()}</td><td class=\"nowrap\">{bucket}</td><td class=\"num\">{n}</td>"
                f'<td class="num{" dev-haut strong" if over else ""}"><span class="v">{num(fold, 0)}&nbsp;%</span></td>'
                f'<td class="num">{num(100.0 * c[CALL] / n, 0)}&nbsp;%</td><td class="num">{num(100.0 * c[RAISE] / n, 0)}&nbsp;%</td>'
                f'<td class="num muted">{num(threshold, 0)}&nbsp;%</td><td>{verdict}</td></tr>'
            )
    if not rows:
        return '<p class="muted">Pas de mise postflop.</p>'
    return (
        '<table class="stats duel"><thead><tr><th>Street</th><th>Taille</th><th class="num">Nb</th><th class="num">Fold</th>'
        '<th class="num">Call</th><th class="num">Raise</th><th class="num">Seuil bluff</th><th></th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def timing_html(villain: PlayerStats, hero: PlayerStats) -> str:
    rows = []
    for key, label in (("pf_fold", "Préflop — fold"), ("pf_call", "Préflop — call"), ("pf_raise", "Préflop — raise"),
                       ("post_check", "Postflop — check"), ("post_bet", "Postflop — bet"),
                       ("post_call", "Postflop — call"), ("post_raise", "Postflop — raise"),
                       ("post_fold", "Postflop — fold"), ("river_bet", "River — bet"), ("river_call", "River — call")):
        cells = ""
        for ps in (villain, hero):
            values = ps.times.get(key, [])
            cells += (f'<td class="num">{num(median(values), 0)}&nbsp;s<span class="n">{len(values)}</span></td>'
                      if values else '<td class="num muted">–</td>')
        rows.append(f"<tr><td>{label}</td>{cells}</tr>")
    return (
        '<table class="stats"><thead><tr><th>Temps de décision médian</th><th class="num">Lui</th>'
        f'<th class="num">Toi</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def allin_html(hands: list[Hand], hero: str, villain: str) -> str:
    rows = []
    for h in hands:
        ev = allin_ev(h)
        if ev is None:
            continue
        last = [a for a in h.actions if a.kind in VOLUNTARY][-1]
        n = {"preflop": 0, "flop": 3, "turn": 4}[last.street]
        eq = equity(h.hole_cards[hero], h.hole_cards[villain], h.board[:n], seed=h.hand_id)
        actual = h.net(hero) / h.bb
        expected = ev[hero] / h.bb
        rows.append(
            f'<tr><td>{h.date:%H:%M}</td><td>{last.street}</td><td>{cards_html(h.hole_cards[hero])}</td>'
            f'<td>{cards_html(h.hole_cards[villain])}</td><td>{cards_html(h.board)}</td>'
            f'<td class="num">{num(100 * eq, 0)}&nbsp;%</td><td class="num">{num(expected, 1, sign=True)}</td>'
            f'<td class="num">{num(actual, 1, sign=True)}</td></tr>'
        )
    if not rows:
        return '<p class="muted">Aucun all-in payé avant la river avec les deux mains connues.</p>'
    return (
        '<table class="stats"><thead><tr><th>Heure</th><th>Street</th><th>Toi</th><th>Lui</th><th>Board</th>'
        '<th class="num">Ton équité</th><th class="num">EV (bb)</th><th class="num">Réel (bb)</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def showdowns_html(villain_stats: PlayerStats, hero: str, villain: str) -> str:
    groups: dict[str, list] = defaultdict(list)
    for entry in villain_stats.showdowns:
        groups[f"{entry.position} · {entry.preflop_line}"].append(entry)
    tags = {hero: "H", villain: "V"}
    out = []
    for name, entries in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        combos = Counter(e.combo for e in entries)
        combo_list = ", ".join(f"{c}{'×' + str(n) if n > 1 else ''}" for c, n in combos.most_common())
        rows = []
        for e in sorted(entries, key=lambda e: e.hand.date):
            h = e.hand
            line = "".join(
                f'<div class="st"><span class="sn">{street[0].upper() if street != "preflop" else "PF"}</span>{html}</div>'
                for street, html in hand_line(h, tags, villain)
            )
            rows.append(
                f'<tr><td class="nowrap">{h.date:%H:%M}<br><span class="muted">{e.position}</span></td>'
                f"<td>{cards_html(e.cards)}<br><span class=\"muted\">{escape(describe_holding(e.cards, h.board))}</span></td>"
                f"<td>{cards_html(h.hole_cards.get(hero, []))}</td>"
                f'<td class="line">{line}</td><td class="num">{num(e.net_bb, 1, sign=True)}</td></tr>'
            )
        out.append(
            f"<details><summary><b>{escape(name)}</b> <span class=\"muted\">— {len(entries)} main(s)</span>"
            f'<div class="combos">{escape(combo_list)}</div></summary>'
            '<table class="stats sd"><thead><tr><th>Heure</th><th>Lui</th><th>Toi</th><th>Déroulé (montants en bb)</th>'
            f'<th class="num">Lui (bb)</th></tr></thead><tbody>{"".join(rows)}</tbody></table></details>'
        )
    return "".join(out) or '<p class="muted">Aucune main montrée.</p>'


def build_report(hands: list[Hand], stats: dict[str, PlayerStats], hero: str, villain: str) -> str:
    v, h = stats[villain], stats[hero]
    games = Counter(hd.game_name for hd in hands)
    tables = len({hd.table_id for hd in hands})
    period = f"{hands[0].date:%d/%m/%Y %H:%M} → {hands[-1].date:%d/%m/%Y %H:%M} (UTC)"
    eff = median(v.eff_stacks_bb) if v.eff_stacks_bb else 0
    meta = (
        f"{len(hands)} mains · {period} · {tables} table(s) · "
        + ", ".join(f"{escape(g)} ({n})" for g, n in games.most_common())
        + f" · stack effectif médian {num(eff, 0)} bb"
    )
    checkpoints = "".join(
        f'<tr><td class="num">{i + 1}</td>' + "".join(f'<td class="num">{num(val, 0)}</td>' for val in h.curve[i]) + "</tr>"
        for i in sorted(set(list(range(99, len(h.curve), 100)) + [len(h.curve) - 1]))
    )
    return TEMPLATE.format(
        title=escape(f"Profil HU — {villain}"),
        villain=escape(villain),
        hero=escape(hero),
        meta=meta,
        tiles=tiles(h),
        legend=legend(h.curve),
        chart=chart_svg(h.curve),
        checkpoints=checkpoints,
        series_head="".join(f'<th class="num">{name}</th>' for name, _ in SERIES),
        villain_findings=findings_html(v),
        hero_findings=findings_html(h, hero_mode=True),
        duels=duels_html(v, h),
        stat_tables=stat_tables(v, h),
        preflop_sizes=preflop_sizes(v, h),
        sizing=sizing_html(hands, villain),
        timing=timing_html(v, h),
        responses_you=responses_html(hands, villain, hero, you_respond=True),
        responses_him=responses_html(hands, hero, villain, you_respond=False),
        allins=allin_html(hands, hero, villain),
        showdowns=showdowns_html(v, hero, villain),
        n_showdowns=len(v.showdowns),
        script=SCRIPT,
    )


TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100;
  --alert: #d03b3b; --alert-bg: rgba(208,59,59,0.08); --good: #006300;
  --hi-bg: rgba(235,104,52,0.14); --lo-bg: rgba(42,120,214,0.12);
  --sc: #0b0b0b; --sh: #d03b3b; --sd: #2a78d6; --sclub: #008300;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
    --alert: #e66767; --alert-bg: rgba(230,103,103,0.12); --good: #0ca30c;
    --hi-bg: rgba(217,89,38,0.22); --lo-bg: rgba(57,135,229,0.22);
    --sc: #ffffff; --sh: #e66767; --sd: #6da7ec; --sclub: #0ca30c;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
  --alert: #e66767; --alert-bg: rgba(230,103,103,0.12); --good: #0ca30c;
  --hi-bg: rgba(217,89,38,0.22); --lo-bg: rgba(57,135,229,0.22);
  --sc: #ffffff; --sh: #e66767; --sd: #6da7ec; --sclub: #0ca30c;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--page); color: var(--ink); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }}
h1 {{ font-size: 26px; margin: 0 0 4px; }}
h2 {{ font-size: 18px; margin: 36px 0 12px; }}
h3 {{ font-size: 14px; margin: 0 0 10px; color: var(--ink-2); }}
.meta, .muted, .note {{ color: var(--muted); }}
.note {{ font-size: 12px; margin: 8px 0 0; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; min-width: 0; }}
.grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 460px), 1fr)); gap: 16px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-top: 20px; }}
.tile {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
.tile .label {{ color: var(--ink-2); font-size: 13px; }}
.tile .value {{ font-size: 26px; font-weight: 600; margin: 2px 0; }}
.tile .sub {{ color: var(--muted); font-size: 12px; }}
table.stats {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
table.stats th {{ text-align: left; font-weight: 500; color: var(--muted); font-size: 12px; border-bottom: 1px solid var(--grid); padding: 4px 6px; }}
table.stats td {{ border-bottom: 1px solid var(--grid); padding: 5px 6px; vertical-align: top; }}
.num {{ text-align: right !important; white-space: nowrap; }}
.nowrap {{ white-space: nowrap; }}
td .n {{ display: block; font-size: 11px; color: var(--muted); }}
td .n.small {{ font-style: italic; }}
td.dev-haut {{ background: var(--hi-bg); }}
td.dev-bas {{ background: var(--lo-bg); }}
td.strong .v {{ font-weight: 700; }}
.legend-dev {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; color: var(--ink-2); margin: 0 0 12px; }}
.legend-dev i {{ display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -2px; margin-right: 6px; }}
.findings {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
.findings li {{ display: flex; gap: 10px; align-items: flex-start; }}
.pill {{ flex: none; font-size: 11px; border-radius: 999px; padding: 1px 8px; border: 1px solid var(--border); color: var(--ink-2); margin-top: 2px; }}
.pill.strong {{ color: var(--alert); border-color: var(--alert); font-weight: 600; }}
table.duel tr.alerte td:first-child {{ box-shadow: inset 3px 0 0 var(--alert); }}
table.duel tr.alerte .lvl {{ color: var(--alert); font-weight: 700; }}
table.duel tr.ok .lvl {{ color: var(--good); font-weight: 700; }}
.chart {{ position: relative; }}
.chart svg {{ width: 100%; height: auto; display: block; }}
.chart .grid {{ stroke: var(--grid); stroke-width: 1; }}
.chart .axis {{ stroke: var(--axis); stroke-width: 1; }}
.chart .tick {{ fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }}
.chart .line {{ fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
.chart .cross {{ stroke: var(--axis); stroke-width: 1; }}
.chart .hit {{ fill: transparent; cursor: crosshair; }}
.tooltip {{ position: absolute; top: 8px; pointer-events: none; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); min-width: 170px; }}
.tooltip .row {{ display: flex; align-items: center; gap: 8px; }}
.tooltip .row i {{ width: 14px; height: 2px; display: inline-block; }}
.tooltip .row b {{ margin-left: auto; font-variant-numeric: tabular-nums; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--ink-2); margin-bottom: 8px; }}
.legend i {{ display: inline-block; width: 16px; height: 2px; vertical-align: middle; margin-right: 6px; }}
.legend b {{ color: var(--ink); font-weight: 600; }}
.barcell {{ width: 34%; position: relative; }}
.bar {{ display: inline-block; height: 8px; background: var(--series-1); border-radius: 0 4px 4px 0; vertical-align: middle; max-width: calc(100% - 44px); }}
.barv {{ margin-left: 6px; color: var(--ink-2); font-size: 12px; }}
.chip {{ display: inline-block; font-size: 11px; border-radius: 4px; padding: 0 6px; margin: 1px 2px 1px 0; border: 1px solid var(--border); white-space: nowrap; }}
.chip.k0 {{ background: var(--hi-bg); }}
.chip.k1 {{ background: var(--hi-bg); }}
.chip.k2 {{ background: transparent; }}
.chip.k3, .chip.k4 {{ background: var(--lo-bg); }}
span.pc {{ display: inline-block; font-weight: 600; font-family: ui-monospace, monospace; padding: 0 2px; margin-right: 1px; border-radius: 3px; background: var(--page); border: 1px solid var(--border); font-size: 12px; }}
span.pc.ss {{ color: var(--sc); }} span.pc.sh {{ color: var(--sh); }} span.pc.sd {{ color: var(--sd); }} span.pc.sc {{ color: var(--sclub); }}
details {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; }}
summary {{ cursor: pointer; padding: 12px 16px; }}
details[open] summary {{ border-bottom: 1px solid var(--grid); }}
details table {{ margin: 0; }}
details .stats td, details .stats th {{ padding-left: 12px; }}
.combos {{ font-size: 12px; color: var(--ink-2); margin-top: 2px; }}
.sd .line {{ font-size: 12px; }}
.st {{ margin-bottom: 2px; }}
.sn {{ display: inline-block; width: 22px; color: var(--muted); font-weight: 600; }}
.tag {{ font-weight: 700; }} .tag.tv {{ color: var(--series-2); }} .tag.th {{ color: var(--series-1); }}
.t {{ color: var(--muted); font-size: 11px; }}
em {{ font-style: normal; font-weight: 600; color: var(--alert); }}
.scroll {{ overflow-x: auto; }}
.sd td {{ white-space: normal; }}
.act, .cards {{ white-space: nowrap; }}
.chart-wrap {{ overflow-x: auto; }}
.chart {{ min-width: 600px; }}
table.sizing td:first-child {{ white-space: nowrap; }}
@media (max-width: 640px) {{
  .tile .value {{ font-size: 22px; }}
  .barcell {{ width: auto; }}
  table.sd thead {{ display: none; }}
  table.sd tr {{ display: grid; grid-template-columns: auto auto 1fr auto; gap: 0 12px; border-bottom: 1px solid var(--grid); padding: 8px 0; }}
  table.sd td {{ border: 0; padding: 2px 12px; }}
  table.sd td.line {{ grid-column: 1 / -1; grid-row: 2; }}
  .act {{ white-space: normal; }}
}}
</style>
</head>
<body>
<main>
<h1>{villain}</h1>
<div class="meta">Profil Heads-Up · toi : <b>{hero}</b> · {meta}</div>

{tiles}

<h2>Résultat cumulé (bb)</h2>
<div class="card">
  {legend}
  {chart}
  <details style="margin:12px 0 0"><summary>Voir les données</summary>
  <div class="scroll"><table class="stats"><thead><tr><th class="num">Main</th>{series_head}</tr></thead><tbody>{checkpoints}</tbody></table></div>
  </details>
  <p class="note">« EV all-in » remplace le résultat réel des all-in payés avant la river par ton espérance (équité × pot) : l'écart avec le résultat réel mesure la chance.</p>
</div>

<h2>Lecture de l'adversaire</h2>
<div class="grid2">
  <div class="card"><h3>Ses tendances exploitables</h3>{villain_findings}</div>
  <div class="card"><h3>Tes écarts dans ce match</h3>{hero_findings}</div>
</div>

<h2>Le duel : ses attaques, tes réponses</h2>
<div class="card scroll">{duels}</div>

<h2>Tes réponses à ses mises, selon leur taille</h2>
<div class="card scroll">{responses_you}
<p class="note">« Seuil bluff » = mise / (pot + mise) : fréquence de fold au-delà de laquelle un bluff de cette taille (médiane de la tranche) gagne avec n'importe quelles cartes. Une ligne est signalée quand le fold dépasse ce seuil de plus de 8 points. Repère strict à la river ; au flop et au turn un bluff garde de l'équité, c'est donc indicatif.</p></div>
<details style="margin-top:12px"><summary>Et ses réponses à tes mises</summary><div class="scroll" style="padding:0 16px 16px">{responses_him}</div></details>

<h2>Statistiques</h2>
<div class="legend-dev"><span><i style="background:var(--hi-bg)"></i>au-dessus du repère</span><span><i style="background:var(--lo-bg)"></i>en dessous du repère</span><span><b>gras</b> = écart net (intervalle de confiance à 90&nbsp;% hors repère)</span><span>chiffres en italique = moins de 15 occasions</span></div>
{stat_tables}

<h2>Sizings</h2>
<div class="card scroll" style="margin-bottom:16px"><h3>Préflop</h3>{preflop_sizes}</div>
{sizing}
<p class="note">Force de sa main <b>au moment de la mise</b> (orange = rien ou tirage, blanc = paire moyenne/faible, bleu = top paire et mieux). Seuls les coups allés à l'abattage sont visibles : ses bluffs qui t'ont fait folder n'apparaissent jamais. Survole une ligne pour voir le détail des mains.</p>

<h2>Timing</h2>
<div class="card scroll">{timing}<p class="note">Temps mesuré depuis l'action précédente (horodatage à la seconde ; inclut l'animation de distribution en début de street).</p></div>

<h2>All-in</h2>
<div class="card scroll">{allins}</div>

<h2>Ses mains à l'abattage ({n_showdowns})</h2>
<p class="muted">Groupées par sa ligne préflop. H = toi, V = lui ; le temps de réflexion de l'adversaire est indiqué après chacune de ses actions.</p>
{showdowns}

<p class="note">Repères indicatifs pour un régulier HU solide à 100bb+ : ils servent à repérer les écarts, pas à définir une stratégie optimale. Rapport généré par Analyzer.</p>
</main>
<script>{script}</script>
</body>
</html>
"""

SCRIPT = """
document.querySelectorAll('.chart').forEach(function (chart) {
  var pts = JSON.parse(chart.dataset.points);
  var svg = chart.querySelector('svg'), hit = chart.querySelector('.hit');
  var cross = chart.querySelector('.cross'), tip = chart.querySelector('.tooltip');
  var x0 = +chart.dataset.x0, x1 = +chart.dataset.x1;
  var names = ['Résultat réel', 'EV all-in', "À l'abattage", 'Sans abattage'];
  var vars = ['--series-1', '--series-2', '--series-3', '--series-4'];
  function fmt(v) { return (v > 0 ? '+' : '') + v.toFixed(0).replace('-', '\\u2212') + ' bb'; }
  function show(i) {
    var x = x0 + (x1 - x0) * i / Math.max(pts.length - 1, 1);
    cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
    tip.textContent = '';
    var head = document.createElement('div'); head.textContent = 'Main ' + (i + 1); head.style.color = 'var(--muted)';
    tip.appendChild(head);
    pts[i].forEach(function (v, s) {
      var row = document.createElement('div'); row.className = 'row';
      var key = document.createElement('i'); key.style.background = 'var(' + vars[s] + ')';
      var label = document.createElement('span'); label.textContent = names[s];
      var val = document.createElement('b'); val.textContent = fmt(v);
      row.appendChild(key); row.appendChild(label); row.appendChild(val); tip.appendChild(row);
    });
    tip.hidden = false;
    var rect = svg.getBoundingClientRect(), px = rect.width * x / svg.viewBox.baseVal.width;
    tip.style.left = Math.min(Math.max(px + 12, 0), rect.width - tip.offsetWidth) + 'px';
  }
  function index(evt) {
    var rect = svg.getBoundingClientRect();
    var x = (evt.clientX - rect.left) * svg.viewBox.baseVal.width / rect.width;
    return Math.round(Math.min(Math.max((x - x0) / (x1 - x0), 0), 1) * (pts.length - 1));
  }
  hit.addEventListener('pointermove', function (e) { show(index(e)); });
  hit.addEventListener('pointerleave', function () { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); });
  svg.setAttribute('tabindex', '0');
  var cur = pts.length - 1;
  svg.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowLeft') cur = Math.max(cur - 10, 0); else if (e.key === 'ArrowRight') cur = Math.min(cur + 10, pts.length - 1); else return;
    show(cur); e.preventDefault();
  });
  svg.addEventListener('blur', function () { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); });
});
"""
