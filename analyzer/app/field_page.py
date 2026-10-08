"""Étude du field, onglet « Les joueurs » : tes adversaires en heads-up et aux tables à plusieurs, réguliers et
récréatifs, avec leur type réglable (les bluffs des réguliers ont leur onglet, bluffs_page.py)."""
from __future__ import annotations

from html import escape

from ..report import html_page
from ..selfreport import OPP_SCRIPT, OPP_STYLE
from .leaks_page import KIND_SCRIPT, opponents_html


def _tile(label: str, players: list[dict]) -> str:
    recs = sum(1 for o in players if o.get("kind") == "rec")
    return (f'<div class="tile"><div class="label">{escape(label)}</div><div class="value">{len(players)}</div>'
            f'<div class="sub">{len(players) - recs} régulier(s) · {recs} récréatif(s)</div></div>')


def build_players_page(heads_up: list[dict], ring: list[dict], embed: bool = True) -> str:
    """heads_up, ring : tes adversaires (Library.summary()["opponents"], Library.ring_opponents_view())."""
    if not heads_up and not ring:
        body = '<p class="note">Aucun adversaire pour l\'instant : importe des mains.</p>'
        return html_page("Les joueurs", body, embed)
    tiles = "".join(_tile(label, players) for label, players in (("En heads-up", heads_up),
                                                                    ("Aux tables à plusieurs", ring)) if players)
    body = f"""<div class="meta">Le field : tes adversaires en heads-up et aux tables à plusieurs, réguliers et récréatifs.
Contre un régulier, ton jeu se compare à la théorie ; contre un récréatif, l'exploitation prime.</div>
<div class="tiles">{tiles}</div>
{opponents_html(heads_up, title="En heads-up")}
{opponents_html(ring, ring=True, title="Aux tables à plusieurs")}
<p class="note">Le type d'un joueur est le même partout : choisi ici, dans sa fiche ou dans le Leakfinding, il vaut en
heads-up et aux tables à plusieurs. Sans choix, la suggestion dépend du jeu : en heads-up, ses stats au bouton et à la
BB ; aux tables à plusieurs, ses fréquences à la taille de table où il a le plus joué.</p>
"""
    return html_page("Les joueurs", f"<style>{OPP_STYLE}</style>{body}", embed, script=KIND_SCRIPT + OPP_SCRIPT)
