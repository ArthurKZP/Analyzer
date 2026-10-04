"""Page « Bluffs » : dans quelles lignes, sur quelles cartes, avec quelles tailles un adversaire (ou la population
des réguliers) bluffe (voir analyzer/bluffs.py)."""
from __future__ import annotations

from html import escape
from typing import Optional

from .. import bluffs
from ..lines import INTENT_LABELS, INTENTS
from ..models import POSTFLOP
from ..report import cards_html, html_page

STYLE = """
.bl-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.bl-list li { border-left: 3px solid var(--border); padding: 2px 0 2px 10px; }
.bl-list li.plus { border-left-color: var(--alert); }
.bl-list li.moins { border-left-color: var(--series-1); }
.bl-list .bl-t { font-weight: 600; font-size: 14px; color: var(--ink); }
.bl-list .bl-e { font-size: 13px; color: var(--ink-2); margin-top: 2px; }
.bl-list .bl-a { font-size: 13px; margin-top: 2px; }
.bl-scroll { overflow-x: auto; }
.conf { font-size: 11px; border-radius: 4px; padding: 0 5px; margin-left: 6px; font-weight: 400; white-space: nowrap; }
.conf.solide { background: var(--hi-bg); }
.conf.confirmer { background: var(--lo-bg); }
table.bl td.plus { background: var(--hi-bg); }
table.bl td.moins { background: var(--lo-bg); }
table.bl tr.sub td { font-weight: 600; background: var(--page); }
table.bl td.small { font-size: 12px; color: var(--ink-2); }
"""

STREET_NAME = {"flop": "Flop", "turn": "Turn", "river": "River"}
SOURCE_TEXT = {"fréquences": "ses fréquences", "abattage": "ses mains montrées", "timing": "son timing"}


def _pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{round(100 * x)} %"


def _conf(confidence: str) -> str:
    cls = "solide" if confidence == "solide" else "confirmer"
    return f'<span class="conf {cls}">{escape(confidence)}</span>'


def _pattern_items(patterns: list) -> str:
    return "".join(
        f'<li class="{p.direction}"><div class="bl-t">{escape(p.title)}{_conf(p.confidence)}</div>'
        f'<div class="bl-e">{escape(p.evidence)} — d\'après {SOURCE_TEXT[p.source]}</div>'
        f'<div class="bl-a">→ {escape(p.advice)}</div></li>' for p in patterns)


def patterns_html(report: bluffs.Report, top: int = 8) -> str:
    solid = [p for p in report.patterns if p.confidence == "solide"][:top]
    rest = [p for p in report.patterns if p not in solid]
    if not report.patterns:
        return ('<p class="note">Rien de net pour l\'instant : pas assez de mains, ou un jeu proche de la théorie dans '
                'les situations mesurées.</p>')
    out = ""
    if solid:
        out += f'<ul class="bl-list">{_pattern_items(solid)}</ul>'
    else:
        out += '<p class="note">Aucun pattern solide pour l\'instant : les pistes ci-dessous restent à confirmer.</p>'
    if rest:
        out += (f'<details style="margin-top:12px"><summary>Pistes à confirmer ({len(rest)})</summary>'
                f'<div style="padding:10px 16px"><ul class="bl-list">{_pattern_items(rest)}</ul></div></details>')
    return out


def frequencies_html(report: bluffs.Report) -> str:
    rows, current = [], None
    for f in report.freqs:
        if f.ratio.opps < 3:
            continue
        head = (f.family, f.spot.key)
        if head != current:
            current = head
            pot = bluffs.FAMILY_NAMES.get(f.family, "autres pots")
            rows.append(f'<tr class="sub"><td colspan="5">{escape(f.spot.label)} · {escape(pot)}</td></tr>')
        p = f.ratio.hits / f.ratio.opps
        ref = f.reference
        cls = f.verdict[0] if f.verdict else ""
        mark = _conf(f.verdict[1]) if f.verdict else ""
        ref_text = _pct(f.solver) if f.solver is not None else (
            f'<span class="muted">{_pct(f.average)} (ses autres cartes)</span>' if f.average is not None else "–")
        gap = f"{round(100 * (p - ref)):+d} pts" if ref is not None else ""
        rows.append(f'<tr><td>{escape(bluffs.feature_label(f.spot.street, f.feature))}</td>'
                    f'<td class="num {cls}">{_pct(p)} <span class="muted">({f.ratio.opps})</span></td>'
                    f'<td class="num">{ref_text}</td><td class="num">{gap}</td><td>{mark}</td></tr>')
    if not rows:
        return '<p class="note">Pas encore assez de mains.</p>'
    return ('<div class="bl-scroll"><table class="bl"><thead><tr><th>Carte ou texture</th><th class="num">Il mise</th>'
            '<th class="num">Solveur</th><th class="num">Écart</th><th></th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def shown_html(report: bluffs.Report) -> str:
    if not report.shown:
        return '<p class="note">Aucune de ses mises n\'a encore été vue à l\'abattage.</p>'
    dims = dict(bluffs.DIMENSIONS)
    out = []
    for street in POSTFLOP:
        groups = [g for g in report.groups if g.street == street and len(g.items) >= 2]
        if not groups:
            continue
        river = street == "river"
        rows = []
        for g in groups:
            c = g.intents
            r = g.ratio
            cls = g.verdict[0] if g.verdict else ""
            value = (bluffs.feature_label(street, g.value) if g.dimension == "feature" else g.value)
            dim = "Texture" if g.dimension == "feature" and street == "flop" else dims[g.dimension]
            rows.append(f'<tr><td class="small">{escape(dim)}</td><td>{escape(value)}</td>'
                        + "".join(f'<td class="num">{c[i] or ""}</td>' for i in INTENTS)
                        + f'<td class="num {cls}">{_pct(r.hits / r.opps)}</td><td class="num">{_pct(g.reference)}</td>'
                        f'<td>{_conf(g.verdict[1]) if g.verdict else ""}</td></tr>')
        share = "Bluffs" if river else "Sans main faite"
        ref = "Théorie" if river else "Sa moyenne"
        out.append(f'<h3>{STREET_NAME[street]}</h3><div class="bl-scroll"><table class="bl"><thead><tr><th></th><th></th>'
                   + "".join(f'<th class="num">{INTENT_LABELS[i]}</th>' for i in INTENTS)
                   + f'<th class="num">{share}</th><th class="num">{ref}</th><th></th></tr></thead><tbody>'
                   + "".join(rows) + "</tbody></table></div>")
    return "".join(out)


def examples_html(report: bluffs.Report, limit: int = 30) -> str:
    """Ses bluffs vus, les plus récents d'abord."""
    seen = [s for s in report.shown if s.bluff]
    seen.sort(key=lambda s: s.hand.date or 0, reverse=True)
    if not seen:
        return ""
    rows = []
    for s in seen[:limit]:
        board = s.hand.board[: bluffs.BOARD_SIZE[s.street]]
        who = f"<td>{escape(s.player)}</td>" if len(report.names) > 1 else ""
        rows.append(f'<tr>{who}<td>{STREET_NAME[s.street]}</td><td>{escape(s.line)}'
                    f'{" · " + escape(s.size) if s.size else ""}</td>'
                    f'<td>{cards_html(board)}</td><td>{cards_html(s.hand.hole_cards[s.player])}</td>'
                    f'<td class="small">{escape(s.description)}</td><td class="small">{escape(s.hand.hand_id)}</td></tr>')
    who = "<th>Joueur</th>" if len(report.names) > 1 else ""
    return (f'<details><summary>Ses bluffs vus à l\'abattage ({len(seen)})</summary><div class="bl-scroll">'
            f'<table class="bl"><thead><tr>{who}'
            '<th>Street</th><th>Ligne</th><th>Board</th><th>Sa main</th><th>Ce qu\'il avait</th><th>Main</th>'
            '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div></details>")


def players_html(rows: list[dict]) -> str:
    if len(rows) < 2:
        return ""
    body = "".join(
        f'<tr><td>{escape(r["name"])}</td><td class="num">{r["hands"]}</td><td class="num">{r["shown"]}</td>'
        f'<td class="num">{r["river"]}</td><td>{escape(r["top"] or "–")}</td></tr>' for r in rows)
    return ('<h2>Adversaire par adversaire</h2><div class="card"><div class="bl-scroll"><table class="bl"><thead><tr>'
            '<th>Joueur</th><th class="num">Mains</th><th class="num">Mises vues</th><th class="num">Bluffs river vus</th>'
            '<th>Son pattern le plus net</th></tr></thead><tbody>' + body + '</tbody></table></div></div>')


def build_bluffs_page(report: bluffs.Report, who: str, embed: bool = True, note: str = "",
                      players: Optional[list[dict]] = None) -> str:
    """who : « de _Bërsërk_ » ou « des réguliers » ; players : résumé par adversaire (population)."""
    river = sum(1 for s in report.shown if s.street == "river")
    tiles = (
        '<div class="tiles">'
        f'<div class="tile"><div class="label">Mains</div><div class="value">{report.hands}</div></div>'
        f'<div class="tile"><div class="label">Mises et relances</div><div class="value">{report.bets}</div>'
        '<div class="sub">après le flop</div></div>'
        f'<div class="tile"><div class="label">Vues à l\'abattage</div><div class="value">{len(report.shown)}</div>'
        f'<div class="sub">dont {river} à la river</div></div>'
        f'<div class="tile"><div class="label">Patterns solides</div>'
        f'<div class="value">{sum(p.confidence == "solide" for p in report.patterns)}</div>'
        f'<div class="sub">{sum(p.confidence != "solide" for p in report.patterns)} à confirmer</div></div>'
        '</div>')
    body = f"""
<div class="meta">Les bluffs {escape(who)} : dans quelles lignes, sur quelles cartes, avec quelles tailles.</div>
{note}
{tiles}
<h2>Ce qui ressort</h2>
<div class="card">{patterns_html(report)}
<p class="note">Deux sources. <b>Ses fréquences</b>, sur toutes ses mains : une carte ne lui donne pas plus de bonnes
mains qu'à la théorie ; s'il mise nettement plus que le solveur quand elle tombe, le surplus est fait de bluffs (ou de
value fine). <b>Ses mains montrées</b> : à la river, la part de bluffs est comparée à l'équité qu'il te faut pour payer
(la part de bluffs de la théorie pour cette taille) ; au flop et à la turn, à sa propre moyenne. « Solide » : le hasard
l'explique mal (intervalle de confiance à 90 %, au moins 10 occasions) ; « à confirmer » : écart net, peu de mains.</p></div>
<h2>Ses mises selon la carte</h2>
<div class="card">{frequencies_html(report)}
<p class="note">À chaque situation, sa fréquence de mise selon la carte qui vient de tomber (ou la texture du flop),
face à celle du solveur dans les mêmes situations : moyenne des plans de jeu des flops résolus de ce type de pot. Sans
repère du solveur, on compare à ses autres cartes. Seules les mains de pots SRP, 3bet et 4bet ont un repère.</p></div>
<h2>Ses mains montrées</h2>
<div class="card">{shown_html(report)}{examples_html(report)}
<p class="note">Chaque mise ou relance vue à l'abattage, classée par son intention au moment de miser : value (top paire
ou mieux), value fine (paire moyenne ou faible), semi-bluff (rien de fait mais au moins 25 % d'équité contre ta main),
bluff. Biais : une mise n'est vue que si le coup va à l'abattage. À la river, ta décision de payer ne dépend pas de ses
cartes : l'échantillon est honnête. Au flop et à la turn, il manque les coups où tu as payé puis foldé plus tard.</p></div>
{players_html(players or [])}
"""
    return html_page(f"Bluffs {who}", f"<style>{STYLE}</style>{body}", embed)
