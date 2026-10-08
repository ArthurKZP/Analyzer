"""Alias : plusieurs pseudos d'un même joueur (un par site, ou un pseudo changé) regroupés sous un nom choisi.

Le regroupement est gardé pour tout le compte (base de données, réglage « alias » : {pseudo: alias}) et s'applique au
chargement des mains, avant toute analyse : chaque pseudo du groupe y prend le nom de l'alias (comme le héros prend
son pseudo principal sur tous les sites). Le type choisi pour un de ses pseudos passe à l'alias s'il n'en a pas.
Défaire un alias rend leurs pseudos aux mains, à la lecture suivante.
"""
from __future__ import annotations

from typing import Optional

from . import db, players
from .models import Hand

KEY = "alias"
MAX_NAME = 60


def load() -> dict[str, str]:
    """{pseudo: alias} (un alias qui garde le nom d'un de ses pseudos n'a pas d'entrée pour celui-là)."""
    found = db.current().setting(KEY, {})
    return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}


def groups(mapping: Optional[dict[str, str]] = None) -> dict[str, list[str]]:
    """{alias: [les pseudos regroupés sous lui]}, par ordre alphabétique."""
    mapping = load() if mapping is None else mapping
    out: dict[str, list[str]] = {}
    for pseudo, alias in mapping.items():
        out.setdefault(alias, []).append(pseudo)
    return {alias: sorted(names, key=str.lower) for alias, names in sorted(out.items(), key=lambda kv: kv[0].lower())}


def _clean(name: object) -> str:
    text = " ".join(str(name or "").split())
    if not text or len(text) > MAX_NAME:
        raise ValueError("Nom vide ou trop long (60 caractères au plus).")
    return text


def group(alias: str, pseudos: list[str]) -> dict[str, list[str]]:
    """Regroupe ces pseudos (et ceux de leurs alias) sous cet alias ; renvoie les groupes. ValueError si rien à
    regrouper."""
    alias = _clean(alias)
    if not all(isinstance(p, str) and 0 < len(p) <= 100 for p in pseudos):
        raise ValueError("Pseudo invalide.")
    names = set(pseudos)  # tels qu'à la table
    mapping = load()
    for name in list(names):  # un alias regroupé : tous ses pseudos suivent
        names |= {p for p, a in mapping.items() if a == name}
    names.discard(alias)
    if not names and alias not in mapping:
        raise ValueError("Choisis au moins un pseudo à regrouper sous cet alias.")
    kinds = players.load()
    if alias not in kinds:  # le type choisi pour un de ses pseudos vaut pour l'alias
        chosen = next((kinds[n] for n in sorted(names) if n in kinds), None)
        if chosen:
            players.set_kind(alias, chosen)
    for name in names:
        mapping[name] = alias
    mapping.pop(alias, None)  # l'alias garde son propre nom
    for pseudo, target in list(mapping.items()):  # pas de chaîne : un ancien alias devenu pseudo pointe au bout
        if target in mapping:
            mapping[pseudo] = mapping[target]
    db.current().set_setting(KEY, mapping)
    return groups(mapping)


def ungroup(alias: str) -> dict[str, list[str]]:
    """Défait un alias : ses pseudos redeviennent des joueurs à part ; renvoie les groupes."""
    mapping = {p: a for p, a in load().items() if a != alias}
    db.current().set_setting(KEY, mapping)
    return groups(mapping)


def apply(hands: list[Hand], mapping: Optional[dict[str, str]] = None) -> None:
    """Chaque pseudo regroupé prend le nom de son alias dans les mains (un joueur déjà assis sous ce nom à la même
    table garde le sien) ; le héros de la main aussi, s'il est du groupe."""
    mapping = load() if mapping is None else mapping
    if not mapping:
        return
    for h in hands:
        for name in [n for n in h.seats if n in mapping]:
            h.rename(name, mapping[name])
            if h.hero == name and name not in h.seats:
                h.hero = mapping[name]
