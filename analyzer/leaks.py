"""Leakfinding : ce qu'un joueur (toi ou un élève) doit travailler en priorité.

1. Ses stats face à la théorie, contre les réguliers et contre les récréatifs : préflop, face à la solution HU
   100 bb (open, limp, 3bet, 4bet, folds) ; postflop, face aux plans de jeu des flops résolus (c-bet, barrels,
   folds face aux mises, relances, probes), par type de pot et par rôle. Un écart est « solide » quand le
   hasard l'explique mal (intervalle de confiance à 90 %), « indicatif » quand il est net sur peu de mains.
2. Ses décisions face au solveur (mains contre les réguliers résolues, theory/review.py) : l'EV perdue par
   situation et ses erreurs les plus chères.
3. Des mains à revoir, choisies pour couvrir les lignes (type de pot, position, street atteinte) contre chaque
   type d'adversaire : les plus gros pots de chaque ligne. Contre les réguliers, elles passent au solveur.
4. Les leaks prioritaires : les écarts et les pertes ci-dessus, classés par confiance puis par poids (fréquence
   de la situation multipliée par l'écart, ou EV perdue), avec la façon de les travailler.

Contre un récréatif, s'écarter de la théorie n'est pas une erreur en soi : ses stats sont montrées à côté, mais
seules celles contre les réguliers comptent pour les leaks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import players
from .insights import wilson
from .models import Hand
from .stats import HandReader, Ratio, analyze
from .theory import coach, exploit, postflop, preflop, review, studyspots

SCOPES = (("all", "Toutes ses mains"), ("reg", "Contre réguliers"), ("rec", "Contre récréatifs"))
PREFLOP = (  # (stat, nœud de la solution, action, libellé, sens de l'action)
    ("sb_first.raise", "sb_open", "raise", "Bouton : open", "aggr"),
    ("sb_first.call", "sb_open", "call", "Bouton : limp", "limp"),
    ("bb_vs_open.fold", "bb_vs_open", "fold", "BB : fold face à l'open", "fold"),
    ("bb_vs_open.raise", "bb_vs_open", "raise", "BB : 3bet", "aggr"),
    ("sb_vs_3bet.fold", "sb_vs_3bet", "fold", "Bouton : fold face au 3bet", "fold"),
    ("sb_vs_3bet.raise", "sb_vs_3bet", "raise", "Bouton : 4bet", "aggr"),
    ("bb_vs_4bet.fold", "bb_vs_4bet", "fold", "BB : fold face au 4bet", "fold"),
)
SOLID_N = 20       # occasions minimum pour un écart « solide »
SOLID_MARGIN = 0.04
LOOSE_N = 10       # occasions minimum pour un écart « indicatif »
LOOSE_GAP = 0.08
MISTAKE_COST = 0.5  # bb par décision jouée autrement que la théorie : sert seulement à classer les leaks
TOP = 5


@dataclass
class Stat:
    key: str
    section: str          # Préflop, SRP, pot 3bet, pot 4bet
    label: str
    kind: str             # aggr, fold, limp, bet, raise : pour le conseil
    ratios: dict          # portée (all, reg, rec) -> Ratio
    reference: Optional[float]   # fréquence du solveur (0 à 1)
    note: str = ""
    per100: float = 0.0   # occasions pour 100 mains (toutes ses mains)
    fragile: bool = False  # repère du solveur sur trop peu de flops : l'écart reste « indicatif »

    def verdict(self, scope: str = "reg") -> Optional[tuple[str, str]]:
        """(« plus » | « moins », « solide » | « indicatif ») face au solveur, ou None."""
        r = self.ratios.get(scope)
        if self.reference is None or r is None or r.opps < LOOSE_N:
            return None
        lo, hi = (x / 100 for x in wilson(r))
        p = r.hits / r.opps
        if r.opps >= SOLID_N and not self.fragile and \
                (lo > self.reference + SOLID_MARGIN or hi < self.reference - SOLID_MARGIN):
            return ("plus" if p > self.reference else "moins"), "solide"
        if abs(p - self.reference) >= LOOSE_GAP:
            return ("plus" if p > self.reference else "moins"), "indicatif"
        return None


@dataclass
class Leak:
    title: str
    evidence: str
    advice: str
    source: str           # solveur, préflop, postflop
    confidence: str
    weight: float         # pour classer (≈ bb pour 100 mains, ordre de grandeur seulement)
    link: Optional[str] = None       # où le travailler (entraîneur, page Préflop, plan de jeu)
    link_text: str = ""
    example: Optional[tuple] = None  # (main, décision, EV perdue) : la plus chère de la situation


@dataclass
class Pick:
    """Une main à revoir."""
    hand: Hand
    kind: str             # reg, rec
    line: str             # « SRP · BTN · river »
    pot_bb: float
    net_bb: float
    digest: Optional[dict] = None    # résultat du solveur s'il a été analysé
    spot: Optional[object] = None    # spot à résoudre (réguliers), si la main se résout


@dataclass
class Report:
    hero: str
    hands: int
    scope_hands: dict           # portée -> nombre de mains
    winrate: dict               # portée -> bb/100
    info: dict                  # portée -> {vpip, pfr, wtsd, wsd} (Ratio)
    stats: list
    review: dict                # digests, groups, costly, todo, lost, analyzed
    picks: list
    leaks: list
    rec_notes: list = field(default_factory=list)
    opponents: dict = field(default_factory=dict)   # type -> joueurs


# --- Découpage par type d'adversaire ---------------------------------------------------------------------

def split(hands: list[Hand], hero: str, kinds: dict[str, dict]) -> dict[str, list[Hand]]:
    """Ses mains, toutes puis contre les réguliers et contre les récréatifs."""
    out = {"all": list(hands), "reg": [], "rec": []}
    for h in hands:
        opp = h.opponent_of(hero)
        out["rec" if kinds.get(opp, {}).get("kind") == "rec" else "reg"].append(h)
    return out


def _pct_info(st) -> dict:
    keys = {"vpip": "vpip", "pfr": "pfr", "wtsd": "wtsd", "wsd": "wsd"}
    return {name: st.r(key) if st else Ratio() for name, key in keys.items()}


# --- 1. Stats face à la théorie ------------------------------------------------------------------------

def preflop_stats(stats: dict, hero: str, hands: dict) -> list[Stat]:
    out = []
    total = len(hands["all"]) or 1
    for key, node, action, label, kind in PREFLOP:
        ratios = {scope: (stats[scope].get(hero).r(key) if stats[scope].get(hero) else Ratio()) for scope, _ in SCOPES}
        if not ratios["all"].opps:
            continue
        ref = preflop.gto_value(node, action) / 100
        out.append(Stat(key, "Préflop", label, kind, ratios, ref, per100=100 * ratios["all"].opps / total))
    return out


def postflop_stats(hands: dict, hero: str) -> list[Stat]:
    out = []
    total = len(hands["all"]) or 1
    for family in studyspots.FAMILIES:
        aggressor = exploit.seat_name(coach.aggressor_of(family))
        defender = exploit.seat_name(1 - coach.aggressor_of(family))
        for role, seat in (("agresseur", aggressor), ("defenseur", defender)):
            base = exploit.baselines(family, role)
            counts = {scope: exploit.observed(hands[scope], [hero], family, role)[0] for scope, _ in SCOPES}
            for s in exploit.situations(family, role):
                hits, opps = counts["all"].get(s.key, (0, 0))
                if not opps:
                    continue
                ratios = {scope: Ratio(*counts[scope].get(s.key, (0, 0))) for scope, _ in SCOPES}
                ref, flops = base.get(s.key, (None, 0))
                note = (f"repère du solveur tiré de {flops} flop(s) résolu(s)" if ref is not None and flops < exploit.MIN_FLOPS
                        else "" if ref is not None else "pas encore de repère du solveur (aucun plan de jeu lu)")
                kind = "bet" if s.measure == "bet" else s.measure
                out.append(Stat(f"{family}:{s.key}", studyspots.FAMILIES[family]["name"], f"{seat} : {s.label}", kind,
                                ratios, ref, note, per100=100 * opps / total,
                                fragile=ref is not None and flops < exploit.MIN_FLOPS))
    return out


# --- 2. Face au solveur -----------------------------------------------------------------------------------

def solver_review(hands: list[Hand], hero: str) -> dict:
    digests, todo = review.collect(hands, hero)
    known = [review.loss(d) for _, d in review.decisions_of(digests, "H") if d["ev_loss"] is not None]
    return {"digests": digests, "todo": todo, "groups": review.by_situation(digests, "H"),
            "costly": review.costly(digests, "H", 10), "lost": sum(known), "decisions": len(known),
            "analyzed": len(digests)}


# --- 3. Mains à revoir --------------------------------------------------------------------------------

def line_of(hand: Hand, hero: str) -> Optional[str]:
    """« SRP · BTN · river » : type de pot, position, dernière street jouée (abattage compris)."""
    reader = HandReader(hand)
    family = exploit.hand_family(reader)
    if family is None or len(hand.board) < 3:
        return None
    seat = "BTN" if hero == hand.button else "BB"
    streets = [s for s in ("flop", "turn", "river") if any(a.street == s for a in hand.actions)]
    last = streets[-1] if streets else "flop"
    return f"{studyspots.FAMILIES[family]['name']} · {seat} · {last}" + (" · abattage" if hand.showdown else "")


def interesting(hands: dict, hero: str, digests: list[dict], per_line: int = 2, limit: int = 24) -> dict[str, list[Pick]]:
    """Par type d'adversaire : les plus gros pots de chaque ligne (per_line par ligne), jusqu'à limit mains."""
    solved = {d["hand"]: d for d in digests}
    out = {}
    for scope in ("reg", "rec"):
        lines: dict[str, list[Pick]] = {}
        for h in hands[scope]:
            line = line_of(h, hero)
            if line is None:
                continue
            pot = h.total_pot / h.bb
            lines.setdefault(line, []).append(Pick(h, scope, line, round(pot, 1), round(h.net(hero) / h.bb, 1),
                                                   solved.get(h.hand_id)))
        chosen = []
        for picks in sorted(lines.values(), key=lambda ps: -max(p.pot_bb for p in ps)):
            chosen.extend(sorted(picks, key=lambda p: -p.pot_bb)[:per_line])
        chosen = sorted(chosen, key=lambda p: -p.pot_bb)[:limit]
        if scope == "reg":
            for p in chosen:
                if p.digest is None:
                    try:
                        p.spot = postflop.build_spot(p.hand, hero)
                    except postflop.Unsupported:
                        p.spot = None
        out[scope] = chosen
    return out


# --- 4. Leaks prioritaires ----------------------------------------------------------------------------------

ADVICE = {
    ("aggr", "plus"): "trop large : resserre (onglet Préflop, les mains concernées)",
    ("aggr", "moins"): "trop serré : élargis (onglet Préflop, les mains concernées)",
    ("limp", "plus"): "trop de limps : ouvre ou folde ces mains comme la solution",
    ("limp", "moins"): "pas assez de limps par rapport à la solution",
    ("fold", "plus"): "tu foldes trop : défends plus large",
    ("fold", "moins"): "tu ne foldes pas assez : lâche tes mains les plus faibles",
    ("bet", "plus"): "tu mises trop souvent : checke davantage tes mains moyennes (plan de jeu)",
    ("bet", "moins"): "tu ne mises pas assez : mise plus, avec tes mains fortes et tes bluffs (plan de jeu)",
    ("raise", "plus"): "tu relances trop : garde des mains moyennes en call",
    ("raise", "moins"): "tu ne relances pas assez : relance tes mains fortes et quelques bluffs",
}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _stat_leak(stat: Stat) -> Optional[Leak]:
    v = stat.verdict("reg")
    if not v:
        return None
    direction, conf = v
    r = stat.ratios["reg"]
    p = r.hits / r.opps
    gap = abs(p - stat.reference)
    link = ("preflop", "Voir l'onglet Préflop") if stat.section == "Préflop" else ("plan", "Voir le plan de jeu")
    return Leak(f"{stat.section} · {stat.label}",
                f"{round(100 * p)} % sur {r.opps} occasions contre les réguliers, contre {round(100 * stat.reference)} % "
                "pour le solveur" + (f" ({stat.note})" if stat.note else ""),
                _cap(ADVICE.get((stat.kind, direction), "écart à la théorie à corriger")) + ".",
                "préflop" if stat.section == "Préflop" else "postflop", conf,
                stat.per100 * gap * MISTAKE_COST, link[0], link[1])


def _worst(digests: list[dict], group: dict) -> Optional[tuple]:
    rows = [(review.loss(d) or 0.0, g["hand"], d["d"]) for g, d in review.decisions_of(digests, "H")
            if g["family"] == group["family"] and d["key"] == group["key"]]
    if not rows:
        return None
    lost, hand, index = max(rows)
    return hand, index, lost


def _solver_leak(group: dict, analyzed: int, digests: Optional[list[dict]] = None) -> Optional[Leak]:
    if group["known"] < 2 or group["lost"] < 0.5 or group["errors"] < 1:
        return None
    devs = review.deviations(group, min_n=5)
    conf = "solide" if group["errors"] >= 3 and group["lost"] >= 2 else "indicatif"
    evidence = (f"{group['lost']:.1f} bb perdus en {group['known']} décisions ({group['errors']} erreur(s) de plus de "
                f"{review.ERROR:.2f} bb), sur {analyzed} mains analysées").replace(".", ",")
    advice = " ".join(review.exploit(group, d, villain=False) for d in devs) or \
        "Revois ces décisions dans l'explorateur : l'EV de chaque action y est donnée."
    return Leak(f"{studyspots.FAMILIES[group['family']]['name']} · {group['label']}", evidence, advice, "solveur", conf,
                100 * group["lost"] / max(analyzed, 1), f"drill:{group['family']}:{group['key']}", "S'entraîner",
                _worst(digests or [], group))


def rank(stats: list[Stat], groups: list[dict], analyzed: int, top: int = TOP,
         digests: Optional[list[dict]] = None) -> list[Leak]:
    leaks = [x for x in (_stat_leak(s) for s in stats) if x]
    leaks += [x for x in (_solver_leak(g, analyzed, digests) for g in groups) if x]
    leaks.sort(key=lambda x: (x.confidence != "solide", -x.weight))
    return leaks[:top]


def rec_notes(stats: list[Stat]) -> list[str]:
    """Contre les récréatifs : où il joue comme contre les réguliers alors que l'exploitation paie."""
    notes = []
    by = {s.key: s for s in stats}
    for key, more_is_better, text in (
            ("sb_first.raise", True, "Contre les récréatifs, ouvre plus large au bouton (ils paient trop et jouent mal "
                                     "postflop)"),
            ("bb_vs_open.fold", False, "Contre les récréatifs, défends plus ta BB"),
            ("srp:fold_flop", False, "Contre les récréatifs, folde moins face à la c-bet")):
        s = by.get(key)
        if not s or s.ratios["rec"].opps < LOOSE_N or s.ratios["reg"].opps < LOOSE_N:
            continue
        rec, reg = (s.ratios[x].hits / s.ratios[x].opps for x in ("rec", "reg"))
        if abs(rec - reg) < 0.05:
            notes.append(f"{text} : {round(100 * rec)} % contre eux, {round(100 * reg)} % contre les réguliers, tu ne "
                         "t'adaptes pas.")
    return notes


def build(hands: list[Hand], hero: str, kinds: Optional[dict] = None) -> Report:
    """Le rapport complet d'un joueur (ses mains, ses adversaires classés réguliers ou récréatifs)."""
    if kinds is None:
        names = sorted({h.opponent_of(hero) for h in hands if h.opponent_of(hero)})
        kinds = players.classify(names, analyze(hands))
    parts = split(hands, hero, kinds)
    stats = {scope: analyze(parts[scope]) for scope, _ in SCOPES}
    winrate = {scope: stats[scope][hero].bb_per_100 if hero in stats[scope] else None for scope, _ in SCOPES}
    info = {scope: _pct_info(stats[scope].get(hero)) for scope, _ in SCOPES}
    rows = preflop_stats(stats, hero, parts) + postflop_stats(parts, hero)
    solver = solver_review(parts["reg"], hero)
    picks = interesting(parts, hero, solver["digests"])
    opponents: dict = {"reg": [], "rec": []}
    for name, info_k in kinds.items():
        opponents["rec" if info_k.get("kind") == "rec" else "reg"].append(name)
    return Report(hero, len(hands), {s: len(parts[s]) for s, _ in SCOPES}, winrate, info, rows, solver, picks,
                  rank(rows, solver["groups"], solver["analyzed"], digests=solver["digests"]), rec_notes(rows),
                  opponents)


def selection_spots(report: Report) -> list:
    """Les mains choisies contre les réguliers, encore à passer au solveur."""
    return [p.spot for p in report.picks.get("reg", []) if p.digest is None and p.spot is not None]


def summary(report: Report) -> dict:
    """Le rapport en données simples (pour le coach et l'export)."""
    pct = lambda r: None if not r or not r.opps else round(100 * r.hits / r.opps)  # noqa: E731
    return {
        "joueur": report.hero, "mains": report.hands, "mains_par_type": report.scope_hands,
        "bb_100": {k: None if v is None else round(v, 1) for k, v in report.winrate.items()},
        "leaks": [{"titre": x.title, "preuve": x.evidence, "conseil": x.advice, "source": x.source,
                   "confiance": x.confidence, "main_la_plus_chere": x.example[0] if x.example else None}
                  for x in report.leaks],
        "stats": [{"section": s.section, "stat": s.label, "solveur_pct": None if s.reference is None else round(100 * s.reference),
                   **{f"{scope}_pct": pct(s.ratios[scope]) for scope, _ in SCOPES},
                   **{f"{scope}_n": s.ratios[scope].opps for scope, _ in SCOPES},
                   "ecart_reguliers": "/".join(s.verdict("reg")) if s.verdict("reg") else None} for s in report.stats],
        "solveur": {"mains_analysees": report.review["analyzed"], "a_analyser": len(report.review["todo"]),
                    "ev_perdue_bb": round(report.review["lost"], 1),
                    "situations": [{"situation": g["label"], "pot": studyspots.FAMILIES[g["family"]]["name"],
                                    "decisions": g["known"], "erreurs": g["errors"], "ev_perdue_bb": round(g["lost"], 2)}
                                   for g in report.review["groups"][:12] if g["known"]]},
        "mains_a_revoir": {scope: [{"main": p.hand.hand_id, "ligne": p.line, "pot_bb": p.pot_bb, "resultat_bb": p.net_bb,
                                    "analysee": p.digest is not None} for p in picks]
                           for scope, picks in report.picks.items()},
        "recreatifs": report.rec_notes,
    }
