"""Catalogue des stats, repères indicatifs et lectures automatiques (exploits / fuites).

Les repères correspondent à un régulier HU solide en 100bb+ ; ce sont des ordres
de grandeur pour repérer les écarts, pas des fréquences « GTO » exactes.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

from .cards import describe_holding
from .models import BET, CALL, FOLD, RAISE
from .stats import PlayerStats, Ratio

MIN_SAMPLE = 15


@dataclass(frozen=True)
class Reading:
    """Lecture d'un écart : constat, exploit contre lui, correction pour toi."""
    fact: str
    exploit: str
    fix: str


@dataclass(frozen=True)
class StatDef:
    key: str
    label: str
    ref: Optional[tuple[float, float]] = None
    high: Optional[Reading] = None  # au-dessus du repère
    low: Optional[Reading] = None  # en dessous du repère


R = Reading
SECTIONS: list[tuple[str, list[StatDef]]] = [
    (
        "Préflop — au bouton (SB)",
        [
            StatDef("vpip_sb", "VPIP bouton", (80, 95)),
            StatDef("sb_first.raise", "Open-raise", (75, 92),
                    R("Ouvre très large au bouton", "défends plus large en BB et 3bet plus souvent (value + bluffs avec bloqueurs)",
                      "ton open est très large : garde des mains qui jouent bien contre 3bet"),
                    R("Ouvre serré au bouton", "respecte ses opens et 3bet surtout en value",
                      "tu ouvres peu : en HU le bouton se joue large")),
            StatDef("sb_first.call", "Limp"),
            StatDef("sb_first.fold", "Fold d'entrée", (8, 22),
                    R("Folde souvent son bouton", "il te laisse des blindes gratuites", "tu folds trop ton bouton : ouvre plus large"),
                    R("Ne folde presque jamais son bouton", "ses opens contiennent des mains faibles : défends et 3bet plus",
                      "tu joues presque tout au bouton : attention aux mains dominées")),
            StatDef("sb_vs_3bet.fold", "Fold vs 3bet", (35, 50),
                    R("Folde trop contre 3bet", "3bet light (mains jouables, bloqueurs A/K)",
                      "défends plus contre 3bet : call IP plus large, 4bet bluff avec bloqueurs"),
                    R("Ne lâche presque jamais contre 3bet", "3bet plus large en value, réduis les 3bets bluff",
                      "tu défends très large contre 3bet : attention aux mains dominées")),
            StatDef("sb_vs_3bet.call", "Call 3bet", (35, 55)),
            StatDef("sb_vs_3bet.raise", "4bet", (8, 16),
                    R("4bet souvent", "5bet jam ou call ses 4bets avec tes mains fortes, arrête les 3bets bluff marginaux",
                      "tu 4bet beaucoup : rentable s'il folde, risqué s'il 5bet"),
                    R("4bet rarement", "ses 4bets sont très forts : folde tes 3bets bluff",
                      "ajoute des 4bets bluff (bloqueurs A/K) pour qu'il ne 3bet pas gratuitement")),
            StatDef("sb_vs_iso.fold", "Limp puis fold vs iso"),
            StatDef("sb_vs_iso.raise", "Limp-raise"),
        ],
    ),
    (
        "Préflop — en big blind",
        [
            StatDef("vpip_bb", "VPIP big blind"),
            StatDef("bb_vs_open.fold", "Fold vs open", (25, 42),
                    R("Folde trop sa BB", "open-raise plus large (voire 100 %) et petit", "défends ta BB plus large"),
                    R("Défend très large sa BB", "moins de c-bets bluff, value-bets plus fins postflop",
                      "tu défends très large : il faut ensuite bien réaliser ton équité postflop")),
            StatDef("bb_vs_open.call", "Call open", (40, 58)),
            StatDef("bb_vs_open.raise", "3bet vs open", (12, 20),
                    R("3bet très souvent", "ne folde pas trop contre 3bet : call IP plus large, 4bet bluff avec bloqueurs",
                      "tu 3bet beaucoup : rentable seulement s'il folde assez"),
                    R("3bet rarement", "ses 3bets sont forts : folde tes mains marginales",
                      "ajoute des 3bets (value + bluffs) en BB")),
            StatDef("bb_vs_limp.raise", "Iso-raise vs limp"),
            StatDef("bb_vs_4bet.fold", "Fold vs 4bet", (40, 65),
                    R("Folde trop contre 4bet", "4bet bluff plus souvent", "tu folds trop contre 4bet : ses 4bets bluff deviennent rentables"),
                    R("Paye ou 5bet beaucoup contre 4bet", "4bet surtout en value", "tu continues beaucoup contre 4bet")),
            StatDef("bb_vs_4bet.raise", "5bet"),
        ],
    ),
    (
        "Postflop — quand il est l'agresseur préflop",
        [
            StatDef("cbet_flop_ip", "C-bet flop IP", (55, 80),
                    R("C-bet presque tout au flop", "check-raise et float plus souvent", "tu c-bet presque tout : il peut check-raise plus"),
                    R("C-bet peu au flop", "quand il mise il a souvent une main, et ses checks sont prenables (probe turn)",
                      "tu c-bet peu : il peut attaquer tes checks au turn")),
            StatDef("cbet_flop_oop", "C-bet flop OOP (pot 3bet)", (40, 65),
                    R("C-bet OOP très souvent en pot 3bet", "défends plus (call/raise) en pot 3bet", "tu c-bet beaucoup OOP : attention aux raises"),
                    R("C-bet OOP rarement", "mise quand il checke (float)", "tu c-bet peu OOP : il peut miser tous tes checks")),
            StatDef("cbet_turn", "Barrel turn", (45, 65),
                    R("Double-barrel beaucoup", "paye plus large au turn avec tes bluff-catchers", "tu double-barrel beaucoup : surveille tes bluffs"),
                    R("Lâche souvent au turn", "float le flop pour prendre le turn", "tu lâches souvent au turn : il peut floater le flop")),
            StatDef("cbet_river", "Barrel river", (40, 65),
                    R("Triple-barrel beaucoup", "paye la river plus large", "tu triple-barrel beaucoup : il peut payer plus large"),
                    R("Triple-barrel rarement", "ses trois barils sont de la value : folde tes bluff-catchers faibles",
                      "ajoute des bluffs river")),
            StatDef("delayed_cbet_turn", "C-bet retardé turn", (35, 60)),
        ],
    ),
    (
        "Postflop — face à l'agresseur préflop",
        [
            StatDef("vs_cbet_flop.fold", "Fold vs c-bet flop", (30, 45),
                    R("Folde trop aux c-bets flop", "c-bet flop large avec un petit sizing", "défends plus contre les c-bets (call/check-raise)"),
                    R("Paye beaucoup les c-bets flop", "c-bet moins en bluff, plus en value mince",
                      "tu défends beaucoup contre les c-bets : ok si tu réalises ton équité")),
            StatDef("vs_cbet_flop.raise", "Raise vs c-bet flop", (8, 18),
                    R("Raise beaucoup les c-bets", "c-bet plus polarisé et défends plus contre ses raises (call/3bet)",
                      "tu raises beaucoup les c-bets : il peut payer ou relancer plus léger"),
                    R("Raise rarement les c-bets", "ses raises sont forts", "ajoute des check-raises")),
            StatDef("vs_cbet_turn.fold", "Fold vs barrel turn", (35, 50),
                    R("Folde trop face au 2e barrel", "double-barrel plus souvent", "défends plus contre le 2e barrel"),
                    R("Paye beaucoup le 2e barrel", "barrel turn surtout en value/équité", "tu paies beaucoup au turn : attention au 3e barrel value")),
            StatDef("vs_cbet_river.fold", "Fold vs barrel river", (35, 55),
                    R("Folde trop face au 3e barrel", "bluff river", "paye plus la river avec tes bluff-catchers"),
                    R("Paye trop le 3e barrel", "value-bet fin, arrête les bluffs", "folde tes bluff-catchers faibles à la river")),
            StatDef("donk_flop", "Donk flop", (0, 10),
                    R("Donk souvent au flop", "ses donks sont souvent moyennes ou des tirages : raise-les",
                      "tu donk souvent : il peut raise tes donks")),
            StatDef("float_flop", "Bet si l'agresseur checke (flop)", (40, 75),
                    R("Attaque systématiquement quand l'agresseur checke", "check-raise ou check-call plus souvent",
                      "tu mises dès qu'il checke : il peut check-raise plus"),
                    R("Mise peu quand l'agresseur checke", "checke tes mains moyennes sans crainte", "mise plus quand il renonce au c-bet")),
            StatDef("probe_turn", "Probe turn", (30, 55),
                    R("Probe beaucoup au turn après un flop checké", "check back plus de mains moyennes pour payer ou induire",
                      "tu probe beaucoup au turn : rentable s'il folde"),
                    R("Probe peu au turn", "ses probes sont fortes", "attaque plus le turn quand il a checké derrière au flop")),
        ],
    ),
    (
        "Postflop — général",
        [
            StatDef("xr_flop", "Check-raise flop", (8, 18),
                    R("Check-raise beaucoup au flop", "c-bet plus polarisé, défends plus contre ses check-raises",
                      "tu check-raise beaucoup : il peut payer ou relancer plus léger"),
                    R("Check-raise peu au flop", "c-bet large, ses check-raises sont forts", "ajoute des check-raises flop")),
            StatDef("xr_turn", "Check-raise turn"),
            StatDef("vs_bet_flop.fold", "Fold vs bet flop"),
            StatDef("vs_bet_turn.fold", "Fold vs bet turn", (35, 50),
                    R("Folde beaucoup face à une mise turn", "mise le turn plus souvent", "défends plus face aux mises turn"),
                    R("Paye beaucoup les mises turn", "mise le turn surtout en value", "tu paies beaucoup au turn")),
            StatDef("vs_bet_river.fold", "Fold vs bet river", (35, 55),
                    R("Folde trop à la river", "bluffe plus la river",
                      "tu folds trop à la river : il bluffe gratuitement, paye plus avec tes bluff-catchers"),
                    R("Paye trop à la river", "value-bet plus fin, réduis les bluffs", "folde tes bluff-catchers faibles à la river")),
            StatDef("vs_raise_flop.fold", "Fold vs raise flop"),
        ],
    ),
    (
        "Abattage",
        [
            StatDef("saw_flop", "Voit le flop"),
            StatDef("wtsd", "WTSD (va à l'abattage)", (27, 38),
                    R("Va beaucoup à l'abattage", "value-bet large et bluffe moins", "tu vas beaucoup à l'abattage : folde plus tes mains faibles"),
                    R("Va peu à l'abattage", "il abandonne vite : bluffe plus", "tu abandonnes trop vite avant l'abattage")),
            StatDef("wsd", "W$SD (gagne à l'abattage)", (46, 56)),
            StatDef("wwsf", "WWSF (gagne après le flop)"),
        ],
    ),
]

STAT_DEFS = {d.key: d for _, defs in SECTIONS for d in defs}


def wilson(r: Ratio, z: float = 1.645) -> tuple[float, float]:
    """Intervalle de confiance à 90 % (Wilson) en pourcentage."""
    if not r.opps:
        return 0.0, 100.0
    n, p = r.opps, r.hits / r.opps
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return 100 * max(0.0, centre - margin), 100 * min(1.0, centre + margin)


@dataclass
class Finding:
    stat: StatDef
    ratio: Ratio
    direction: str  # "haut" / "bas"
    strong: bool  # l'intervalle de confiance entier est hors repère
    reading: Reading

    @property
    def score(self) -> float:
        lo, hi = self.stat.ref
        gap = self.ratio.pct - hi if self.direction == "haut" else lo - self.ratio.pct
        return gap * math.sqrt(self.ratio.opps) + (1000 if self.strong else 0)


def deviation(stat: StatDef, r: Ratio) -> Optional[tuple[str, bool]]:
    if stat.ref is None or r.opps < MIN_SAMPLE:
        return None
    lo, hi = stat.ref
    ci_lo, ci_hi = wilson(r)
    if r.pct > hi:
        return "haut", ci_lo > hi
    if r.pct < lo:
        return "bas", ci_hi < lo
    return None


def findings(ps: PlayerStats) -> list[Finding]:
    """Écarts significatifs aux repères, triés du plus marqué au moins marqué."""
    out = []
    for stat in STAT_DEFS.values():
        r = ps.r(stat.key)
        dev = deviation(stat, r)
        if dev is None:
            continue
        direction, strong = dev
        reading = stat.high if direction == "haut" else stat.low
        if reading:
            out.append(Finding(stat, r, direction, strong, reading))
    return sorted(out, key=lambda f: f.score, reverse=True)


# --- Duel : ce qu'il fait / ce que tu réponds ----------------------------------

@dataclass(frozen=True)
class Duel:
    title: str
    villain_key: str  # fréquence d'agression de l'adversaire
    hero_key: str  # ta fréquence de fold en face
    villain_ref: tuple[float, float]
    hero_ref: tuple[float, float]


DUELS = [
    Duel("Ses 3bets en BB → tes folds vs 3bet au bouton", "bb_vs_open.raise", "sb_vs_3bet.fold", (12, 20), (35, 50)),
    Duel("Tes 3bets en BB → ses folds vs 3bet au bouton", "sb_vs_3bet.fold", "bb_vs_open.raise", (35, 50), (12, 20)),
    Duel("Ses c-bets flop → tes folds vs c-bet", "cbet_flop", "vs_cbet_flop.fold", (50, 75), (30, 45)),
    Duel("Ses barrels turn → tes folds vs barrel turn", "cbet_turn", "vs_cbet_turn.fold", (45, 65), (35, 50)),
    Duel("Ses probes turn → tes folds vs probe", "probe_turn", "vs_probe_turn.fold", (30, 55), (35, 55)),
    Duel("Ses c-bets retardés turn → tes folds", "delayed_cbet_turn", "vs_delayed_cbet_turn.fold", (35, 60), (35, 55)),
    Duel("Ses mises turn → tes folds au turn", "stab_turn+lead_turn", "vs_bet_turn.fold", (35, 55), (35, 50)),
    Duel("Ses mises river → tes folds à la river", "stab_river+lead_river", "vs_bet_river.fold", (35, 55), (35, 55)),
]


def combined(ps: PlayerStats, keys: str) -> Ratio:
    total = Ratio()
    for key in keys.split("+"):
        r = ps.r(key)
        total.hits += r.hits
        total.opps += r.opps
    return total


def duel_verdict(duel: Duel, villain: Ratio, hero: Ratio) -> tuple[str, str]:
    """(niveau, texte). Niveau : 'alerte', 'ok', 'info'."""
    if villain.opps < MIN_SAMPLE or hero.opps < 8:
        return "info", "Échantillon trop faible pour conclure."
    v_hi = villain.pct > duel.villain_ref[1]
    v_lo = villain.pct < duel.villain_ref[0]
    h_hi = hero.pct > duel.hero_ref[1]
    h_lo = hero.pct < duel.hero_ref[0]
    if duel.villain_key == "sb_vs_3bet.fold":  # sens inversé : c'est toi l'agresseur
        if v_hi and not h_hi:
            return "alerte", "Il folde beaucoup vs 3bet et tu ne 3bet pas assez : 3bet plus light."
        if v_lo and h_hi:
            return "alerte", "Il ne folde pas vs 3bet : tes 3bets bluff perdent de l'argent, 3bet surtout en value."
        return "ok", "Ta fréquence de 3bet est cohérente avec sa défense."
    if v_hi and h_hi:
        return "alerte", "Il attaque plus que la norme ET tu folds plus que la norme : défends plus (call/relance)."
    if v_hi and not h_hi:
        return "ok", "Il attaque beaucoup mais tu ne te laisses pas faire."
    if v_lo and h_lo:
        return "alerte", "Il attaque peu (range forte) mais tu paies beaucoup : folde plus tes mains marginales."
    if h_hi:
        return "alerte", "Tu folds plus que la norme ici : il peut t'exploiter en misant plus."
    return "ok", "Pas d'écart notable."


# --- Tells de sizing -----------------------------------------------------------

SIZE_BUCKETS = [(30, "≤ 30 %"), (55, "31–55 %"), (85, "56–85 %"), (120, "86–120 %"), (float("inf"), "> 120 %")]


def size_bucket(pct: float) -> str:
    return next(label for limit, label in SIZE_BUCKETS if pct <= limit)


STRENGTH_ORDER = ["Rien", "Tirage", "Paire moyenne/faible", "Top paire / overpair", "Deux paires +"]


def strength_class(description: str) -> str:
    if description.startswith("Rien"):
        return "Tirage" if "tirage" in description else "Rien"
    if description.startswith(("Top paire", "Overpair")):
        return "Top paire / overpair"
    if description.startswith(("2e paire", "Petite paire")):
        return "Paire moyenne/faible"
    return "Deux paires +"


def sizing_profile(hands, player: str) -> dict:
    """Répartition des sizings postflop et mains montrées pour chaque tranche."""
    counts = {s: Counter() for s in ("flop", "turn", "river")}
    shown = defaultdict(lambda: defaultdict(list))  # street -> bucket -> [(desc, hand)]
    for h in hands:
        known = h.showdown and len(h.hole_cards.get(player, [])) == 2
        for a in h.actions:
            if a.player != player or a.kind != BET or a.street == "preflop" or not a.pot_before:
                continue
            bucket = size_bucket(100.0 * a.amount / a.pot_before)
            counts[a.street][bucket] += 1
            if known:
                n = {"flop": 3, "turn": 4, "river": 5}[a.street]
                shown[a.street][bucket].append((describe_holding(h.hole_cards[player], h.board[:n]), h))
    return {"counts": counts, "shown": shown}


def raise_showdowns(hands, player: str) -> dict:
    out = defaultdict(list)
    for h in hands:
        if not (h.showdown and len(h.hole_cards.get(player, [])) == 2):
            continue
        for a in h.actions:
            if a.player == player and a.kind == RAISE and a.street != "preflop":
                n = {"flop": 3, "turn": 4, "river": 5}[a.street]
                out[a.street].append((describe_holding(h.hole_cards[player], h.board[:n]), h))
    return out


def responses_by_size(hands, bettor: str, responder: str) -> dict:
    """Réponse (fold/call/raise) de `responder` à chaque mise de `bettor`, par street et tranche de sizing.

    Renvoie {street: {tranche: {"counts": Counter, "sizes": [% du pot, ...]}}}.
    """
    table = {s: defaultdict(lambda: {"counts": Counter(), "sizes": []}) for s in ("flop", "turn", "river")}
    for h in hands:
        acts = [a for a in h.actions if a.street != "preflop" and a.kind in (BET, RAISE, CALL, FOLD)]
        for i, a in enumerate(acts):
            if a.player != bettor or a.kind != BET or not a.pot_before:
                continue
            reply = next((b for b in acts[i + 1:] if b.player == responder and b.street == a.street), None)
            if reply is not None:
                pct = 100.0 * a.amount / a.pot_before
                cell = table[a.street][size_bucket(pct)]
                cell["counts"][reply.kind] += 1
                cell["sizes"].append(pct)
    return table


def bluff_break_even(pct: float) -> float:
    """Fréquence de fold (en %) au-delà de laquelle un bluff de cette taille gagne avec n'importe quelles cartes."""
    return 100.0 * pct / (100.0 + pct)
