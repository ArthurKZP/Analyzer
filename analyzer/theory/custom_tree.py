"""Tes modifications de l'arbre d'un coup ou d'un spot d'étude, faites dans l'explorateur : les tailles de mise de
chaque situation (ajoutées, retirées, changées) et les nœuds verrouillés (nodelock).

Gardées dans la base (document « arbre-perso », une clé par coup ou spot : son numéro, ou « spot:srp:KsKd4c ») :

    {"plan": {"bet:fi:": [33, 75, "a"], "raise:fo::0": ["x3"]},
     "locks": [{"path": [...], "player": 1, "title": "Flop · BTN après le check de la BB",
                "actions": ["Check", "Mise 33 %"], "edits": [{"label": "AKs", "freqs": [0, 1], "combos": 4}],
                "combos": {"AhKh": [0, 1], ...}}]}

« plan » remplace les tailles de ces situations (clés et tailles de native/arbre.rs : un % du pot, « geo », « a » pour
le tapis, « x3 » pour une relance à trois fois la mise ; une liste vide retire toute mise ou relance). Un verrou fixe la
stratégie de toutes les mains du joueur à ce nœud : celle de la résolution d'où il a été posé, avec tes changements
(GTOpen verrouille un nœud entier, comme PioSolver) ; le reste de l'arbre s'adapte à la résolution suivante.

Une modification change la requête du solveur : le coup se résout à nouveau, dans une étude à part, et la résolution
d'origine reste. Changer les tailles retire les verrous : leurs chemins ne mènent plus aux mêmes nœuds.
"""
from __future__ import annotations

import re
from itertools import permutations
from typing import Optional

from .. import db
from ..db import documents

KIND = "arbre-perso"
MAX_SIZES = 8      # tailles par situation
MAX_LOCKS = 12     # nœuds verrouillés par coup
_KEY_RE = re.compile(r"^(?:bet:[ftr][oi]:[oix]{0,2}|raise:[ftr][oi]:[oix]{0,2}:\d)$")
SUITS = "cdhs"


def load(ident: str) -> dict:
    """{"plan": {situation: tailles}, "locks": [verrous]} de ce coup ou de ce spot (vides sans modification)."""
    data = documents.get(db.current(), KIND, ident)
    data = data if isinstance(data, dict) else {}
    return {"plan": dict(data.get("plan") or {}), "locks": list(data.get("locks") or [])}


def save(ident: str, data: dict) -> None:
    if data.get("plan") or data.get("locks"):
        documents.put(db.current(), KIND, ident, {"plan": data.get("plan") or {}, "locks": data.get("locks") or []})
    else:
        documents.delete(db.current(), KIND, ident)


def edited(data: Optional[dict]) -> bool:
    return bool(data and (data.get("plan") or data.get("locks")))


# --- tailles --------------------------------------------------------------------------------------------------

def check_key(key: str) -> str:
    """Une clé de situation (native/arbre.rs) ; ValueError sinon."""
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise ValueError("Situation inconnue.")
    return key


def _size(value, raise_key: bool):
    if isinstance(value, bool):
        raise ValueError("Taille invalide.")
    if isinstance(value, (int, float)):
        if not 1 <= value <= 1000:
            raise ValueError("Une taille se donne en % du pot, de 1 à 1000.")
        return int(value) if float(value).is_integer() else round(float(value), 1)
    if value in ("a", "geo", "geo1", "geo2", "geo3"):
        return value
    if isinstance(value, str) and value.startswith("x") and raise_key:
        try:
            mult = float(value[1:].replace(",", "."))
        except ValueError:
            raise ValueError("Multiple invalide : écris x2,5 ou x3.") from None
        if not 1 < mult <= 100:
            raise ValueError("Une relance se fait à plus d'une fois la mise (x1,5 à x100).")
        return "x" + (f"{mult:g}")
    raise ValueError("Taille invalide : un % du pot, « géo », « tapis »" + (" ou x3." if raise_key else "."))


def _order(size) -> tuple:
    if isinstance(size, (int, float)):
        return (0, float(size))
    if size.startswith("x"):
        return (1, float(size[1:]))
    return (2, 0.0) if size.startswith("geo") else (3, 0.0)


def check_sizes(key: str, sizes) -> list:
    """Les tailles d'une situation, vérifiées et rangées (les plus petites d'abord, le tapis en dernier)."""
    if not isinstance(sizes, list) or len(sizes) > MAX_SIZES:
        raise ValueError(f"De 0 à {MAX_SIZES} tailles par situation.")
    raise_key = check_key(key).startswith("raise")
    out = []
    for value in sizes:
        size = _size(value, raise_key)
        if size not in out:
            out.append(size)
    return sorted(out, key=_order)


def set_sizes(ident: str, key: str, sizes: list, base: list) -> int:
    """Remplace les tailles d'une situation (base : celles de l'arbre d'origine ; les mêmes : plus de modification).
    Les verrous sont retirés ; renvoie leur nombre."""
    data = load(ident)
    sizes = check_sizes(key, sizes)
    if sizes == check_sizes(key, list(base)):
        data["plan"].pop(key, None)
    else:
        data["plan"][key] = sizes
    removed = len(data["locks"])
    data["locks"] = []
    save(ident, data)
    return removed


def reset_sizes(ident: str, key: Optional[str] = None) -> int:
    """Revient aux tailles d'origine d'une situation (ou de toutes) ; les verrous sont retirés (renvoie leur nombre)."""
    data = load(ident)
    if key is None:
        data["plan"] = {}
    else:
        data["plan"].pop(check_key(key), None)
    removed = len(data["locks"])
    data["locks"] = []
    save(ident, data)
    return removed


def merged_plan(base: Optional[dict], data: Optional[dict]) -> Optional[dict]:
    """Le plan de l'arbre : celui d'origine (None : l'arbre par défaut), avec tes tailles par-dessus."""
    overrides = (data or {}).get("plan") or {}
    if not overrides:
        return base
    return {**(base or {}), **overrides}


# --- verrous --------------------------------------------------------------------------------------------------

def board_perms(board: list[str]) -> list[dict[str, str]]:
    """Les permutations des couleurs qui laissent le board identique (en ensemble de cartes) : deux mains qu'elles
    échangent sont les mêmes pour le solveur, qui demande alors de les verrouiller ensemble."""
    cards = set(board)
    out = []
    for perm in permutations(SUITS):
        mapping = dict(zip(SUITS, perm))
        if {c[0] + mapping[c[1]] for c in cards} == cards:
            out.append(mapping)
    return out


def _cards(combo: str) -> frozenset:
    return frozenset((combo[:2], combo[2:]))


def mirrors(combo: str, perms: list[dict[str, str]]) -> set[frozenset]:
    """La main et ses équivalents de couleur sur ce board (en paires de cartes)."""
    return {frozenset(c[0] + m[c[1]] for c in (combo[:2], combo[2:])) for m in perms}


def normalize(freqs, n: int) -> list[float]:
    """Une fréquence par action, ramenées à une somme de 1 ; ValueError sinon."""
    if not isinstance(freqs, list) or len(freqs) != n:
        raise ValueError(f"Une fréquence par action ({n}).")
    try:
        values = [float(x) for x in freqs]
    except (TypeError, ValueError):
        raise ValueError("Fréquence invalide.") from None
    if any(x < 0 or x != x for x in values):
        raise ValueError("Fréquence invalide.")
    total = sum(values)
    if total <= 0:
        raise ValueError("Choisis au moins une action.")
    return [round(x / total, 4) for x in values]


def lock_from(node: dict, strategy: list, edits: list[dict], previous: Optional[dict] = None) -> dict:
    """Le verrou d'un nœud : la stratégie de toutes les mains du joueur (strategy : [[main, fréquences...]], la
    résolution en cours), puis tes changements (edits : [{"label", "combos", "freqs"}]) avec les équivalents de
    couleur de chaque main. previous : le verrou déjà posé à ce nœud (ses changements restent listés)."""
    actions = node["actions"]
    n = len(actions)
    if node.get("type") != "action" or n < 2:
        raise ValueError("Ce nœud n'a qu'une action possible : rien à verrouiller.")
    combos = {row[0]: [float(x) for x in row[1:1 + n]] for row in strategy if len(row) == n + 1}
    by_cards = {_cards(c): c for c in combos}
    perms = board_perms(node["board"])
    applied = list((previous or {}).get("edits") or [])
    edited = set((previous or {}).get("edited") or [])
    for edit in edits:
        freqs = normalize(edit.get("freqs"), n)
        targets = set()
        for combo in edit.get("combos") or []:
            if not isinstance(combo, str) or len(combo) != 4 or _cards(combo) not in by_cards:
                continue  # main absente de la range du joueur ici
            targets |= {by_cards[m] for m in mirrors(combo, perms) if m in by_cards}
        if not targets:
            raise ValueError("Aucune de ces mains n'est dans la range du joueur à ce nœud.")
        for combo in targets:
            combos[combo] = freqs
        edited |= targets
        label = str(edit.get("label") or "")[:60] or ", ".join(sorted(targets)[:3])
        applied.append({"label": label, "freqs": freqs, "combos": len(targets)})
    if not applied:
        raise ValueError("Choisis des mains et leur stratégie.")
    return {"path": node["path"], "player": node.get("player"), "street": node.get("street"),
            "board": node.get("board"), "title": node.get("title", ""),
            "actions": node.get("labels") or [a.get("label", "") for a in actions], "edits": applied,
            "edited": sorted(edited), "combos": {c: combos[c] for c in sorted(combos)}}


def put_lock(ident: str, lock: dict) -> None:
    """Pose (ou remplace) le verrou de ce nœud."""
    data = load(ident)
    rest = [x for x in data["locks"] if x["path"] != lock["path"]]
    if len(rest) >= MAX_LOCKS:
        raise ValueError(f"{MAX_LOCKS} nœuds verrouillés au plus : retires-en un.")
    data["locks"] = rest + [lock]
    save(ident, data)


def remove_lock(ident: str, index: Optional[int] = None) -> None:
    """Retire un verrou (son rang dans la liste), ou tous."""
    data = load(ident)
    if index is None:
        data["locks"] = []
    elif isinstance(index, int) and 0 <= index < len(data["locks"]):
        data["locks"].pop(index)
    else:
        raise ValueError("Verrou inconnu.")
    save(ident, data)


def lock_at(data: Optional[dict], path: list) -> Optional[dict]:
    return next((x for x in (data or {}).get("locks") or [] if x["path"] == path), None)


def request_locks(data: Optional[dict]) -> list[dict]:
    """Les verrous pour le solveur (native/main.rs) : toutes les mains de chaque nœud, avec leur stratégie."""
    return [{"path": lock["path"], "mode": {"kind": "hands", "edits": [
        {"combo": combo, "freqs": freqs} for combo, freqs in sorted(lock["combos"].items())]}}
        for lock in (data or {}).get("locks") or []]
