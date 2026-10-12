"""Les limites jouées, d'après la grosse blinde de chaque main (la monnaie ne compte pas : NL10 = 0,10 de grosse
blinde) :

- micro limites : NL10 et moins ;
- limites du milieu : au-dessus, jusqu'à NL100 ;
- hautes limites : au-delà de NL100 (NL200 et plus).

L'Étude du field juge chaque adversaire à un niveau de limites : ses mains de ce niveau, face aux repères d'un régulier
solide de ce niveau, avec les exploits qui y marchent (field.band_defs) ; un joueur qui joue à plusieurs niveaux a
une étude par niveau.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, Optional

from .models import Hand

BANDS = ("micro", "milieu", "hautes")
NAMES = {"micro": "Micro limites", "milieu": "Limites du milieu", "hautes": "Hautes limites"}
LEVELS = {"micro": "NL10 et moins", "milieu": "jusqu'à NL100", "hautes": "NL200 et plus"}
TOPS = (("micro", 0.10), ("milieu", 1.00))  # grosse blinde maximum de chaque niveau ; au-delà : hautes limites


def band_of(bb: float) -> Optional[str]:
    """Le niveau d'une grosse blinde (None : grosse blinde inconnue)."""
    if not bb or bb <= 0:
        return None
    for band, top in TOPS:
        if bb <= top + 1e-9:
            return band
    return "hautes"


def label(band: str) -> str:
    """« Micro limites (NL10 et moins) »."""
    return f"{NAMES[band]} ({LEVELS[band]})"


def select(hands: Iterable[Hand], band: str) -> list[Hand]:
    return [h for h in hands if band_of(h.bb) == band]


def counts(hands: Iterable[Hand]) -> list[tuple[str, int]]:
    """Les niveaux de ces mains et leur nombre, des micro limites aux hautes limites."""
    found = Counter(band_of(h.bb) for h in hands)
    return [(band, found[band]) for band in BANDS if found[band]]


def choose(wanted: Optional[str], found: list[tuple[str, int]]) -> Optional[str]:
    """Le niveau demandé s'il a des mains, sinon celui qui en a le plus (None : aucune main)."""
    if wanted in dict(found):
        return wanted
    return max(found, key=lambda bn: bn[1])[0] if found else None
