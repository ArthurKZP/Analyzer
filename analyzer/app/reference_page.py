"""Les pages d'un joueur de référence qui le comparent à toi : « Toi et lui » (résultats, écarts de fréquence nets,
positions, toutes les fréquences) et « Value et bluffs » (ses lignes après le flop, avec ce qu'il avait, et les
tiennes). Les autres pages de son jeu sont celles de Mon jeu, appliquées à ses mains (Library.reference)."""
from __future__ import annotations

from html import escape
from typing import Optional
from urllib.parse import quote

from .. import ring
from ..field import INTENT_LABELS
from ..lines import BOARD_SIZE
from ..reference import POSITION_MIN, LineRow, LineStats, Result, Row, examples, gaps, passed, street_totals
from ..report import FORMAT_NAMES, cards_html, html_page, num, pct_cell
from ..theory import postflop

STREET_NAME = {"flop": "Flop", "turn": "Turn", "river": "River"}

STYLE = """
.rf-who { display: flex; flex-wrap: wrap; gap: 4px 16px; }
.rf-gap { font-variant-numeric: tabular-nums; }
.rf-gap.clear { font-weight: 700; }
td.rf-gap.clear.up { background: var(--hi-bg); }
td.rf-gap.clear.down { background: var(--lo-bg); }
.rf-tag { font-size: 11px; color: var(--ink-2); }
.rf-tag.lui { color: var(--good); }
.rf-pair { white-space: nowrap; font-variant-numeric: tabular-nums; }
.rf-pair .me { color: var(--muted); }
table.stats tr.rf-section td { font-weight: 600; color: var(--ink-2); padding-top: 14px; border-bottom: none; }
.rf-mix { display: flex; height: 8px; width: 120px; border-radius: 4px; overflow: hidden; background: var(--grid);
  margin-top: 4px; }
.rf-mix i { display: block; height: 100%; }
.rf-mix .value { background: var(--int-value); } .rf-mix .thin { background: var(--int-thin); }
.rf-mix .semi { background: var(--int-semi); } .rf-mix .bluff { background: var(--int-bluff); }
.rf-key { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--ink-2); margin: 0 0 10px; }
.rf-key i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
th.rf-me, td.rf-me { border-left: 1px solid var(--grid); }
"""

INTENT_CLASSES = ("value", "thin", "semi", "bluff")


def _pct(x: Optional[float], digits: int = 0) -> str:
    return "–" if x is None else f"{num(x, digits)}&nbsp;%"


def _rate(x: Optional[float]) -> str:
    return "–" if x is None else num(x, 1, sign=True)


def _tile(label: str, his: Optional[float], mine: Optional[float], unit: str = "bb/100") -> str:
    return (f'<div class="tile"><div class="label">{label}</div><div class="value">{_rate(his)}</div>'
            f'<div class="sub">{unit} · toi : {_rate(mine)}</div></div>')


def _head(context: dict) -> str:
    """Le choix du format, puis qui est comparé à qui, sur combien de mains."""
    fmt = FORMAT_NAMES.get(context["format"], context["format"])
    period = context.get("period") or "Toutes les mains"
    mine = (f'toi (<b>{escape(context["me"])}</b>) : {num(context["my_hands"], 0)} mains '
            f'<span class="muted">({escape(period.lower())})</span>' if context["my_hands"]
            else "toi : aucune main à ce format pour comparer")
    return (f'{context["switch"]}<div class="meta rf-who"><span>{fmt} · <b>{escape(context["him"])}</b> : '
            f'{num(context["his_hands"], 0)} mains, toutes ses cartes connues</span><span>{mine}</span></div>')


# --- Toi et lui -----------------------------------------------------------------------------------------------------

def _gap_text(row: Row) -> str:
    more = row.gap is not None and row.gap > 0
    closer = row.closer()
    ref = f"{row.ref[0]:.0f}–{row.ref[1]:.0f} %" if row.ref else ""
    note = (f' <span class="rf-tag lui">lui dans le repère d\'un régulier solide ({ref})</span>' if closer == "lui"
            else f' <span class="rf-tag">toi plus près du repère ({ref})</span>' if closer == "toi" else "")
    return (f'<li><span class="pill">{"plus" if more else "moins"}</span>'
            f'<div><b>{escape(row.label)}</b> : {_pct(row.his.pct)} pour lui, {_pct(row.mine.pct)} pour toi '
            f'<span class="muted">({row.his.opps} et {row.mine.opps} occasions)</span>{note}</div></li>')


def gaps_html(rows: list[Row]) -> str:
    found = gaps(rows)
    if not found:
        body = ('<p class="muted" style="margin:0">Pas d\'écart net entre vous pour l\'instant : vos fréquences se '
                'ressemblent, ou l\'échantillon est encore petit.</p>')
    else:
        body = '<ul class="findings">' + "".join(_gap_text(r) for r in found) + "</ul>"
    return (f'<h2>Ce qu\'il fait autrement</h2><div class="card">{body}<p class="note">Ses fréquences qui s\'écartent '
            'nettement des tiennes, les plus marquées d\'abord (le hasard les explique mal : test à 90 %, au moins 20 '
            'occasions chacun). Un bon joueur n\'a pas toujours raison, mais c\'est là que vos jeux diffèrent : à '
            'creuser dans ses mains (onglet Value et bluffs, Mains de départ).</p></div>')


def _pair(his: Optional[float], mine: Optional[float], fmt=_rate) -> str:
    return f'<span class="rf-pair">{fmt(his)} <span class="me">· {fmt(mine)}</span></span>'


def positions_html(by_pos: tuple[dict[str, Result], dict[str, Result]],
                   stats: Optional[tuple[list, list]]) -> str:
    """Par position : vos mains et vos winrates (lui · toi) ; aux tables à plusieurs, VPIP, PFR et 3bet aussi."""
    mine, his = by_pos
    order = list(ring.ORDER) + sorted((set(mine) | set(his)) - set(ring.ORDER))
    shown = [p for p in order if max(mine.get(p, Result()).hands, his.get(p, Result()).hands) >= POSITION_MIN]
    if not shown:
        return ""
    keys = (("vpip", "VPIP"), ("pfr", "PFR"), ("threebet", "3bet")) if stats else ()
    pos_stats: tuple[dict, dict] = ({}, {})
    if stats:
        for k, found in enumerate(stats):
            if found:
                pos_stats[k].update({p.position: p for p in found[0].positions})
    head = "".join(f'<th class="num">{label}</th>' for _, label in keys)
    rows = []
    for p in shown:
        a, b = his.get(p, Result()), mine.get(p, Result())
        cells = ""
        for key, _ in keys:
            r_his = pos_stats[1].get(p).ratios[key] if p in pos_stats[1] else None
            r_mine = pos_stats[0].get(p).ratios[key] if p in pos_stats[0] else None
            cells += ('<td class="num">' + _pair(r_his.pct if r_his else None, r_mine.pct if r_mine else None,
                                                  lambda x: "–" if x is None else f"{num(x, 0)} %") + "</td>")
        rows.append(f'<tr><td>{escape(p)}</td><td class="num">{_pair(a.hands, b.hands, lambda x: num(x or 0, 0))}</td>'
                    f'<td class="num">{_pair(a.bb100, b.bb100)}</td>{cells}</tr>')
    return ('<h2>Par position</h2><div class="card scroll"><table class="stats"><thead><tr><th>Position</th>'
            f'<th class="num">Mains</th><th class="num">bb/100</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody>'
            '</table><p class="note">Chaque case : lui · <span class="muted">toi</span>. Les winrates par position '
            'varient beaucoup d\'un échantillon à l\'autre (il faut des dizaines de milliers de mains pour qu\'un écart '
            'soit sûr) ; les fréquences se stabilisent bien plus vite.</p></div>')


def stats_html(rows: list[Row]) -> str:
    """Toutes vos fréquences, section par section : lui, toi, l'écart (net : surligné), le repère."""
    body, section = [], None
    for r in rows:
        if not r.mine.opps and not r.his.opps:
            continue
        if r.section != section:
            section = r.section
            body.append(f'<tr class="rf-section"><td colspan="5">{escape(section)}</td></tr>')
        gap = r.gap
        cls = "num rf-gap" + (" clear " + ("up" if (gap or 0) > 0 else "down") if r.clear else "")
        gap_text = "–" if gap is None else f"{num(gap, 0, sign=True)}"
        ref = f"{r.ref[0]:.0f}–{r.ref[1]:.0f}&nbsp;%" if r.ref else ""
        body.append(f'<tr><td>{escape(r.label)}</td>{pct_cell(r.his)}{pct_cell(r.mine)}'
                    f'<td class="{cls}">{gap_text}</td><td class="num muted">{ref}</td></tr>')
    if not body:
        return ""
    return ('<h2>Toutes les fréquences</h2><div class="card scroll"><table class="stats"><thead><tr><th></th>'
            '<th class="num">Lui</th><th class="num">Toi</th><th class="num">Écart (points)</th>'
            f'<th class="num">Repère</th></tr></thead><tbody>{"".join(body)}</tbody></table>'
            '<p class="note">Écart : sa fréquence moins la tienne, en points ; en gras et surligné quand il est net. '
            'Repère : la fourchette d\'un régulier solide, indicative.</p></div>')


def build_comparison_page(rows: list[Row], results: tuple[Result, Result],
                          by_pos: tuple[dict[str, Result], dict[str, Result]], positions: Optional[tuple[list, list]],
                          context: dict, embed: bool = True) -> str:
    """rows : reference.compare_hu ou compare_ring ; results, by_pos : (toi, lui) ; positions : (toi, lui)
    ring.analyze(…, merge=True) aux tables à plusieurs ; context : me, him, format, switch, my_hands, his_hands,
    period."""
    mine, his = results
    tiles = ('<div class="tiles">' + _tile("Son winrate", his.bb100, mine.bb100)
             + _tile("Sans abattage", his.nosd100, mine.nosd100)
             + _tile("À l'abattage", his.sd100, mine.sd100)
             + _tile("EV all-in", his.ev100, mine.ev100) + "</div>")
    body = (_head(context) + tiles + gaps_html(rows) + positions_html(by_pos, positions) + stats_html(rows)
            + '<p class="note">Ses mains viennent des historiques où il est le héros : toutes ses cartes y sont connues. '
              'Les tiennes : celles de la période choisie dans Mon jeu.</p>')
    return html_page(f"Toi et {context['him']}", f"<style>{STYLE}</style>{body}", embed)


# --- Value et bluffs ------------------------------------------------------------------------------------------------

def _mix(stats: LineStats) -> str:
    """La composition de ses mises (value, value fine, semi-bluff, bluff) en barre."""
    known = len(stats.known)
    if not known:
        return ""
    counts = stats.intents
    parts = "".join(f'<i class="{k}" style="width:{100 * counts[k] / known:.1f}%"></i>' for k in INTENT_CLASSES
                    if counts[k])
    title = ", ".join(f"{INTENT_LABELS[k]} {counts[k]}" for k in INTENT_CLASSES if counts[k])
    return f'<div class="rf-mix" title="{escape(title)}">{parts}</div>'


def _cells(stats: LineStats, me: bool = False) -> str:
    cls = ' class="num rf-me"' if me else ' class="num"'
    if not stats.count:
        return f'<td{cls}>–</td><td class="num">–</td><td class="num">–</td><td class="num">–</td>'
    return (f'<td{cls}>{stats.count}</td><td class="num">{_pct(stats.value)}</td><td class="num">{_pct(stats.bluff)}'
            f'</td><td class="num">{_pct(stats.passes)}</td>')


def street_html(street: str, rows: list[LineRow]) -> str:
    river = street == "river"
    body = []
    for r in rows:
        balance = f'<td class="num muted">{_pct(r.his.balance)}</td>' if river else ""
        body.append(f'<tr><td>{escape(r.name)}{_mix(r.his)}</td>{_cells(r.his)}{balance}{_cells(r.mine, me=True)}</tr>')
    extra = '<th class="num" title="Part de bluffs d\'une range équilibrée pour sa taille de mise">Équilibre</th>' \
        if river else ""
    return (f'<h3>{STREET_NAME[street]}</h3><div class="card scroll" style="margin-bottom:16px"><table class="stats">'
            '<thead><tr><th>Ligne · taille</th><th class="num">Ses mises</th><th class="num">Value</th>'
            f'<th class="num">Bluff</th><th class="num">Passe</th>{extra}<th class="num rf-me">Tes mises</th>'
            '<th class="num">Value</th><th class="num">Bluff</th><th class="num">Passe</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _replay(hand) -> str:
    """Revoir la main dans l'explorateur (au solveur), quand il ne restait que deux joueurs au flop."""
    if postflop.flop_pair(hand) is None:
        return ""
    return (f'<a class="spots-link" href="/explorateur/{quote(hand.hand_id, safe="")}" target="_blank" '
            'rel="noopener">revoir ↗</a>')


def examples_html(title: str, bets: list, note: str) -> str:
    if not bets:
        return ""
    rows = []
    for b in bets:
        board = b.hand.board[:BOARD_SIZE[b.street]]
        result = num(b.hand.net(b.player) / b.hand.bb, 1, sign=True)
        rows.append(f'<tr><td class="nowrap">{b.hand.date:%d/%m/%Y}</td><td>{STREET_NAME[b.street]}</td>'
                    f'<td>{escape(b.label)}{" · " + escape(b.size) if b.size else ""}</td><td>{cards_html(board)}</td>'
                    f'<td>{cards_html(b.hand.hole_cards[b.player])}</td><td class="small">{escape(b.description)}</td>'
                    f'<td>{"tout le monde se couche" if passed(b) else "payée ou relancée"}</td>'
                    f'<td class="num">{result}</td><td>{_replay(b.hand)}</td></tr>')
    return (f'<details><summary><b>{escape(title)}</b> <span class="muted">— {len(bets)} main(s)</span></summary>'
            '<div class="scroll"><table class="stats"><thead><tr><th>Date</th><th>Street</th><th>Ligne</th><th>Board</th>'
            '<th>Ses cartes</th><th>Ce qu\'il avait</th><th>Suite</th><th class="num">Résultat (bb)</th><th></th></tr>'
            f'</thead><tbody>{"".join(rows)}</tbody></table></div><p class="note" style="margin:8px 12px 12px">{note}'
            '</p></details>')


def build_lines_page(rows: list[LineRow], context: dict, embed: bool = True) -> str:
    """rows : reference.lines (ses lignes et les tiennes) ; context : comme build_comparison_page."""
    head = _head(context)
    if not rows:
        body = head + ('<p class="note">Pas encore assez de mises après le flop pour lire ses lignes (au moins 5 dans une '
                       'ligne).</p>')
        return html_page(f"Value et bluffs — {context['him']}", f"<style>{STYLE}</style>{body}", embed)
    totals = street_totals(rows)
    tiles = '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="label">{STREET_NAME[street]} : ses bluffs</div>'
        f'<div class="value">{_pct(his.bluff)}</div><div class="sub">de ses {his.count} mises · toi : {_pct(mine.bluff)} '
        f'de {mine.count}</div></div>' for street, (his, mine) in totals.items()) + "</div>"
    key = '<div class="rf-key">' + "".join(f'<span><i style="background:var(--int-{k})"></i>{INTENT_LABELS[k]}</span>'
                                           for k in INTENT_CLASSES) + "</div>"
    streets = "".join(street_html(street, [r for r in rows if r.street == street]) for street in totals)
    bluffs = examples(rows, ("bluff", "semi"))
    thin = examples(rows, ("thin",))
    body = (head + '<p>Chaque mise ou relance après le flop, rangée par street, ligne et taille, avec ce qu\'il avait '
            'au moment de miser : <b>value</b> (top paire ou mieux), <b>value fine</b> (paire moyenne ou faible), '
            '<b>semi-bluff</b> (un tirage, avant la river), <b>bluff</b> (rien). Ses cartes sont toujours connues : '
            'ses lignes se lisent sans le biais de l\'abattage. <b>Passe</b> : la mise a fait coucher tout le monde. '
            'Les tiennes à côté.</p>' + tiles + "<h2>Ses lignes, une par une</h2>" + key + streets
            + '<p class="note">Value et Bluff : en % de ses mises dans la ligne (value fine et semi-bluffs compris). '
              'Équilibre (river) : la part de bluffs d\'une range équilibrée pour sa taille de mise médiane, celle qui '
              'rend indifférent l\'adversaire qui paie (un tiers pour une mise de la taille du pot).</p>'
            + "<h2>Ses mains</h2>"
            + examples_html("Ses bluffs et semi-bluffs, les plus récents", bluffs,
                            "Où et avec quoi il bluffe : les cartes qui bloquent, les tirages, les boards qu'il attaque.")
            + examples_html("Sa value fine, la plus récente", thin,
                            "Les paires moyennes ou faibles qu'il mise pour être payé par pire."))
    return html_page(f"Value et bluffs — {context['him']}", f"<style>{STYLE}</style>{body}", embed)
