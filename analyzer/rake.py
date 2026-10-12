"""Le rake que paie un joueur : sa part du rake de chaque pot, au prorata de ce qu'il y a mis (méthode « weighted
contributed », celle de la plupart des sites pour calculer le rakeback, et des trackers par défaut). Une mise non
payée, rendue au joueur, ne compte pas ; les blindes d'un joueur qui se couche, si. Le rake d'un pot sans rake (« no
flop, no drop ») est nul.

Somme sur les joueurs du pot : le rake de la main. Ce rake est déjà déduit des résultats (le pot gagné est net de
rake) : « avant rake », c'est le résultat plus le rake payé.
"""
from __future__ import annotations

from .models import Hand


def paid(hand: Hand, player: str) -> float:
    """La part du rake de la main que paie ce joueur (en monnaie)."""
    if not hand.rake or player not in hand.seats:
        return 0.0
    put = {p: max(hand.put.get(p, 0.0) - hand.uncalled.get(p, 0.0), 0.0) for p in hand.seats}
    total = sum(put.values())
    return hand.rake * put[player] / total if total > 0 else 0.0
