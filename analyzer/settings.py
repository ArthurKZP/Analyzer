"""Tes paramètres (onglet Paramètres de l'application) : ton pseudo, si tu es coach, les formats que tu joues et le
nombre de mains à partir duquel un adversaire s'étudie dans l'Étude du field.

Gardés dans la base (réglage « parametres » du compte). Ils valent pour ton espace : chaque élève garde son pseudo
(fiche de l'élève) et tous ses formats.

- hero : ton pseudo principal, choisi parmi ceux que tes historiques marquent comme toi (None : le plus fréquent) ;
  tes pseudos des différents sites sont réunis sous lui.
- coach : True affiche les Élèves dans le menu, False les cache ; None : affichés dès qu'il y a un élève.
- formats : les formats que tu joues, parmi « HU » (heads-up) et « ring » (tables de 3 à 9 joueurs) ; ceux que tu ne
  joues pas sortent des menus et des choix de format. None : tous ceux de tes mains.
- min_hands : mains minimum contre un adversaire pour qu'il ait sa fiche dans l'Étude du field.
"""
from __future__ import annotations

from typing import Optional

from . import db

KEY = "parametres"
FORMATS = ("HU", "ring")
FORMAT_NAMES = {"HU": "Heads-up", "ring": "Tables à plusieurs"}
DEFAULTS = {"hero": None, "coach": None, "formats": None, "min_hands": 50}
MIN_HANDS_RANGE = (10, 5000)


def load() -> dict:
    """Les paramètres, complétés par les valeurs par défaut."""
    found = db.current().setting(KEY, {})
    out = dict(DEFAULTS)
    if isinstance(found, dict):
        out.update({k: v for k, v in found.items() if k in DEFAULTS})
    return out


def check(changes: object) -> dict:
    """Les changements demandés, vérifiés ; ValueError sinon."""
    if not isinstance(changes, dict) or not changes or not set(changes) <= set(DEFAULTS):
        raise ValueError("Paramètre inconnu.")
    out = {}
    for key, value in changes.items():
        if key == "hero":
            if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 100):
                raise ValueError("Pseudo invalide.")
        elif key == "coach":
            if value is not None and not isinstance(value, bool):
                raise ValueError("Coach : oui, non ou automatique.")
        elif key == "formats":
            if value is not None:
                if not isinstance(value, list) or not value or not set(value) <= set(FORMATS):
                    raise ValueError("Choisis au moins un format : heads-up ou tables à plusieurs.")
                value = [f for f in FORMATS if f in value]
        elif key == "min_hands":
            lo, hi = MIN_HANDS_RANGE
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"Mains minimum : de {lo} à {hi}.")
        out[key] = value
    return out


def save(changes: object) -> dict:
    """Enregistre ces changements (check) ; renvoie tous les paramètres."""
    current = load()
    current.update(check(changes))
    db.current().set_setting(KEY, {k: v for k, v in current.items() if v != DEFAULTS[k]})
    return current


def enabled_formats(present: list[str], chosen: Optional[list[str]] = None) -> list[str]:
    """Les formats à montrer parmi ceux de tes mains (present) : ceux que tu joues ; si aucun de ceux-là n'a de
    mains, tous ceux de tes mains (rien ne disparaît sans raison)."""
    chosen = load()["formats"] if chosen is None else chosen
    if not chosen:
        return list(present)
    kept = [f for f in present if f in chosen]
    return kept or list(present)


def plays(fmt: str, chosen: Optional[list[str]] = None) -> bool:
    """Tu joues ce format (sans choix : tous)."""
    chosen = load()["formats"] if chosen is None else chosen
    return not chosen or fmt in chosen


def coach_shown(students: int, chosen: Optional[bool] = None) -> bool:
    """Les Élèves dans le menu : selon ton choix, sinon dès que tu en as un."""
    return chosen if chosen is not None else students > 0
