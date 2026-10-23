"""Le replayer : une main jouée, telle quelle, pour la revoir action par action (static/replayer.js) — heads-up ou
table à plusieurs, finie au préflop ou à l'abattage.

record() la décrit en grosses blindes : les joueurs dans l'ordre des places (position, tapis, cartes connues : les
tiennes, et celles montrées), les actions (street, joueur, montant ajouté, total engagé sur la street, pot avant),
le tableau, ce que chacun récupère (gains, mise non payée rendue) et son résultat.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from .models import Hand
from .stats import allin_ev

STREETS = ("preflop", "flop", "turn", "river")


def _bb(x: float, bb: float) -> float:
    return round(x / bb, 2)


def record(hand: Hand, hero: Optional[str] = None) -> dict:
    """La main pour le replayer ; hero : ton pseudo (tes cartes et ton résultat mis en avant)."""
    bb = hand.bb or 1.0
    order = sorted(hand.seats.values(), key=lambda s: s.seat)
    index = {s.name: k for k, s in enumerate(order)}
    ev = allin_ev(hand)
    players = []
    for s in order:
        cards = hand.hole_cards.get(s.name, [])
        players.append({
            "name": s.name, "pos": hand.position(s.name), "stack": _bb(s.stack, bb),
            "cards": cards if len(cards) == 2 else [], "hero": s.name == hero, "button": s.is_button,
            "net": _bb(hand.net(s.name), bb), "shown": hand.shown_hand.get(s.name, ""),
            "ev": _bb(ev[s.name], bb) if ev is not None and s.name in ev else None,
        })
    actions = [{"s": a.street, "p": index[a.player], "k": a.kind, "a": _bb(a.amount, bb), "to": _bb(a.to, bb),
                "pot": _bb(a.pot_before, bb), "ai": a.all_in}
               for a in hand.actions if a.player in index]
    return {
        "id": hand.hand_id, "site": hand.site, "date": hand.date.strftime("%d/%m/%Y %H:%M"), "game": hand.game_name,
        "stakes": f"NL{round(bb * 100, 2):g}", "bb": bb, "format": hand.table_format,
        "players": players, "actions": actions, "board": hand.board,
        "win": {index[p]: _bb(v, bb) for p, v in hand.winnings.items() if p in index and v},
        "ret": {index[p]: _bb(v, bb) for p, v in hand.uncalled.items() if p in index and v},
        "rake": _bb(hand.rake, bb), "total": _bb(hand.total_pot, bb), "showdown": hand.showdown,
    }


def api_of(pages: str) -> str:
    """L'adresse des données d'un espace d'après celle de ses pages : « /moi » → « /api », « /eleve/paul » →
    « /api/eleves/paul », « /reference/x » → « /api/references/x »."""
    parts = [p for p in pages.split("/") if p]
    if len(parts) == 2 and parts[0] in ("eleve", "reference"):
        return f"/api/{parts[0]}s/{parts[1]}"
    return "/api"


def href(hand_id: str, api: str = "/api", at: Optional[int] = None) -> str:
    """L'adresse du replayer pour cette main, dans l'espace de api ; at : placée sur cette action (hand.actions)."""
    query = ([] if api == "/api" else [f"api={quote(api, safe='/')}"]) + ([] if at is None else [f"at={at}"])
    return f"/rejouer/{quote(hand_id, safe='')}" + ("?" + "&".join(query) if query else "")
