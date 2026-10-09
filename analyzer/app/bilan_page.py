"""Bilan de « Mon jeu » aux tables à plusieurs, sur le plan de celui du heads-up (selfreport.build_self_report) :
ton résultat et sa courbe, tes écarts les plus importants, tes stats, tes résultats par adversaire. En tête des deux
bilans, tes résultats tous formats confondus et le choix du format."""
from __future__ import annotations

from html import escape
from typing import Optional

from .. import ring
from ..models import FOLD, Hand
from ..report import SCRIPT, chart_svg, html_page, legend, num, pct_cell
from ..selfreport import OPP_SCRIPT, OPP_STYLE, opponent_row_data, opponent_tools
from ..stats import allin_ev
from .leaks_page import STYLE as LEAK_STYLE
from .review_page import STYLE as REVIEW_STYLE
from .ring_page import _gaps


def results(hands: list[Hand], hero: str) -> dict:
    """Ton résultat sur ces mains : net (en monnaie et en bb), EV all-in (tapis à deux payés avant la river), avec et
    sans abattage, et la courbe (cumul réel, EV, abattage, sans abattage), main par main."""
    out = {"hands": 0, "net": 0.0, "net_bb": 0.0, "ev_bb": 0.0, "allin": 0, "sd_bb": 0.0, "nosd_bb": 0.0,
           "sd_hands": 0, "curve": [], "first": None, "last": None}
    prev = (0.0, 0.0, 0.0, 0.0)
    for h in hands:
        if hero not in h.seats or not h.bb:
            continue
        net = h.net(hero)
        net_bb = net / h.bb
        ev = allin_ev(h)
        ev_bb = ev[hero] / h.bb if ev is not None and hero in ev else net_bb
        showdown = h.showdown and not any(a.player == hero and a.kind == FOLD for a in h.actions)
        out["hands"] += 1
        out["net"] += net
        out["net_bb"] += net_bb
        out["ev_bb"] += ev_bb
        out["allin"] += ev is not None and hero in ev and not any(a.player == hero and a.kind == FOLD
                                                                  for a in h.actions)
        out["sd_bb" if showdown else "nosd_bb"] += net_bb
        out["sd_hands"] += showdown
        out["first"] = out["first"] or h.date
        out["last"] = h.date
        prev = (prev[0] + net_bb, prev[1] + ev_bb, prev[2] + (net_bb if showdown else 0.0),
                prev[3] + (0.0 if showdown else net_bb))
        out["curve"].append(prev)
    out["bb100"] = 100 * out["net_bb"] / out["hands"] if out["hands"] else None
    return out


def _tile(label: str, value: str, sub: str) -> str:
    return f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div><div class="sub">{sub}</div></div>'


def _bb100(x: Optional[float]) -> str:
    return "–" if x is None else f"{num(x, 1, sign=True)} bb/100"


def overall_html(heads_up: dict, tables: dict) -> str:
    """Tes résultats tous formats confondus, puis en heads-up et aux tables à plusieurs (rien sans les deux)."""
    if not heads_up["hands"] or not tables["hands"]:
        return ""
    hands = heads_up["hands"] + tables["hands"]
    net = heads_up["net_bb"] + tables["net_bb"]
    items = [("Tous formats confondus", hands, net, 100 * net / hands),
             ("En heads-up", heads_up["hands"], heads_up["net_bb"], heads_up["bb100"]),
             ("Aux tables à plusieurs", tables["hands"], tables["net_bb"], tables["bb100"])]
    tiles = "".join(_tile(label, _bb100(rate), f"{n} mains · {num(bb, 1, sign=True)} bb") for label, n, bb, rate in items)
    return f'<div class="tiles bl-all">{tiles}</div>'


def result_tiles(res: dict) -> str:
    """Les mêmes tuiles que le bilan heads-up : résultat, winrate, EV all-in, avec et sans abattage."""
    return '<div class="tiles">' + "".join((
        _tile("Ton résultat", f"{num(res['net_bb'], 1, sign=True)} bb", f"{num(res['net'], 2, sign=True)} €"),
        _tile("Winrate", num(res["bb100"], 1, sign=True) if res["bb100"] is not None else "–", "bb/100 mains"),
        _tile("Résultat EV all-in", f"{num(res['ev_bb'], 1, sign=True)} bb",
              f"écart chance : {num(res['net_bb'] - res['ev_bb'], 1, sign=True)} bb sur {res['allin']} all-in"),
        _tile("Avec / sans abattage", f"{num(res['sd_bb'], 0, sign=True)} / {num(res['nosd_bb'], 0, sign=True)}",
              f"bb · {num(res['sd_hands'], 0)} / {num(res['hands'] - res['sd_hands'], 0)} mains"),
    )) + "</div>"


def stats_table(scopes: list[tuple[str, str, ring.FormatStats]]) -> str:
    """Tes stats toutes positions confondues, sur toutes tes mains, contre les réguliers et contre les récréatifs,
    face aux repères indicatifs d'un régulier 6-max."""
    head = "".join(f'<th class="num">{escape(label)}</th>' for _, label, _ in scopes)
    rows = []
    for key, label in ring.STATS:
        stat = ring.stat_def(ring.MERGED, key)
        if not any(fs.total.ratios[key].opps for _, _, fs in scopes):
            continue
        cells = "".join(pct_cell(fs.total.ratios[key], stat=stat) for _, _, fs in scopes)
        ref = f"{stat.ref[0]:.0f}–{stat.ref[1]:.0f}&nbsp;%" if stat.ref else ""
        rows.append(f'<tr><td>{escape(label)}</td>{cells}<td class="num muted">{ref}</td></tr>')
    return (f'<div class="card scroll"><table class="stats"><thead><tr><th></th>{head}<th class="num">Repère</th></tr>'
            f'</thead><tbody>{"".join(rows)}</tbody></table>'
            '<p class="note">Toutes positions confondues ; le détail par position est dans l\'onglet <a href="tables">'
            "Tables à plusieurs</a>. Repères indicatifs d'un régulier 6-max à 100 bb.</p></div>")


def _by_scope(scopes: list[tuple[str, str, ring.FormatStats]]) -> str:
    """Ton résultat contre les réguliers et contre les récréatifs (rien si un seul type est présent)."""
    shown = [(label, fs.total) for scope, label, fs in scopes if scope != "all" and fs.total.hands]
    if len(shown) < 2:
        return ""
    return '<div class="tiles">' + "".join(
        _tile(escape(label), _bb100(t.bb100), f"{t.hands} mains · {num(t.net_bb, 1, sign=True)} bb")
        for label, t in shown) + "</div>"


def opponents_table(opponents: list[dict]) -> str:
    """Tes adversaires des tables à plusieurs : mains à la même table, pots disputés ensemble et ton résultat dans ces
    pots, leur type ; recherche, filtre et tri comme pour le heads-up."""
    if not opponents:
        return '<p class="muted">Pas encore d\'adversaire.</p>'
    names = {"reg": "Régulier", "rec": "Récréatif"}
    rows = []
    for o in opponents:
        source = "" if o.get("source") == "toi" else f' <span class="muted">({escape(o.get("source", ""))})</span>'
        rate = 100 * o["net_bb"] / o["pots"] if o["pots"] else 0.0
        data = opponent_row_data(o["name"], o.get("kind") or "reg", o["hands"], o["net_bb"], rate, o["last"])
        rows.append(f'<tr {data}><td>{escape(o["name"])}</td><td>{escape(names.get(o.get("kind"), "Régulier"))}{source}</td>'
                    f'<td class="num">{o["hands"]}</td><td class="num">{o["pots"]}</td>'
                    f'<td class="num">{num(o["net_bb"], 1, sign=True)}</td>'
                    f'<td class="num">{num(rate, 1, sign=True) if o["pots"] else "–"}</td>'
                    f'<td class="muted nowrap">{escape(o["last"])}</td></tr>')
    tools = opponent_tools() if len(opponents) > 1 else ""
    return (f'<div class="card opp-box">{tools}<div class="scroll"><table class="stats opp-table"><thead><tr>'
            '<th data-sort="name">Adversaire</th><th>Type</th><th class="num" data-sort="hands">Mains ensemble</th>'
            '<th class="num">Pots disputés</th><th class="num" data-sort="net">Ton résultat (bb)</th>'
            '<th class="num" data-sort="bb100">bb/100 (pots)</th><th data-sort="last">Dernière main</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div><p class="note">Ton résultat dans les pots disputés ensemble '
            "(chacun y a mis de l'argent de lui-même), en bb ; bb/100 : pour 100 de ces pots. Le type se règle dans "
            "Paramètres › Joueurs et alias.</p></div>")


def build_ring_bilan(hands: list[Hand], hero: str, scopes: list[tuple[str, str, Optional[ring.FormatStats]]],
                     opponents: list[dict], gaps: Optional[list], head: str = "", embed: bool = True) -> str:
    """hands : tes mains aux tables à plusieurs ; scopes : [(portée, libellé, ring.analyze(…, merge=True)[0])] ;
    opponents : Library.ring_opponents_view() ; gaps : tes écarts les plus importants contre les réguliers ; head : le
    choix du format et tes résultats tous formats confondus."""
    res = results(hands, hero)
    if not res["hands"]:
        body = (f'{head}<p class="note">Aucune main à une table de 3 joueurs ou plus. Importe des historiques 3-max, '
                "6-max ou de 7 à 9 joueurs (Betclic, Winamax, Unibet).</p>")
        return html_page(f"Mon jeu — {hero}", body, embed)
    shown = [(scope, label, fs) for scope, label, fs in scopes if fs is not None]
    formats = sorted({h.table_format for h in hands}, key=lambda f: (ring.h_size(f), f))
    period = f"{res['first']:%d/%m/%Y} → {res['last']:%d/%m/%Y}"
    tools = len(opponents) > 1
    body = f"""<style>{OPP_STYLE}{REVIEW_STYLE}{LEAK_STYLE}</style>
<div class="meta">{res['hands']} mains aux tables à plusieurs ({escape(', '.join(formats))}) · {len(opponents)} adversaire(s)
· {period}</div>
{head}
{result_tiles(res)}

<h2>Résultat cumulé (bb)</h2>
<div class="card">{legend(res['curve'])}{chart_svg(res['curve'])}
<p class="note">« EV all-in » remplace le résultat réel des tapis payés avant la river, quand il ne reste que deux
joueurs, par ton espérance : l'écart mesure la chance. « À l'abattage » : les mains où tu es allé jusqu'à
l'abattage.</p></div>

{_gaps(gaps) if gaps is not None else ""}

<h2>Tes statistiques</h2>
{stats_table(shown)}

<h2>Résultats par adversaire</h2>
{_by_scope(shown)}
{opponents_table(opponents)}

<p class="note">Toutes tes tables de 3 joueurs et plus ensemble. Une main compte contre les récréatifs quand un récréatif
a mis de l'argent dans le pot pendant que tu y étais encore.</p>
"""
    return html_page(f"Mon jeu — {hero}", body, embed, script=SCRIPT + (OPP_SCRIPT if tools else ""))
