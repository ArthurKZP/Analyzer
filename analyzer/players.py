"""Type des adversaires : régulier ou récréatif.

Contre un récréatif, le bon jeu est l'exploitation, pas la théorie : ses mains sont exclues des comparaisons
au solveur (« Face au solveur », préflop), ses écarts restent affichés pour en profiter.

Le type se choisit dans l'application (fiche de l'adversaire, Leakfinding, Étude du field) ; sans choix, une
suggestion d'après ses stats, sinon « régulier ». Le classement est gardé par joueur, pas par duel (base de données,
table adversaires ; avant : ~/.analyzer/joueurs.json, repris à la première ouverture) : le même aux tables à
plusieurs. Les signaux de la suggestion, eux, dépendent du jeu : ceux du heads-up (bouton / BB), ou aux tables à
plusieurs (ring_signals) ceux d'un joueur trop large et trop passif, à la taille de table où il a le plus joué.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import db
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
    """L'ancien fichier des types (repris dans la base, analyzer/db/legacy.py)."""
    return postflop.home() / "joueurs.json"


def load() -> dict[str, str]:
    """Types choisis à la main : {joueur: "reg" | "rec"}."""
    base = db.current()
    return {name: kind for name, kind in base.all("SELECT nom, type FROM adversaires WHERE compte_id = ?",
                                                    (base.account(),)) if kind in KINDS}


def set_kind(name: str, kind: Optional[str]) -> None:
    """Fixe le type d'un joueur ; None revient à la suggestion."""
    if kind is not None and kind not in KINDS:
        raise ValueError(kind)
    base = db.current()
    if kind is None:
        base.execute("DELETE FROM adversaires WHERE compte_id = ? AND nom = ?", (base.account(), name))
    else:
        base.execute("INSERT INTO adversaires (compte_id, nom, type, maj_le) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT (compte_id, nom) DO UPDATE SET type = excluded.type, maj_le = excluded.maj_le",
                     (base.account(), name, kind, db.now()))


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


# Aux tables à plusieurs : (stat de ring.read, sens, seuil par taille de table — 3 joueurs, 4 à 6, 7 et plus —, texte).
RING_SIGNALS = (
    ("vpip", ">", {"3": 60, "6": 40, "9": 32}, "joue {v} % de ses mains"),
    ("limp", ">", {"3": 15, "6": 10, "9": 10}, "limpe {v} % quand il parle le premier"),
    ("flat", ">", {"3": 45, "6": 30, "9": 30}, "paie {v} % des ouvertures"),
    ("fold_cbet", "<", {"3": 25, "6": 25, "9": 25}, "ne folde que {v} % face à la c-bet"),
)
RING_GAP = 18  # points de VPIP sans relance (VPIP - PFR) : un joueur qui paie beaucoup


def ring_suggest(profile: Optional[dict]) -> tuple[Optional[str], list[str]]:
    """Suggestion aux tables à plusieurs, sur ses fréquences à la taille de table où il a le plus de mains (profile :
    ring.profiles, {taille : « 3 », « 6 » ou « 9 » -> {"hands": n, stat: Ratio}} ; les normes d'un régulier changent
    avec la table), comme suggest."""
    if not profile:
        return None, []
    size, st = max(profile.items(), key=lambda kv: kv[1]["hands"])
    if st["hands"] < MIN_HANDS:
        return None, []
    reasons = []
    for key, sense, limits, text in RING_SIGNALS:
        ratio = st.get(key)
        if ratio is None or ratio.opps < MIN_OPPS:
            continue
        value = ratio.pct
        if (value > limits[size]) if sense == ">" else (value < limits[size]):
            reasons.append(text.format(v=round(value)))
    vpip, pfr = st.get("vpip"), st.get("pfr")
    if vpip and pfr and vpip.opps >= MIN_HANDS and vpip.pct - pfr.pct > RING_GAP:
        reasons.append(f"paie sans relancer (VPIP {round(vpip.pct)} %, PFR {round(pfr.pct)} %)")
    if len(reasons) >= 2:
        return "rec", reasons
    return ("reg" if not reasons else None), reasons


def classify(names: list[str], stats: dict[str, PlayerStats], ring_profiles: Optional[dict] = None) -> dict[str, dict]:
    """Type retenu pour chaque joueur : {"kind", "source" (toi | suggestion | défaut), "suggestion", "reasons"} ;
    ring_profiles : la suggestion aux tables à plusieurs (ring_suggest) plutôt qu'en heads-up."""
    chosen = load()
    out = {}
    for name in names:
        suggestion, reasons = (ring_suggest(ring_profiles.get(name)) if ring_profiles is not None
                               else suggest(stats.get(name)))
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
