"""Tes paramètres (onglet Paramètres de l'application) : tes pseudos réunis sous ton nom, si tu es coach, les formats
que tu joues et le nombre de mains à partir duquel un adversaire s'étudie dans l'Étude du field.

Gardés dans la base (réglage « parametres » du compte). Ils valent pour ton espace : chaque élève garde son pseudo
(fiche de l'élève) et tous ses formats.

- hero : le nom sous lequel tes pseudos sont réunis et analysés ensemble (None : ton pseudo le plus fréquent) ; un
  nom à toi, qui n'est pas celui d'un adversaire.
- hero_excluded : des pseudos que tes historiques marquent comme toi mais qui ne le sont pas (les mains d'un autre
  joueur importées chez toi) : leurs mains sortent de tes analyses.
- hero_added : des pseudos à toi que tes historiques ne marquent pas comme toi : leurs mains sans héros repéré
  deviennent les tiennes.
- coach : True affiche les Élèves dans le menu, False les cache ; None : affichés dès qu'il y a un élève.
- formats : les formats que tu joues, parmi « HU » (heads-up) et « ring » (tables de 3 à 9 joueurs) ; ceux que tu ne
  joues pas sortent des menus et des choix de format. None : tous ceux de tes mains.
- min_hands : mains minimum contre un adversaire pour qu'il ait sa fiche dans l'Étude du field.
- references : les joueurs de référence, [{"name": …, "pseudos": […]}] : un bon joueur dont tu as importé les
  historiques (ses pseudos y sont marqués comme héros) ; ses mains sortent de tes analyses et il est étudié à part
  (menu Joueurs de référence). Chaque pseudo n'appartient qu'à un joueur de référence.
- situation_refs : tes repères des résultats par situation (le bilan, analyzer/situations.py), par format :
  {"ring": {situation: bb/100 ou None}, "HU": {…}} ; None : les repères par défaut. Ils valent aussi pour tes élèves.
- stat_selection : tes stats de l'onglet Statistiques, dans ton ordre (analyzer/statlib.py), par format :
  {"HU": [stat, …], "ring": […]} ; un format absent (ou None) : la sélection par défaut. Elle vaut aussi pour tes
  élèves et tes joueurs de référence.
"""
from __future__ import annotations

from typing import Optional

from . import db

KEY = "parametres"
FORMATS = ("HU", "ring")
FORMAT_NAMES = {"HU": "Heads-up", "ring": "Tables à plusieurs"}
DEFAULTS = {"hero": None, "hero_excluded": [], "hero_added": [], "coach": None, "formats": None, "min_hands": 50,
            "references": [], "situation_refs": None, "stat_selection": None}
REF_RANGE = 10_000  # un repère de situation, en bb/100 : entre -REF_RANGE et REF_RANGE
MIN_HANDS_RANGE = (10, 5000)
MAX_NAME = 60      # caractères du nom
MAX_PSEUDOS = 200  # pseudos retirés ou ajoutés
MAX_REFERENCES = 20  # joueurs de référence


def load() -> dict:
    """Les paramètres, complétés par les valeurs par défaut (des listes neuves à chaque fois)."""
    found = db.current().setting(KEY, {})
    out = {k: list(v) if isinstance(v, list) else v for k, v in DEFAULTS.items()}
    if isinstance(found, dict):
        out.update({k: v for k, v in found.items() if k in DEFAULTS})
    return out


def _name(value: object, what: str) -> str:
    value = " ".join(value.split()) if isinstance(value, str) else ""
    if not 0 < len(value) <= MAX_NAME:
        raise ValueError(f"{what} : de 1 à {MAX_NAME} caractères.")
    return value


def reference_id(name: str) -> str:
    """L'identifiant d'un joueur de référence dans les adresses (son nom simplifié)."""
    from .cli import slugify
    return slugify(name)[:40] or "joueur"


def _references(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_REFERENCES:
        raise ValueError(f"Joueurs de référence : {MAX_REFERENCES} au plus.")
    out, idents, owned = [], set(), set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "pseudos"}:
            raise ValueError("Joueur de référence invalide.")
        name = _name(item["name"], "Le nom du joueur de référence")
        pseudos = _pseudos(item["pseudos"])
        if not pseudos:
            raise ValueError(f"« {name} » : choisis au moins un pseudo.")
        ident = reference_id(name)
        if ident in idents:
            raise ValueError(f"Deux joueurs de référence portent le nom « {name} ».")
        if owned & set(pseudos):
            raise ValueError(f"Le pseudo « {sorted(owned & set(pseudos))[0]} » est déjà celui d'un autre joueur de "
                             "référence.")
        idents.add(ident)
        owned |= set(pseudos)
        out.append({"name": name, "pseudos": pseudos})
    return out


def reference_pseudos(conf: dict) -> set[str]:
    """Les pseudos des joueurs de référence (ils ne sont pas les tiens)."""
    return {p for ref in conf["references"] for p in ref["pseudos"]}


def _pseudos(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_PSEUDOS or not all(
            isinstance(p, str) and 0 < len(p) <= 100 for p in value):
        raise ValueError("Liste de pseudos invalide.")
    return sorted(set(value), key=str.lower)


def _situation_refs(value: object) -> Optional[dict]:
    """Les repères des résultats par situation : None (ceux par défaut), ou {format: {situation: bb/100 ou None}}."""
    if value is None:
        return None
    from .situations import ORDER  # (le module des situations lit ces paramètres)
    keys = {g.key for g in ORDER}
    if not isinstance(value, dict) or not set(value) <= set(FORMATS):
        raise ValueError("Repères : par format, heads-up ou tables à plusieurs.")
    out = {}
    for fmt, refs in value.items():
        if not isinstance(refs, dict) or not set(refs) <= keys:
            raise ValueError("Repère d'une situation inconnue.")
        for ref in refs.values():
            if ref is not None and (isinstance(ref, bool) or not isinstance(ref, (int, float))
                                    or not -REF_RANGE <= ref <= REF_RANGE):
                raise ValueError(f"Un repère est un nombre de bb/100, entre -{REF_RANGE} et {REF_RANGE}.")
        out[fmt] = {k: refs[k] for k in sorted(refs)}
    return out


def _stat_selection(value: object) -> Optional[dict]:
    """Tes stats de l'onglet Statistiques : None (celles par défaut), ou {format: [stat, …]} sans doublon."""
    if value is None:
        return None
    from .statlib import BY_ID, MAX_SELECTED  # (le catalogue lit les modules de stats)
    if not isinstance(value, dict) or not set(value) <= set(FORMATS):
        raise ValueError("Stats : par format, heads-up ou tables à plusieurs.")
    out = {}
    for fmt, ids in value.items():
        if not isinstance(ids, list) or len(ids) > MAX_SELECTED or len(set(map(str, ids))) != len(ids):
            raise ValueError("Stats : une liste, sans doublon.")
        unknown = [i for i in ids if not isinstance(i, str) or i not in BY_ID or fmt not in BY_ID[i].formats()]
        if unknown:
            raise ValueError(f"Stat inconnue dans ce format : {unknown[0]}.")
        out[fmt] = list(ids)
    return out


def check(changes: object) -> dict:
    """Les changements demandés, vérifiés ; ValueError sinon."""
    if not isinstance(changes, dict) or not changes or not set(changes) <= set(DEFAULTS):
        raise ValueError("Paramètre inconnu.")
    out = {}
    for key, value in changes.items():
        if key == "hero":
            if value is not None:
                value = _name(value, "Ton nom")
        elif key == "references":
            value = _references(value)
        elif key == "situation_refs":
            value = _situation_refs(value)
        elif key == "stat_selection":
            value = _stat_selection(value)
        elif key in ("hero_excluded", "hero_added"):
            value = _pseudos(value)
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
