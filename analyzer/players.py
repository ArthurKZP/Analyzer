"""Type des adversaires : régulier ou récréatif.

Contre un récréatif, le bon jeu est l'exploitation, pas la théorie : ses mains sont exclues des comparaisons
au solveur (« Face au solveur », préflop), ses écarts restent affichés pour en profiter.

Le type se choisit dans l'application (fiche de l'adversaire) ; sans choix, une suggestion d'après ses stats,
sinon « régulier ». Le classement est gardé par joueur, pas par duel (~/.analyzer/joueurs.json) : il servira
tel quel aux tables à 3 ou 6 joueurs. Les signaux de la suggestion, eux, sont ceux du heads-up (bouton / BB).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .stats import PlayerStats
from .theory import postflop

KINDS = {"reg": "Régulier", "rec": "Récréatif"}
MIN_HANDS = 40   # en dessous, pas de suggestion
MIN_OPPS = 15    # occasions minimum pour qu'un signal compte

# (stat, sens, seuil, texte) : un signal de jeu récréatif quand la stat dépasse (>) ou reste sous (<) le seuil.
SIGNALS = (
    ("sb_first.call", ">", 20, "limpe {v} % de ses boutons"),
    ("sb_first.raise", "<", 50, "n'ouvre que {v} % de ses boutons"),
    ("bb_vs_open.fold", ">", 55, "folde {v} % de ses BB face à l'open"),
    ("bb_vs_open.raise", "<", 5, "ne 3bette que {v} % face à l'open"),
    ("sb_vs_3bet.fold", ">", 75, "folde {v} % de ses opens face au 3bet"),
)


def path() -> Path:
    return postflop.home() / "joueurs.json"


def load() -> dict[str, str]:
    """Types choisis à la main : {joueur: "reg" | "rec"}."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and v in KINDS} if isinstance(data, dict) else {}


def set_kind(name: str, kind: Optional[str]) -> None:
    """Fixe le type d'un joueur ; None revient à la suggestion."""
    if kind is not None and kind not in KINDS:
        raise ValueError(kind)
    data = load()
    if kind is None:
        data.pop(name, None)
    else:
        data[name] = kind
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def suggest(st: Optional[PlayerStats]) -> tuple[Optional[str], list[str]]:
    """Suggestion d'après les stats : (« rec » avec deux signaux ou plus, « reg » sans signal, None entre les
    deux ou avec trop peu de mains), et les signaux relevés."""
    if st is None or st.hands < MIN_HANDS:
        return None, []
    reasons = []
    for key, sense, limit, text in SIGNALS:
        ratio = st.r(key)
        value = ratio.pct
        if value is None or ratio.opps < MIN_OPPS:
            continue
        if (value > limit) if sense == ">" else (value < limit):
            reasons.append(text.format(v=round(value)))
    _, afq = st.aggression()
    if afq is not None and afq < 30 and sum(sum(c.values()) for c in st.street_actions.values()) >= 60:
        reasons.append(f"très passif après le flop (agressivité {round(afq)} %)")
    if len(reasons) >= 2:
        return "rec", reasons
    return ("reg" if not reasons else None), reasons


def classify(names: list[str], stats: dict[str, PlayerStats]) -> dict[str, dict]:
    """Type retenu pour chaque joueur : {"kind", "source" (toi | suggestion | défaut), "suggestion", "reasons"}."""
    chosen = load()
    out = {}
    for name in names:
        suggestion, reasons = suggest(stats.get(name))
        if name in chosen:
            kind, source = chosen[name], "toi"
        elif suggestion:
            kind, source = suggestion, "suggestion"
        else:
            kind, source = "reg", "défaut"
        out[name] = {"kind": kind, "source": source, "suggestion": suggestion, "reasons": reasons}
    return out


def describe(info: dict) -> str:
    """« Récréatif (suggéré : limpe 40 % de ses boutons, …) », pour les pages."""
    text = KINDS[info["kind"]]
    if info["source"] == "toi":
        return text + " (classé par toi)"
    if info["source"] == "suggestion":
        return text + " (suggéré : " + ", ".join(info["reasons"]) + ")" if info["reasons"] else text + " (suggéré)"
    return text + " (par défaut)"
