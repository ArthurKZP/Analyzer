"""Rapport HTML autonome (un seul fichier, sans dépendance) pour un adversaire."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from html import escape
from statistics import median
from typing import Optional
from urllib.parse import quote

from .cards import describe_holding, equity
from .insights import (
    DUELS,
    FOLD_STAT_STREET,
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
from .lines import (
    HOLDING_LABELS,
    HOLDINGS,
    INTENT_LABELS,
    INTENTS,
    Line,
    check_ranges,
    fold_holdings_by_street,
    nonshowdown_losses,
    passive_showdowns,
    pooled_by_size,
    think_by_intent,
    verdict,
    villain_lines,
)
from .models import CALL, FOLD, POSTFLOP, RAISE, STREETS, VOLUNTARY, Hand
from .plan import Plan, build_plan
from .spots import line_tag
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


CHART_POINTS = 1500  # au-delà, la courbe garde les creux et les sommets de chaque tranche de mains


def chart_sample(curve: list[tuple[float, ...]], limit: int = CHART_POINTS) -> list[int]:
    """Les mains à dessiner : toutes, ou pour une longue série, par tranche, la première, la dernière, et les creux et
    sommets du résultat réel et de l'EV (la forme de la courbe ne change pas, la page reste légère)."""
    n = len(curve)
    if n <= limit:
        return list(range(n))
    buckets = limit // 6
    keep = {0, n - 1}
    for b in range(buckets):
        lo, hi = b * n // buckets, min((b + 1) * n // buckets, n)
        if lo >= hi:
            continue
        keep.update((lo, hi - 1))
        for series in (0, 1):
            seg = range(lo, hi)
            keep.add(min(seg, key=lambda i: curve[i][series]))
            keep.add(max(seg, key=lambda i: curve[i][series]))
    return sorted(keep)


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

    grid, xticks = [], []
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
        xticks.append(f'<text class="tick" x="{x(i):.1f}" y="{height - 10}" text-anchor="middle">{i}</text>')
    lines = []
    kept = chart_sample(curve)
    for s, (_, var) in enumerate(SERIES):
        pts = " ".join(f"{x(i):.1f},{y(curve[i][s]):.1f}" for i in kept)
        lines.append(f'<polyline class="line" data-series="{s}" style="stroke:var({var})" points="{pts}"/>')
    data = json.dumps([[round(v, 1) for v in curve[i]] for i in kept])
    sampled = f" data-idx='{json.dumps(kept)}' data-n=\"{n}\"" if len(kept) < n else ""
    # Le script redessine l'axe vertical et les courbes quand on en masque (cases de la légende).
    return f"""
<div class="chart-wrap"><div class="chart" data-points='{data}'{sampled} data-x0="{pad_l}" data-x1="{width - pad_r}"
  data-y0="{pad_t}" data-y1="{height - pad_b}">
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="Résultat cumulé en big blinds, main par main">
    <g class="yaxis">{''.join(grid)}</g>{''.join(xticks)}
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
    """Légende du graphique : une case par courbe, pour la masquer ou l'afficher."""
    last = curve[-1] if curve else (0, 0, 0, 0)
    return '<div class="legend">' + "".join(
        f'<label><input type="checkbox" checked data-series="{i}"><i style="background:var({var})"></i>{name} '
        f'<b>{num(last[i], 0, sign=True)}&nbsp;bb</b></label>'
        for i, (name, var) in enumerate(SERIES)
    ) + "</div>"


def findings_html(ps: PlayerStats, hero_mode: bool = False, folds: Optional[dict] = None,
                  items: Optional[list] = None) -> str:
    """Les écarts aux repères (items : ceux à montrer, dans l'ordre ; par défaut les 12 plus marqués)."""
    items = findings(ps)[:12] if items is None else items
    if not items:
        return '<p class="muted">Aucun écart marqué par rapport aux repères (ou échantillon trop faible).</p>'
    rows = []
    for f in items:
        lo, hi = f.stat.ref
        badge = '<span class="pill strong">net</span>' if f.strong else '<span class="pill">tendance</span>'
        arrow = "▲" if f.direction == "haut" else "▼"
        if hero_mode:
            text = f"{f.reading.fix[0].upper()}{f.reading.fix[1:]}."
            street = FOLD_STAT_STREET.get(f.stat.key)
            if street and folds and sum(folds[street].values()):
                c = folds[street]
                text += (f" Tes folds {street} face à ses mises : {c['rien']} sans rien, {c['tirage']} avec un tirage, "
                         f"{c['paire'] + c['fort']} avec une paire ou mieux.")
        else:
            text = f"{f.reading.fact} → {f.reading.exploit}."
        rows.append(
            f"<li>{badge}<div><b>{escape(f.stat.label)} {arrow} {num(f.ratio.pct, 0)}&nbsp;%</b>"
            f' <span class="muted">({f.ratio.hits}/{f.ratio.opps} · repère {lo:.0f}–{hi:.0f}&nbsp;%)</span>'
            f"<br>{escape(text)}</div></li>"
        )
    return '<ul class="findings">' + "".join(rows) + "</ul>"


def duels_html(villain: PlayerStats, hero: PlayerStats, folds: Optional[dict] = None) -> str:
    rows = []
    for d in DUELS:
        v, h = combined(villain, d.villain_key), combined(hero, d.hero_key)
        level, text = duel_verdict(d, v, h, folds[d.street] if folds and d.street else None)
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


BLUFF_MARGIN = 8  # points de % autour du seuil avant de signaler


def bluffs_html(hands: list[Hand], hero: str, villain: str) -> str:
    """Ses réponses à tes mises : un bluff pur ne dépend que de sa fréquence de fold."""
    table = responses_by_size(hands, hero, villain)
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
            verdict_text, cls = "", ""
            if n >= 8 and fold > threshold + BLUFF_MARGIN:
                verdict_text, cls = "tes bluffs purs gagnent", "dev-bas strong"
            elif n >= 8 and fold < threshold - BLUFF_MARGIN:
                verdict_text, cls = "tes bluffs purs perdent : mise pour la value", "dev-haut strong"
            rows.append(
                f'<tr><td>{street.capitalize()}</td><td class="nowrap">{bucket}</td><td class="num">{n}</td>'
                f'<td class="num {cls}"><span class="v">{num(fold, 0)}&nbsp;%</span></td>'
                f'<td class="num">{num(100.0 * c[CALL] / n, 0)}&nbsp;%</td><td class="num">{num(100.0 * c[RAISE] / n, 0)}&nbsp;%</td>'
                f'<td class="num muted">{num(threshold, 0)}&nbsp;%</td><td>{verdict_text}</td></tr>'
            )
    if not rows:
        return '<p class="muted">Pas de mise postflop.</p>'
    return (
        '<table class="stats"><thead><tr><th>Street</th><th>Ta mise</th><th class="num">Nb</th><th class="num">Il folde</th>'
        '<th class="num">Il paie</th><th class="num">Il relance</th><th class="num">Seuil</th><th></th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


# --- Lignes value / bluff ------------------------------------------------------------

KIND_LABELS = {
    "value": "Ligne de value",
    "bluff": "Ligne de bluff",
    "semi-bluff": "Semi-bluffs (tirages)",
    "mixte": "Mixte",
    "peu vue": "Trop peu vue",
    "inconnue": "Jamais vue",
}


PASSIVE_LABELS = {"bluff": "Rien", "semi": "Tirage", "thin": "Value fine", "value": "Value"}


def composition_html(counts: Counter, total: int, labels: dict = INTENT_LABELS) -> str:
    if not total:
        return '<span class="muted">—</span>'
    segments = "".join(
        f'<span class="seg i-{k}" style="flex:{counts[k]}" title="{labels[k]} : {counts[k]}"></span>'
        for k in INTENTS if counts.get(k)
    )
    text = " · ".join(f"{counts[k]}&nbsp;{labels[k].lower()}" for k in INTENTS if counts.get(k))
    return f'<div class="comp">{segments}</div><div class="comp-t">{text}</div>'


def holdings_html(counts: Counter) -> str:
    if not sum(counts.values()):
        return '<span class="muted">—</span>'
    return " ".join(
        f'<span class="chip h-{k}">{HOLDING_LABELS[k]}&nbsp;{counts[k]}</span>' for k in HOLDINGS if counts.get(k)
    )


def spots_link(spots_href: str, fragment: str, text: str = "voir les mains →") -> str:
    if not spots_href:
        return ""
    return f'<a class="spots-link" href="{escape(spots_href)}#{escape(fragment)}">{text}</a>'


def line_rows(ln: Line, css: str = "", spots_href: str = "") -> str:
    v = verdict(ln)
    faced = sum(ln.replies.values())
    replies = (
        f'<span class="v">{num(100 * ln.fold_rate, 0)}&nbsp;% fold</span>'
        f'<span class="n">{ln.replies[FOLD]} fold · {ln.replies[CALL]} call · {ln.replies[RAISE]} raise</span>'
        if faced else '<span class="muted">—</span>'
    )
    conf = f' <span class="muted">({v.confidence})</span>' if v.confidence else ""
    size = f'<span class="n">{escape(ln.size)}</span>' if ln.size else ""
    return (
        f'<tr class="main {css}"><td class="ln"><b>{escape(ln.label)}</b>{size}</td><td class="num" data-l="Fois">{ln.count}</td>'
        f'<td data-l="Ta réponse">{replies}</td><td data-l="Tes folds, avec…">{holdings_html(ln.fold_holdings)}</td>'
        f'<td class="num" data-l="Vues">{len(ln.seen)}</td>'
        f'<td class="compcell" data-l="Ce qu\'il montre">{composition_html(ln.intents, len(ln.seen))}</td>'
        f'<td data-l="Lecture"><span class="kind k-{v.kind.replace(" ", "-")}">{KIND_LABELS[v.kind]}</span>{conf}</td></tr>'
        f'<tr class="sub {css}"><td colspan="7">{escape(v.reading)}. {escape(v.decision)} '
        f'{spots_link(spots_href, "l=" + quote(line_tag("V", ln.street, ln.label, ln.size), safe=""))}</td></tr>'
    )


def lines_html(lines: list[Line], hero: str, villain: str, min_count: int = 3, spots_href: str = "") -> str:
    blocks = []
    pooled = pooled_by_size(lines)
    for street in POSTFLOP:
        rows, seen_rows = [], []
        street_lines = [ln for ln in lines if ln.street == street]
        rare = [ln for ln in street_lines if ln.count < min_count]
        for ln in street_lines:
            if ln.count < min_count:
                continue
            rows.append(line_rows(ln, spots_href=spots_href))
            for s_ in sorted(ln.seen, key=lambda x: x.hand.date)[-MAX_ROWS:]:
                h = s_.hand
                seen_rows.append(
                    f'<tr><td class="nowrap">{h.date:%H:%M}</td><td>{escape(ln.name)}</td>'
                    f"<td>{cards_html(h.board[: {'flop': 3, 'turn': 4, 'river': 5}[street]])}</td>"
                    f'<td>{cards_html(h.hole_cards[villain])}<span class="n">{escape(s_.description)}</span></td>'
                    f'<td class="nowrap"><i class="dot i-{s_.intent}"></i>{INTENT_LABELS[s_.intent]}</td>'
                    f'<td class="num">{num(100 * s_.equity, 0)}&nbsp;%</td>'
                    f'<td>{cards_html(h.hole_cards[hero])}<span class="n">{escape(s_.hero_description)}</span></td>'
                    f'<td>{s_.hero_reply or "—"}</td></tr>'
                )
        if not rows:
            continue
        pooled_rows = "".join(line_rows(ln, "pooled", spots_href) for ln in pooled if ln.street == street)
        rows.append(f'<tr class="group"><td colspan="7">Toutes lignes confondues, par taille</td></tr>{pooled_rows}')
        rare_note = (f'<p class="note">{len(rare)} autre(s) ligne(s) vue(s) moins de {min_count} fois '
                     f'({sum(ln.count for ln in rare)} mises).</p>') if rare else ""
        seen_block = (
            f'<details class="inner"><summary>Voir ses {len(seen_rows)} mise(s) {street} montrée(s) à l\'abattage</summary>'
            '<div class="scroll"><table class="stats"><thead><tr><th>Heure</th><th>Ligne</th><th>Board</th><th>Lui</th>'
            '<th>Intention</th><th class="num">Son équité vs toi</th><th>Toi</th><th>Ta réponse</th></tr></thead>'
            f'<tbody>{"".join(seen_rows)}</tbody></table></div></details>'
        ) if seen_rows else ""
        blocks.append(
            f'<div class="card lines-card"><h3>{street.capitalize()}</h3><div class="scroll"><table class="stats lines"><thead><tr>'
            '<th>Sa ligne</th><th class="num">Fois</th><th>Ta réponse</th><th>Tes folds, avec…</th>'
            '<th class="num">Vues</th><th>Ce qu\'il montre</th><th>Lecture</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>{rare_note}{seen_block}</div>'
        )
    return "".join(blocks) or '<p class="muted">Aucune mise postflop.</p>'


def passive_html(hands: list[Hand], hero: str, villain: str) -> str:
    data = passive_showdowns(hands, villain, hero)
    rows = []
    for street in POSTFLOP:
        c = data.get(street)
        if c:
            rows.append(f"<tr><td>{street.capitalize()}</td><td class=\"num\">{sum(c.values())}</td>"
                        f'<td class="compcell">{composition_html(c, sum(c.values()), PASSIVE_LABELS)}</td></tr>')
    if not rows:
        return ""
    return (
        '<div class="card"><h3>Ce qu\'il montre quand il checke</h3><table class="stats"><thead><tr><th>Street</th>'
        '<th class="num">Mains</th><th>Sa main au moment du check</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table><p class="note">Plus il checke de value, moins ses mises en contiennent. '
        "Beaucoup de « rien » checkés à la river = il n'y bluffe pas tout son air.</p></div>"
    )


def intent_timing_html(lines: list[Line]) -> str:
    times = think_by_intent(lines)
    if not times:
        return ""
    cells = "".join(
        f'<tr><td class="nowrap"><i class="dot i-{k}"></i>{INTENT_LABELS[k]}</td>'
        f'<td class="num">{num(median(times[k]), 1)}&nbsp;s<span class="n">{len(times[k])}</span></td></tr>'
        for k in INTENTS if times.get(k)
    )
    return (
        '<table class="stats"><thead><tr><th>Ses mises montrées</th><th class="num">Temps médian</th></tr></thead>'
        f"<tbody>{cells}</tbody></table>"
    )


def nonshowdown_html(hands: list[Hand], hero: str, villain: Optional[str] = None) -> str:
    data = nonshowdown_losses(hands, hero)
    street_names = {"preflop": "Préflop", "flop": "Flop", "turn": "Turn", "river": "River"}
    rows = []
    for street, cell in data["by_street"].items():
        (hf, hbb), (vf, vbb) = cell["hero_folds"], cell["villain_folds"]
        rows.append(
            f'<tr><td>{street_names[street]}</td><td class="num">{hf}</td><td class="num">{num(hbb, 0, sign=True)}</td>'
            f'<td class="num">{vf}</td><td class="num">{num(vbb, 0, sign=True)}</td>'
            f'<td class="num"><b>{num(hbb + vbb, 0, sign=True)}</b></td></tr>'
        )
    story_rows = "".join(
        f'<tr><td>{escape(story)}</td><td class="num">{n}</td><td class="num">{num(total, 0, sign=True)}</td>'
        f"<td>{holdings_html(held)}</td></tr>"
        for story, (n, total, held) in data["stories"][:8]
    )
    checks = check_ranges(hands, hero, villain)
    check_rows = "".join(
        f'<tr><td>{street.capitalize()}</td><td class="num">{sum(c["holdings"].values())}</td>'
        f'<td>{holdings_html(c["holdings"])}</td>'
        f'<td class="num">{num(100 * c["bets"] / c["faced"], 0) + "&nbsp;%" if c["faced"] else "–"}'
        f'<span class="n">{c["bets"]}/{c["faced"]}</span></td></tr>'
        for street, c in checks.items() if sum(c["holdings"].values())
    )
    return f"""
<div class="grid2">
  <div class="card"><h3>Mains finies sans abattage</h3><div class="scroll"><table class="stats"><thead><tr><th>Fin</th>
    <th class="num">Tu folds</th><th class="num">bb</th><th class="num">Il folde</th><th class="num">bb</th><th class="num">Net</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div></div>
  <div class="card"><h3>Ce que tu as quand tu checkes en premier</h3><div class="scroll"><table class="stats"><thead><tr><th>Street</th>
    <th class="num">Checks</th><th>Ta main</th><th class="num">Il mise derrière</th></tr></thead><tbody>{check_rows}</tbody></table></div>
    <p class="note">« Il mise derrière » ne compte que les coups où il parle après ton check.</p></div>
</div>
<div class="card scroll" style="margin-top:16px"><h3>Tes folds turn et river les plus coûteux, par déroulé</h3>
  <table class="stats"><thead><tr><th>Déroulé</th><th class="num">Fois</th><th class="num">bb</th><th>Ta main au fold</th></tr></thead>
  <tbody>{story_rows}</tbody></table></div>"""


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


MAX_ROWS = 40  # mains montrées par tableau (les plus récentes) ; toutes restent dans Spots


def showdowns_html(villain_stats: PlayerStats, hero: str, villain: str, spots_href: str = "") -> str:
    groups: dict[str, list] = defaultdict(list)
    for entry in villain_stats.showdowns:
        groups[f"{entry.position} · {entry.preflop_line}"].append(entry)
    tags = {hero: "H", villain: "V"}
    out = []
    for name, entries in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        combos = Counter(e.combo for e in entries)
        combo_list = ", ".join(f"{c}{'×' + str(n) if n > 1 else ''}" for c, n in combos.most_common())
        rows = []
        recent = sorted(entries, key=lambda e: e.hand.date)[-MAX_ROWS:]
        if len(recent) < len(entries):
            rows.append(f'<tr><td colspan="5" class="muted">Les {MAX_ROWS} plus récentes ; toutes dans '
                        f'{spots_link(spots_href, "known=1", "Spots") or "Spots"}.</td></tr>')
        for e in recent:
            h = e.hand
            line = "".join(
                f'<div class="st"><span class="sn">{street[0].upper() if street != "preflop" else "PF"}</span>{html}</div>'
                for street, html in hand_line(h, tags, villain)
            )
            rows.append(
                f'<tr><td class="nowrap">{h.date:%H:%M}<br><span class="muted">{e.position}</span>'
                f'<br>{spots_link(spots_href, "hand=" + quote(h.hand_id, safe=""), "rejouer")}</td>'
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


CONFIDENCE_HINT = {
    "solide": "l'écart reste vrai même en tenant compte du hasard",
    "indicatif": "tendance nette, échantillon modeste",
    "à confirmer": "peu de mains, à vérifier sur les prochaines sessions",
}


def plan_html(plan: Plan) -> str:
    cards = []
    for phase, items in plan.by_phase().items():
        if not items:
            continue
        lis = "".join(
            f'<li><div class="pa">{escape(it.action)}</div>'
            f'<div class="pw">{escape(it.why)} <span class="conf c-{it.confidence.replace(" ", "-")}" '
            f'title="{CONFIDENCE_HINT[it.confidence]}">{it.confidence}</span></div></li>'
            for it in items
        )
        cards.append(f'<div class="card phase"><h3>{phase}</h3><ol>{lis}</ol></div>')
    if not cards:
        return '<p class="muted">Pas encore assez de mains pour proposer un plan.</p>'
    return (
        f'<div class="card profile"><b>Profil :</b> {escape(plan.profile)}</div>'
        f'<div class="grid2 plan">{"".join(cards)}</div>'
    )


def build_report(hands: list[Hand], stats: dict[str, PlayerStats], hero: str, villain: str,
                 spots_href: str = "", embed: bool = False, lines: Optional[list[Line]] = None,
                 preflop_href: str = "") -> str:
    """Rapport complet. embed=True : version intégrée à l'application (sans titre)."""
    v, h = stats[villain], stats[hero]
    lines = lines if lines is not None else villain_lines(hands, villain, hero)
    folds = fold_holdings_by_street(lines)
    plan = build_plan(hands, stats, hero, villain, lines)
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
        style=STYLE,
        heading="" if embed else f"<h1>{escape(villain)}</h1>",
        hero=escape(hero),
        meta=meta,
        tiles=tiles(h),
        plan=plan_html(plan),
        legend=legend(h.curve),
        chart=chart_svg(h.curve),
        checkpoints=checkpoints,
        series_head="".join(f'<th class="num">{name}</th>' for name, _ in SERIES),
        villain_findings=findings_html(v),
        hero_findings=findings_html(h, hero_mode=True, folds=folds),
        duels=duels_html(v, h, folds),
        lines=lines_html(lines, hero, villain, spots_href=spots_href),
        passive=passive_html(hands, hero, villain),
        intent_timing=intent_timing_html(lines),
        nonshowdown=nonshowdown_html(hands, hero, villain),
        stat_tables=stat_tables(v, h),
        preflop_sizes=preflop_sizes(v, h),
        sizing=sizing_html(hands, villain),
        timing=timing_html(v, h),
        bluffs=bluffs_html(hands, hero, villain),
        allins=allin_html(hands, hero, villain),
        showdowns=showdowns_html(v, hero, villain, spots_href),
        spots=(f' · <a href="{escape(spots_href)}">Visualiseur de spots</a>' if spots_href and not embed else "")
        + (f' · <a href="{escape(preflop_href)}">Préflop vs solveur</a>' if preflop_href and not embed else ""),
        n_showdowns=len(v.showdowns),
        script=SCRIPT + (EMBED_SCRIPT if embed else ""),
    )


# Signale à l'application la page affichée (utile quand on suit un lien interne).
EMBED_SCRIPT = """
(function () {
  if (window.parent === window) return;
  var post = function () {
    window.parent.postMessage({ type: 'analyzer-page', path: location.pathname, search: location.search,
      hash: location.hash }, location.origin);
  };
  post();
  window.addEventListener('hashchange', post);
})();
"""

PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{style}</style>
</head>
<body>
<main>
{body}
</main>
<script>{script}</script>
</body>
</html>
"""


def html_page(title: str, body: str, embed: bool = False, script: str = "") -> str:
    """Page autonome avec le style du rapport (utilisée par l'application)."""
    return PAGE.format(title=escape(title), style=STYLE, body=body,
                       script=script + (EMBED_SCRIPT if embed else ""))


FORMAT_NAMES = {"HU": "Heads-up", "ring": "Tables à plusieurs"}


def format_query(table_format: str) -> str:
    """« ?format=ring » : la page des tables à plusieurs (rien pour le heads-up)."""
    return "" if table_format == "HU" else f"?format={quote(table_format)}"


def format_switch(formats: list[tuple[str, int]], current: str) -> str:
    """Le choix entre le heads-up et les tables à plusieurs, avec leur nombre de mains (rien s'il n'y en a qu'un)."""
    if len(formats) < 2:
        return ""
    links = "".join(
        f'<a href="{escape(format_query(fmt) or "?format=HU")}" aria-current="{"page" if fmt == current else "false"}">'
        f'{escape(FORMAT_NAMES.get(fmt, fmt))} <span>{n}</span></a>' for fmt, n in formats)
    return f'<nav class="lk-fmt" aria-label="Format de table">{links}</nav>'


def build_plan_page(hands: list[Hand], stats: dict[str, PlayerStats], hero: str, villain: str,
                    embed: bool = False, lines: Optional[list[Line]] = None,
                    report_href: str = "rapport", spots_href: str = "spots") -> str:
    """Page d'accueil d'un adversaire : chiffres clés et plan de jeu."""
    h = stats[hero]
    lines = lines if lines is not None else villain_lines(hands, villain, hero)
    plan = build_plan(hands, stats, hero, villain, lines)
    period = f"{hands[0].date:%d/%m/%Y} → {hands[-1].date:%d/%m/%Y}"
    links = "" if embed else f' · <a href="{escape(report_href)}">Rapport complet</a> · <a href="{escape(spots_href)}">Spots</a>'
    heading = "" if embed else f"<h1>{escape(villain)}</h1>"
    body = (
        f'{heading}<div class="meta">{len(hands)} mains · {period} · toi : <b>{escape(hero)}</b>{links}</div>'
        f"{tiles(h)}<h2>Plan de jeu</h2>{plan_html(plan)}"
        '<p class="note">Consignes générées à partir de l\'analyse. Confiance : <b>solide</b> = l\'écart reste vrai même '
        'en tenant compte du hasard, <b>indicatif</b> = tendance nette sur un échantillon modeste, '
        '<b>à confirmer</b> = peu de mains. Le détail de chaque chiffre est dans le rapport complet.</p>'
    )
    return html_page(f"Plan — {villain}", body, embed)


STYLE = """:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100;
  --alert: #d03b3b; --alert-bg: rgba(208,59,59,0.08); --good: #006300;
  --hi-bg: rgba(235,104,52,0.14); --lo-bg: rgba(42,120,214,0.12);
  --sc: #0b0b0b; --sh: #d03b3b; --sd: #2a78d6; --sclub: #008300;
  --int-bluff: #c8302f; --int-semi: #eb8a89; --int-thin: #86b6ef; --int-value: #2a78d6;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
    --alert: #e66767; --alert-bg: rgba(230,103,103,0.12); --good: #0ca30c;
    --hi-bg: rgba(217,89,38,0.22); --lo-bg: rgba(57,135,229,0.22);
    --sc: #ffffff; --sh: #e66767; --sd: #6da7ec; --sclub: #0ca30c;
    --int-bluff: #e66767; --int-semi: #9c3434; --int-thin: #1c5cab; --int-value: #5598e7;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
  --alert: #e66767; --alert-bg: rgba(230,103,103,0.12); --good: #0ca30c;
  --hi-bg: rgba(217,89,38,0.22); --lo-bg: rgba(57,135,229,0.22);
  --sc: #ffffff; --sh: #e66767; --sd: #6da7ec; --sclub: #0ca30c;
  --int-bluff: #e66767; --int-semi: #9c3434; --int-thin: #1c5cab; --int-value: #5598e7;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 36px 0 12px; }
h3 { font-size: 14px; margin: 0 0 10px; color: var(--ink-2); }
.meta, .muted, .note { color: var(--muted); }
.note { font-size: 12px; margin: 8px 0 0; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; min-width: 0; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 460px), 1fr)); gap: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-top: 20px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.tile .label { color: var(--ink-2); font-size: 13px; }
.tile .value { font-size: 26px; font-weight: 600; margin: 2px 0; }
.tile .sub { color: var(--muted); font-size: 12px; }
table.stats { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
table.stats th { text-align: left; font-weight: 500; color: var(--muted); font-size: 12px; border-bottom: 1px solid var(--grid); padding: 4px 6px; }
table.stats td { border-bottom: 1px solid var(--grid); padding: 5px 6px; vertical-align: top; }
.num { text-align: right !important; white-space: nowrap; }
.nowrap { white-space: nowrap; }
td .n { display: block; font-size: 11px; color: var(--muted); }
td .n.small { font-style: italic; }
td.dev-haut { background: var(--hi-bg); }
td.dev-bas { background: var(--lo-bg); }
td.strong .v { font-weight: 700; }
.lk-fmt { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 12px; }
.lk-fmt a { font-size: 13px; padding: 4px 12px; border-radius: 999px; border: 1px solid var(--border); text-decoration: none;
  color: var(--ink-2); background: var(--surface); }
.lk-fmt a span { color: var(--muted); font-size: 12px; margin-left: 2px; }
.lk-fmt a[aria-current="page"] { background: var(--ink); color: var(--page); border-color: var(--ink); font-weight: 600; }
.lk-fmt a[aria-current="page"] span { color: var(--page); opacity: .75; }
.legend-dev { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; color: var(--ink-2); margin: 0 0 12px; }
.legend-dev i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -2px; margin-right: 6px; }
.findings { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
.findings li { display: flex; gap: 10px; align-items: flex-start; }
.pill { flex: none; font-size: 11px; border-radius: 999px; padding: 1px 8px; border: 1px solid var(--border); color: var(--ink-2); margin-top: 2px; }
.pill.strong { color: var(--alert); border-color: var(--alert); font-weight: 600; }
table.duel tr.alerte td:first-child { box-shadow: inset 3px 0 0 var(--alert); }
table.duel tr.alerte .lvl { color: var(--alert); font-weight: 700; }
table.duel tr.ok .lvl { color: var(--good); font-weight: 700; }
.chart { position: relative; }
.chart svg { width: 100%; height: auto; display: block; }
.chart .grid { stroke: var(--grid); stroke-width: 1; }
.chart .axis { stroke: var(--axis); stroke-width: 1; }
.chart .tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.chart .line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.chart .cross { stroke: var(--axis); stroke-width: 1; }
.chart .hit { fill: transparent; cursor: crosshair; }
.tooltip { position: absolute; top: 8px; pointer-events: none; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); min-width: 170px; }
.tooltip .row { display: flex; align-items: center; gap: 8px; }
.tooltip .row i { width: 14px; height: 2px; display: inline-block; }
.tooltip .row b { margin-left: auto; font-variant-numeric: tabular-nums; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--ink-2); margin-bottom: 8px; }
.legend i { display: inline-block; width: 16px; height: 2px; vertical-align: middle; margin-right: 6px; }
.legend b { color: var(--ink); font-weight: 600; }
.legend label { cursor: pointer; }
.legend input { margin: 0 6px 0 0; vertical-align: -2px; accent-color: var(--ink-2); }
.legend label:has(input:not(:checked)) { opacity: 0.45; }
.barcell { width: 34%; position: relative; }
.bar { display: inline-block; height: 8px; background: var(--series-1); border-radius: 0 4px 4px 0; vertical-align: middle; max-width: calc(100% - 44px); }
.barv { margin-left: 6px; color: var(--ink-2); font-size: 12px; }
.chip { display: inline-block; font-size: 11px; border-radius: 4px; padding: 0 6px; margin: 1px 2px 1px 0; border: 1px solid var(--border); white-space: nowrap; }
.chip.k0 { background: var(--hi-bg); }
.chip.k1 { background: var(--hi-bg); }
.chip.k2 { background: transparent; }
.chip.k3, .chip.k4 { background: var(--lo-bg); }
span.pc { display: inline-block; font-weight: 600; font-family: ui-monospace, monospace; padding: 0 2px; margin-right: 1px; border-radius: 3px; background: var(--page); border: 1px solid var(--border); font-size: 12px; }
span.pc.ss { color: var(--sc); } span.pc.sh { color: var(--sh); } span.pc.sd { color: var(--sd); } span.pc.sc { color: var(--sclub); }
details { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; }
summary { cursor: pointer; padding: 12px 16px; }
details[open] summary { border-bottom: 1px solid var(--grid); }
details table { margin: 0; }
details .stats td, details .stats th { padding-left: 12px; }
.combos { font-size: 12px; color: var(--ink-2); margin-top: 2px; }
.sd .line { font-size: 12px; }
.st { margin-bottom: 2px; }
.sn { display: inline-block; width: 22px; color: var(--muted); font-weight: 600; }
.tag { font-weight: 700; } .tag.tv { color: var(--series-2); } .tag.th { color: var(--series-1); }
.t { color: var(--muted); font-size: 11px; }
em { font-style: normal; font-weight: 600; color: var(--alert); }
.scroll { overflow-x: auto; }
.sd td { white-space: normal; }
.act, .cards { white-space: nowrap; }
.spots-link { white-space: nowrap; font-size: 12px; }
a { color: var(--series-1); }
.profile { margin-bottom: 16px; }
.plan .phase ol { margin: 0; padding-left: 20px; display: grid; gap: 12px; }
.plan .phase li::marker { color: var(--muted); font-weight: 600; }
.pa { font-weight: 600; }
.pw { font-size: 12px; color: var(--ink-2); margin-top: 2px; }
.conf { display: inline-block; font-size: 11px; border-radius: 999px; padding: 0 7px; margin-left: 4px; white-space: nowrap; border: 1px solid var(--border); }
.conf.c-solide { background: var(--series-1); border-color: var(--series-1); color: #fff; font-weight: 600; }
.conf.c-indicatif { color: var(--ink); border-color: var(--axis); }
.conf.c-à-confirmer { color: var(--muted); border-style: dashed; }
.i-bluff { background: var(--int-bluff); } .i-semi { background: var(--int-semi); }
.i-thin { background: var(--int-thin); } .i-value { background: var(--int-value); }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
.comp { display: flex; gap: 2px; height: 10px; min-width: 110px; }
.comp .seg { display: block; height: 100%; }
.comp .seg:first-child { border-radius: 4px 0 0 4px; } .comp .seg:last-child { border-radius: 0 4px 4px 0; }
.comp .seg:only-child { border-radius: 4px; }
.comp-t { font-size: 11px; color: var(--ink-2); margin-top: 3px; }
.compcell { min-width: 150px; }
.legend-int { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--ink-2); margin: 8px 0; }
.legend-int i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -2px; margin-right: 6px; }
.intro p { margin: 0; }
.lines-card { margin-top: 16px; }
table.lines tr.main td { border-bottom: 0; padding-bottom: 2px; }
table.lines tr.sub td { font-size: 12px; color: var(--ink-2); padding-top: 0; }
table.lines tr.group td { font-size: 12px; font-weight: 600; color: var(--muted); padding-top: 14px; border-bottom: 1px solid var(--axis); }
table.lines tr.pooled td { background: var(--page); }
.kind { display: inline-block; font-size: 12px; font-weight: 600; border-radius: 4px; padding: 1px 6px; white-space: nowrap; border: 1px solid var(--border); }
.kind.k-value { background: var(--lo-bg); } .kind.k-bluff, .kind.k-semi-bluff { background: var(--hi-bg); }
.kind.k-inconnue, .kind.k-peu-vue { color: var(--muted); font-weight: 500; }
.chip.h-rien { color: var(--muted); } .chip.h-paire { background: var(--lo-bg); } .chip.h-fort { background: var(--lo-bg); font-weight: 600; }
details.inner { margin: 12px 0 0; border-radius: 8px; }
details.inner summary { padding: 8px 12px; font-size: 13px; }
details .inner-body { padding: 10px 12px; }
.chart-wrap { overflow-x: auto; }
.chart { min-width: 600px; }
table.sizing td:first-child { white-space: nowrap; }
@media (max-width: 640px) {
  .tile .value { font-size: 22px; }
  .barcell { width: auto; }
  table.sd thead { display: none; }
  table.sd tr { display: grid; grid-template-columns: auto auto 1fr auto; gap: 0 12px; border-bottom: 1px solid var(--grid); padding: 8px 0; }
  table.sd td { border: 0; padding: 2px 12px; }
  table.sd td.line { grid-column: 1 / -1; grid-row: 2; }
  .act { white-space: normal; }
  table.lines thead { display: none; }
  table.lines tr.main { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px 12px; padding-top: 10px; }
  table.lines tr.main td { display: block; padding: 0; text-align: left !important; }
  table.lines tr.main td.ln, table.lines tr.main td.compcell { grid-column: 1 / -1; }
  table.lines tr.main td[data-l="Tes folds, avec…"], table.lines tr.main td[data-l="Lecture"] { grid-column: span 2; }
  table.lines td[data-l]::before { content: attr(data-l); display: block; font-size: 11px; color: var(--muted); }
  table.lines tr.sub { display: block; }
  table.lines tr.sub td { display: block; padding: 4px 0 10px; }
  table.lines tr.group td { display: block; }
  .compcell { min-width: 0; }
  table.lines tr.pooled { background: var(--page); }
  table.lines tr.pooled td { background: transparent; }
}
"""


TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{style}</style>
</head>
<body>
<main>
{heading}
<div class="meta">Profil Heads-Up · toi : <b>{hero}</b> · {meta}{spots}</div>

{tiles}

<h2>Plan de jeu</h2>
{plan}
<p class="note">Consignes générées à partir des sections ci-dessous. Confiance : <b>solide</b> = l'écart reste vrai même en tenant compte du hasard (intervalle à 90&nbsp;%), <b>indicatif</b> = tendance nette sur un échantillon modeste, <b>à confirmer</b> = peu de mains.</p>

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

<h2>Ses lignes : value ou bluff ?</h2>
<div class="card intro">
  <p>Chaque mise ou relance postflop est rangée par ligne. Quand le coup va à l'abattage, sa main est classée <b>au moment de la mise</b> :</p>
  <div class="legend-int">
    <span><i class="i-bluff"></i>Bluff : rien de fait, moins de 25&nbsp;% d'équité contre ta main</span>
    <span><i class="i-semi"></i>Semi-bluff : rien de fait mais au moins 25&nbsp;% d'équité (flop, turn)</span>
    <span><i class="i-thin"></i>Value fine : paire faible ou moyenne</span>
    <span><i class="i-value"></i>Value : top paire ou mieux</span>
  </div>
  <p class="note">À la river, ta décision de payer ne dépend pas de ses cartes : les mains vues quand tu paies sont un échantillon honnête de sa ligne. Au flop et au turn, on ne voit pas les coups où tu as payé puis foldé plus tard. « Tes folds, avec… » indique ta main quand tu as lâché : un fold sans paire à la river n'est jamais une erreur.</p>
</div>
{lines}
<div class="grid2" style="margin-top:16px">{passive}<div class="card"><h3>Timing selon sa main</h3>{intent_timing}<p class="note">Un écart net de temps entre value et bluff serait un tell exploitable.</p></div></div>

<h2>Où partent tes bb sans abattage</h2>
{nonshowdown}

<h2>Tes bluffs : est-ce qu'il folde assez ?</h2>
<div class="card scroll">{bluffs}
<p class="note">Un bluff pur (sans équité) gagne dès qu'il folde plus souvent que le seuil = mise / (pot + mise), quelle que soit sa main. Signalé quand l'écart dépasse 8 points (au moins 8 occurrences).</p></div>

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
  // Longue série : seuls certains points sont dans la page (data-idx : leur numéro de main, data-n : le total).
  var idx = chart.dataset.idx ? JSON.parse(chart.dataset.idx) : null, N = idx ? +chart.dataset.n : pts.length;
  function at(k) { return idx ? idx[k] : k; }
  var svg = chart.querySelector('svg'), hit = chart.querySelector('.hit');
  var cross = chart.querySelector('.cross'), tip = chart.querySelector('.tooltip');
  var x0 = +chart.dataset.x0, x1 = +chart.dataset.x1, y0 = +chart.dataset.y0, y1 = +chart.dataset.y1;
  var names = ['Résultat réel', 'EV all-in', "À l'abattage", 'Sans abattage'];
  var vars = ['--series-1', '--series-2', '--series-3', '--series-4'];
  var shown = names.map(function () { return true; });
  function fmt(v) { return (v > 0 ? '+' : '') + v.toFixed(0).replace('-', '\\u2212') + ' bb'; }
  // Courbes masquées (cases de la légende) : l'échelle verticale suit celles qui restent ; le choix est gardé.
  var card = chart.closest('.card'), boxes = card ? card.querySelectorAll('.legend input[data-series]') : [];
  var KEY = 'analyzer.courbes-masquees';
  function niceStep(raw) {
    raw = Math.max(raw, 1e-9);
    var mag = raw >= 1 ? Math.pow(10, String(Math.floor(raw)).length) / 10 : 1;
    var ms = [1, 2, 2.5, 5, 10];
    for (var k = 0; k < ms.length; k++) if (raw <= ms[k] * mag) return ms[k] * mag;
    return 10 * mag;
  }
  function tickText(v) { return (v < -1e-9 ? '\\u2212' : '') + Math.abs(Math.round(v)).toLocaleString('fr-FR'); }
  function redraw() {
    var values = [0];
    pts.forEach(function (p) { p.forEach(function (v, s) { if (shown[s]) values.push(v); }); });
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    var step = niceStep((hi - lo) / 5 || 1);
    lo = step * Math.floor(lo / step); hi = step * Math.ceil(hi / step);
    var y = function (v) { return y0 + (y1 - y0) * (1 - (v - lo) / ((hi - lo) || 1)); };
    var x = function (k) { return x0 + (x1 - x0) * (at(k) / Math.max(N - 1, 1)); };
    var axis = svg.querySelector('.yaxis'), ns = 'http://www.w3.org/2000/svg';
    axis.textContent = '';
    for (var t = lo; t <= hi + 1e-9; t += step) {
      var line = document.createElementNS(ns, 'line');
      line.setAttribute('class', Math.abs(t) < 1e-9 ? 'axis' : 'grid');
      line.setAttribute('x1', x0); line.setAttribute('x2', x1);
      line.setAttribute('y1', y(t).toFixed(1)); line.setAttribute('y2', y(t).toFixed(1));
      var text = document.createElementNS(ns, 'text');
      text.setAttribute('class', 'tick'); text.setAttribute('x', x0 - 8); text.setAttribute('y', (y(t) + 4).toFixed(1));
      text.setAttribute('text-anchor', 'end'); text.textContent = tickText(t);
      axis.appendChild(line); axis.appendChild(text);
    }
    svg.querySelectorAll('polyline[data-series]').forEach(function (pl) {
      var s = +pl.dataset.series;
      pl.style.display = shown[s] ? '' : 'none';
      if (shown[s]) pl.setAttribute('points', pts.map(function (p, i) { return x(i).toFixed(1) + ',' + y(p[s]).toFixed(1); }).join(' '));
    });
  }
  try {
    var hidden = JSON.parse(localStorage.getItem(KEY) || '[]');
    if (boxes.length) hidden.forEach(function (s) { if (s >= 0 && s < shown.length) shown[s] = false; });
  } catch (e) { /* stockage indisponible : toutes les courbes */ }
  boxes.forEach(function (box) {
    var s = +box.dataset.series;
    box.checked = shown[s];
    box.addEventListener('change', function () {
      shown[s] = box.checked;
      try { localStorage.setItem(KEY, JSON.stringify(shown.map(function (v, k) { return v ? -1 : k; }).filter(function (k) { return k >= 0; }))); } catch (e) { /* rien */ }
      redraw();
    });
  });
  if (shown.some(function (v) { return !v; })) redraw();
  function show(i) {
    var x = x0 + (x1 - x0) * at(i) / Math.max(N - 1, 1);
    cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
    tip.textContent = '';
    var head = document.createElement('div'); head.textContent = 'Main ' + (at(i) + 1); head.style.color = 'var(--muted)';
    tip.appendChild(head);
    pts[i].forEach(function (v, s) {
      if (!shown[s]) return;
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
    var t = Math.min(Math.max((x - x0) / (x1 - x0), 0), 1) * (N - 1);
    if (!idx) return Math.round(t);
    var lo = 0, hi = idx.length - 1;  // le point gardé le plus proche
    while (lo < hi) { var mid = (lo + hi) >> 1; if (idx[mid] < t) lo = mid + 1; else hi = mid; }
    return lo > 0 && t - idx[lo - 1] < idx[lo] - t ? lo - 1 : lo;
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
