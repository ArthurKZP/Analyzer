"""Mains jouées face au solveur : les erreurs qui coûtent le plus, les plus fréquentes, et les écarts de
l'adversaire à exploiter.

Chaque main résolue (postflop.solve, mise en cache) donne, à chaque décision, l'EV de chaque action pour la
main connue (la tienne ; la sienne au showdown) et la stratégie de toute la range. On en garde un résumé par
main (base de données, table analyses), qu'on regroupe ensuite par situation de la ligne : c-bet, face à la c-bet,
2e barrel, probe… (mêmes clés que le choix des tailles, voir sizing.py).

- Erreur : l'EV perdue par l'action jouée face à la meilleure action pour cette main (en bb). Une action
  que le solveur joue au moins 10 % du temps avec cette main ne coûte rien : à l'équilibre, les actions
  mélangées rapportent autant ; un écart d'EV entre elles vient d'un nœud profond pas tout à fait convergé.
- Fréquences : à chaque décision, la range du solveur aurait foldé / joué passif / joué agressif avec telle
  probabilité ; la somme sur les occurrences donne ce qu'on attendrait d'un joueur théorique, à comparer
  à ce qui a été joué (aucune carte n'est nécessaire : valable pour l'adversaire à chaque main).
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Optional

from .. import db, store
from ..db import analyses as db_analyses, documents
from ..models import Hand
from . import postflop, sizing
from .preflop import MIXED_THRESHOLD

FAMILY = {"SRP": "srp", "pot 3bet": "3bet", "pot 4bet": "4bet"}
ERROR = 0.25  # bb : en dessous, l'écart d'EV ne compte pas comme une erreur
VERSION = 2  # 2 : une main que le solveur ne joue presque jamais sur la ligne est jugée sur l'EV (settle)
CATEGORIES = (("fold", "fold"), ("passive", "check / call"), ("aggressive", "mise / relance"))


def review_dir() -> Path:
    """L'ancien dossier des résumés (repris dans la base, analyzer/db/legacy.py)."""
    return postflop.home() / "revue"


# --- Situations ---------------------------------------------------------------------------------

def situation(node: dict) -> str:
    """Clé de la situation d'une décision, d'après l'historique du nœud : « bet:ti:i » pour une décision
    sans mise en face (miser ou checker), « face:fo::1 » face à une mise, « face:fi::2 » face à une relance."""
    past, aggressor, level = "", None, 0
    for step in node["history"][:-1]:
        if step["kind"] == "card":
            past += "x" if aggressor is None else "o" if aggressor == 0 else "i"
            aggressor, level = None, 0
        elif step["kind"] == "action" and step.get("chosen") is not None:
            if step["actions"][step["chosen"]]["kind"] in ("bet", "raise"):
                aggressor, level = step["player"], level + 1
    where = "ftr"[len(past)] + ("o" if node["player"] == 0 else "i")
    return f"bet:{where}:{past}" if level == 0 else f"face:{where}:{past}:{level}"


def situation_label(key: str, family: str, positions: Optional[tuple[str, str]] = None) -> str:
    """Le nom d'une situation ; positions : (hors de position, en position), celles de la famille par défaut."""
    kind, where, past, *level = key.split(":")
    if kind == "bet":
        return sizing.label(key, family, positions)
    other = "i" if where[1] == "o" else "o"
    n = int(level[0])
    source = f"bet:{where[0]}{other}:{past}" if n == 1 else f"raise:{where[0]}{other}:{past}:{n - 2}"
    return "Face à : " + sizing.label(source, family, positions)


def loss(d: dict) -> Optional[float]:
    """EV perdue par la décision (None : main inconnue) ; nulle pour une action que le solveur mélange."""
    if d["ev_loss"] is None:
        return None
    return 0.0 if (d["frequency"] or 0) >= MIXED_THRESHOLD else d["ev_loss"]


def category(kind: str) -> str:
    return "fold" if kind == "fold" else "passive" if kind in ("check", "call") else "aggressive"


# --- Résumé d'une main -------------------------------------------------------------------------

def digest(spot: postflop.PostflopSpot, raw: dict) -> dict:
    """Ce que l'analyse garde d'une main résolue (quelques Ko au lieu du résultat complet)."""
    hand = spot.hand
    result = postflop.interpret(spot, raw)
    family = FAMILY.get(spot.pot_type, "srp")
    decisions = []
    for k, (d, raw_d) in enumerate(zip(result["decisions"], raw["decisions"])):
        if len(d["actions"]) < 2 or d["chosen"] is None:
            continue  # pas de choix (la BB qui ne peut que checker) ou action hors de l'arbre
        key = situation(raw_d["node"])
        decisions.append({
            "d": k, "street": d["street"], "who": d["who"], "key": key, "label": situation_label(key, family),
            "actions": d["actions"], "chosen": d["chosen"], "played": d["played"], "approx": d["approx"],
            "range": d["range"], "combo": d["combo"], "strategy": d["strategy"], "evs": d["evs"],
            "ev_loss": d["ev_loss"], "frequency": d["frequency"], "verdict": d["verdict"], "pot": d["pot"],
            "board": d["board"], "settled": d["settled"],
        })
    return {
        "v": VERSION, "hand": hand.hand_id, "date": hand.date.strftime("%d/%m/%Y %H:%M"), "villain": spot.villain,
        "hero_position": "BB" if spot.oop == spot.hero else "BTN", "pot_type": spot.pot_type, "family": family,
        "pot": spot.pot_bb, "net": round(hand.net(spot.hero) / hand.bb, 2), "board": hand.board,
        "iterations": raw.get("iterations"), "exploit_pct": raw.get("exploit_pct"), "decisions": decisions,
    }


def upgrade(data: dict) -> dict:
    """Résumé d'avant la version 2 : la stratégie d'une main que le solveur ne joue presque jamais sur la
    ligne n'y veut rien dire (voir postflop.settle) ; ces décisions prennent la meilleure action selon l'EV.
    La présence de la main au nœud est le produit des fréquences de ses actions précédentes sur la ligne."""
    if data.get("v", 1) >= VERSION:
        return data
    path = {"H": 1.0, "V": 1.0}
    for d in data["decisions"]:
        strategy, evs, chosen = d.get("strategy"), d.get("evs"), d["chosen"]
        if strategy is None or not evs or any(e is None for e in evs):
            continue
        played = strategy[chosen]
        if path[d["who"]] < postflop.RARE_PATH:
            d["strategy"] = postflop.best_response(evs)
            d["frequency"] = d["strategy"][chosen]
            d["verdict"] = postflop.verdict(d["frequency"])
            d["settled"] = True
        path[d["who"]] *= played
    data["v"] = VERSION
    return data


def digest_key(spot: postflop.PostflopSpot) -> str:
    # Clé du spot seul (comme une étude) : une mise à jour du pont ne fait pas perdre l'analyse.
    return postflop.study_key(spot.request())


def save_digest(spot: postflop.PostflopSpot, raw: dict) -> dict:
    """Résume la main résolue et garde le résumé dans la base (table analyses)."""
    data = digest(spot, raw)
    key = digest_key(spot)
    base = db.current()
    db_analyses.put(base, key, data)
    _DIGESTS[(base.url, key)] = data
    return data


_DIGESTS: dict[tuple[str, str], dict] = {}  # (base, clé) -> résumé lu


def _read_digest(key: str) -> Optional[dict]:
    """Un résumé enregistré (lu une fois)."""
    base = db.current()
    known = _DIGESTS.get((base.url, key))
    if known is None:
        data = db_analyses.get(base, key)
        if data is None:
            return None
        known = _DIGESTS[(base.url, key)] = upgrade(data)
    return known


def digest_keys() -> set[str]:
    """Les clés des mains déjà résumées."""
    return db_analyses.keys(db.current())


def _done(spot: postflop.PostflopSpot, listed: Optional[set[str]] = None,
          solved: Optional[tuple[set[str], set[str]]] = None) -> Optional[dict]:
    """Le résumé de la main pour cet arbre, s'il est déjà fait (ou son résultat en cache). listed : les clés des
    résumés de la base ; solved : les clés des résultats et des études (postflop.solved_keys : une lecture pour
    toutes les mains)."""
    key = digest_key(spot)
    if listed is None or key in listed:
        data = _read_digest(key)
        if data is not None:
            return data
    request = postflop.solved_request(spot.request(), solved)  # résolu à n'importe quelle précision
    raw = postflop.cached(request) if request else None
    return save_digest(spot, raw) if raw is not None else None


# --- Les clés du spot de chaque main, gardées ----------------------------------------------------------
# Construire le spot d'une main (ranges, tailles) coûte ; pour savoir si elle est déjà analysée, il suffit de ses
# clés (nom du résumé, du résultat, de l'étude). On les garde par main, tant que rien de ce dont le spot dépend
# ne change (signature des réglages : tailles choisies, ranges, précision). Les spots des mains à analyser ne se
# construisent qu'au besoin (PendingSpots).

_KEYS: dict[tuple, Optional[list[dict]]] = {}
_KEYS_LIMIT = 300_000


def _signature() -> tuple:
    """Ce dont dépend le spot d'une main, hors la main : tailles choisies, ranges (charts, ranges ajustées) et
    réglage de précision (leurs révisions dans la base)."""
    from .studyspots import DEPENDS
    return documents.revision(db.current(), *DEPENDS)


def _variant(spot: postflop.PostflopSpot) -> dict:
    request = spot.request()
    default = dict(request, max_iterations=postflop.DEFAULT_ITERATIONS, target_exploit_pct=postflop.DEFAULT_TARGET)
    return {"theory": spot.plan is not None, "digest": digest_key(spot), "base": postflop.base_key(request),
            "solved": (postflop.cache_key(default), postflop.study_key(default)), "pot_type": spot.pot_type}


def _spot_keys(hand: Hand, hero: str, signature: tuple) -> Optional[list[dict]]:
    """Les clés des arbres de la main (tailles théoriques, puis celui d'avant) ; None : pas de spot postflop.
    Gardées en mémoire, et sur disque (analyzer.store) d'une session à l'autre."""
    key = (hand.hand_id, hero, signature)
    if key in _KEYS:
        return _KEYS[key]
    stored_key = f"{hand.site}:{hand.hand_id}|{hero}|{_signature_hash(signature)}"
    stored = store.get("spots", stored_key)
    if stored is not None:
        variants = stored["variants"]
    else:
        try:
            spot = postflop.build_spot(hand, hero)
        except postflop.Unsupported:
            variants = None
        else:
            variants = [_variant(spot)]
            if spot.plan is not None:
                variants.append(_variant(postflop.build_spot(hand, hero, theory=False)))
        store.put("spots", stored_key, {"variants": variants})
    if len(_KEYS) >= _KEYS_LIMIT:
        _KEYS.clear()
    _KEYS[key] = variants
    return variants


_SIGNATURES: dict[tuple, str] = {}


def _signature_hash(signature: tuple) -> str:
    """L'empreinte des réglages ; à chaque nouvelle empreinte, les clés gardées pour les anciennes s'effacent."""
    if signature not in _SIGNATURES:
        _SIGNATURES.clear()
        digest = _SIGNATURES[signature] = hashlib.sha1(repr(signature).encode()).hexdigest()[:12]
        store.retain("spots", lambda key: key.endswith("|" + digest))
    return _SIGNATURES[signature]


def _done_by_keys(hand: Hand, hero: str, variants: list[dict], listed: set[str],
                  solved: tuple[set[str], set[str]], precisions: dict) -> Optional[dict]:
    """Comme _done, avec les clés gardées : le spot ne se reconstruit que si un résultat attend son résumé."""
    for variant in variants:
        if variant["digest"] in listed:
            data = _read_digest(variant["digest"])
            if data is not None:
                return data
        result, study = variant["solved"]
        if variant["base"] in precisions or result in solved[0] or study in solved[1]:
            spot = postflop.build_spot(hand, hero, theory=variant["theory"])
            data = _done(spot, listed, solved)
            if data is not None:
                return data
    return None


def _pot_size(hand: Hand) -> float:
    return sum(a.amount for a in hand.actions) / hand.bb


class PendingSpots:
    """Les mains à analyser, les plus gros pots d'abord : leurs spots se construisent quand on les parcourt.
    pot_types : le type de pot de chacune (SRP, pot 3bet…), connu sans construire le spot."""

    def __init__(self, hands: list[Hand], hero: str, pot_types: Optional[list[str]] = None):
        self.hands, self.hero = hands, hero
        self.pot_types = pot_types if pot_types is not None else []

    def __len__(self) -> int:
        return len(self.hands)

    def __bool__(self) -> bool:
        return bool(self.hands)

    def __iter__(self):
        for hand in self.hands:
            try:
                yield postflop.build_spot(hand, self.hero)
            except postflop.Unsupported:
                continue

    def __getitem__(self, index):
        if isinstance(index, slice):
            return PendingSpots(self.hands[index], self.hero, self.pot_types[index])
        return postflop.build_spot(self.hands[index], self.hero)

    @property
    def hand_ids(self) -> list[str]:
        return [h.hand_id for h in self.hands]


def collect(hands: list[Hand], hero: str) -> tuple[list[dict], PendingSpots]:
    """Résumés des mains déjà résolues, et mains à résoudre (les plus gros pots d'abord).

    Une main analysée avec l'arbre d'avant les tailles théoriques (tailles fixes) garde son analyse."""
    done, todo = [], []
    listed = digest_keys()
    solved = postflop.solved_keys()
    signature = _signature()
    precisions = postflop.precision_entries()
    for hand in hands:
        variants = _spot_keys(hand, hero, signature)
        if variants is None:
            continue
        data = _done_by_keys(hand, hero, variants, listed, solved, precisions)
        if data is not None:
            done.append(data)
        else:
            todo.append((hand, variants[0].get("pot_type", "SRP")))
    todo.sort(key=lambda t: -_pot_size(t[0]))
    return done, PendingSpots([h for h, _ in todo], hero, [t for _, t in todo])


def analyze(hands: list[Hand], hero: str, log=print, limit: Optional[int] = None) -> int:
    """Résout les mains pas encore analysées (les plus gros pots d'abord) ; renvoie leur nombre."""
    _, todo = collect(hands, hero)
    todo = todo[:limit] if limit else todo
    for k, spot in enumerate(todo, 1):
        log(f"[{k}/{len(todo)}] main {spot.hand.hand_id} ({spot.pot_type}, {spot.villain}) : résolution…")
        raw = postflop.solve(spot.request())
        data = save_digest(spot, raw)
        lost = sum(loss(d) or 0 for d in data["decisions"] if d["who"] == "H")
        log(f"    {raw['iterations']} itérations, {raw['seconds']:.0f} s ; EV perdue : {lost:.2f} bb")
    return len(todo)


def saved_digests() -> list[dict]:
    """Les résumés de toutes les mains déjà passées au solveur (les tiennes et celles des élèves)."""
    out = []
    for _, data in db_analyses.every(db.current()):
        try:
            out.append(upgrade(data))
        except (ValueError, KeyError):
            continue
    return out


def hero_losses(digests: list[dict]) -> dict[str, float]:
    """L'EV perdue après le flop par le joueur de chaque main analysée (main -> bb)."""
    return {g["hand"]: sum(loss(d) or 0.0 for d in g["decisions"] if d["who"] == "H")
            for g in digests if g.get("hand")}


# --- Regroupements -------------------------------------------------------------------------------

def decisions_of(digests: list[dict], who: str) -> list[tuple[dict, dict]]:
    return [(g, d) for g in digests for d in g["decisions"] if d["who"] == who]


def costly(digests: list[dict], who: str, n: int = 15) -> list[tuple[dict, dict]]:
    """Les décisions qui ont coûté le plus (main connue)."""
    rows = [(g, d) for g, d in decisions_of(digests, who) if (loss(d) or 0) >= ERROR]
    return sorted(rows, key=lambda gd: -loss(gd[1]))[:n]


def by_situation(digests: list[dict], who: str) -> list[dict]:
    """Par situation : occurrences, erreurs et EV perdue (main connue), fréquences jouées et théoriques."""
    groups: dict[tuple, dict] = {}
    for g, d in decisions_of(digests, who):
        key = (g["family"], d["key"])
        s = groups.setdefault(key, {"family": g["family"], "key": d["key"], "label": d["label"], "n": 0,
                                    "known": 0, "errors": 0, "lost": 0.0,
                                    "observed": {c: 0 for c, _ in CATEGORIES},
                                    "expected": {c: 0.0 for c, _ in CATEGORIES},
                                    "variance": {c: 0.0 for c, _ in CATEGORIES}})
        s["n"] += 1
        s["observed"][category(d["actions"][d["chosen"]]["kind"])] += 1
        shares = {c: 0.0 for c, _ in CATEGORIES}
        for a, f in zip(d["actions"], d["range"]):
            shares[category(a["kind"])] += f
        for c, p in shares.items():
            s["expected"][c] += p
            s["variance"][c] += p * (1 - p)
        lost = loss(d)
        if lost is not None:
            s["known"] += 1
            s["lost"] += lost
            s["errors"] += lost >= ERROR
    return sorted(groups.values(), key=lambda s: (-s["lost"], -s["n"]))


def deviations(group: dict, min_n: int = 8) -> list[dict]:
    """Écarts de fréquence significatifs d'une situation : catégorie, jouée, théorique, confiance."""
    out = []
    n = group["n"]
    if n < min_n:
        return out
    for c, label in CATEGORIES:
        obs, exp, var = group["observed"][c], group["expected"][c], group["variance"][c]
        if var <= 0:
            continue
        z = (obs - exp) / math.sqrt(var)
        gap = (obs - exp) / n
        if abs(gap) >= 0.10 and abs(z) >= 1.5:
            out.append({"category": c, "label": label, "observed": obs / n, "expected": exp / n, "z": z,
                        "confidence": "solide" if abs(z) >= 2.5 else "indicatif"})
    return out


def exploit(group: dict, dev: dict, villain: bool = True) -> str:
    """Ce que l'écart veut dire et comment en profiter (ou le corriger)."""
    facing = group["key"].startswith("face:")
    more = dev["observed"] > dev["expected"]
    c = dev["category"]
    if villain:
        if facing and c == "fold":
            return ("Il folde trop : mise et relance plus souvent en bluff dans cette situation." if more else
                    "Il ne folde pas assez : mise plus fin pour la valeur, bluffe moins.")
        if facing and c == "aggressive":
            return ("Il relance trop : ses relances sont plus légères, paie-les ou sur-relance plus large." if more
                    else "Il relance rarement : ses relances sont fortes, respecte-les ; tes mises passent plus.")
        if facing and c == "passive":
            return ("Il paie trop : value bet plus fin, moins de bluffs." if more else
                    "Il paie peu : ses calls sont forts, bluffe davantage ce qu'il folde.")
        if c == "aggressive":
            return ("Il mise trop : ses mises sont plus légères, paie plus large et relance en bluff." if more else
                    "Il mise rarement : ses mises sont fortes ; quand il checke, mise plus souvent.")
        if c == "passive":
            return ("Il checke trop : quand il checke, attaque le pot plus souvent." if more else
                    "Il mise plus que la théorie : ses mises sont plus légères, défends plus large et relance plus.")
        return "Écart de fréquence à surveiller."
    if facing and c == "fold":
        return "Tu foldes trop ici : défends plus large." if more else "Tu ne foldes pas assez ici : lâche tes mains faibles."
    if c == "aggressive":
        return "Tu es plus agressif que la théorie ici." if more else "Tu n'es pas assez agressif ici."
    return "Tu joues plus passif que la théorie ici." if more else "Tu joues moins passif que la théorie ici."
