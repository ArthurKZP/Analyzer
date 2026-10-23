"""Leakfinding : ce qu'un joueur (toi ou un élève) doit travailler en priorité.

1. Ses stats face à la théorie, contre les réguliers et contre les récréatifs : préflop, face à la solution HU
   100 bb (open, limp, 3bet, 4bet, folds) ; postflop, face aux plans de jeu des flops résolus (c-bet, barrels,
   folds face aux mises, relances, probes), par type de pot et par rôle. Un écart est « solide » quand le
   hasard l'explique mal (intervalle de confiance à 90 %), « indicatif » quand il est net sur peu de mains.
2. Ses décisions face au solveur (mains contre les réguliers résolues, theory/review.py) : l'EV perdue par
   situation et ses erreurs les plus chères.
3. Des mains à revoir, choisies pour couvrir les lignes (type de pot, position, street atteinte) contre chaque
   type d'adversaire : les plus gros pots de chaque ligne. Contre les réguliers, elles passent au solveur.
4. Les leaks prioritaires : les écarts et les pertes ci-dessus, les pertes confirmées d'abord (mesurées en jeu, face
   au fold ou au solveur, ou confirmées par les réponses de ses adversaires), puis par confiance et par poids (ce
   qu'elles coûtent en bb/100 ; à défaut, la fréquence de la situation multipliée par l'écart et par ce que la
   décision met en jeu, le pot grossissant à chaque street et dans les pots 3bet ou 4bet), avec la façon de les
   travailler.
5. Chaque écart à la théorie est vérifié en jeu (analyzer/leakcheck.py) : ce qu'il rapporte ou coûte sur ses mains,
   la réponse de ses adversaires, le plan de jeu suggéré. Un écart qui exploite ses adversaires (c-bet range quand
   ils se couchent trop, open plus large quand ils 3bet peu…) ou qui suit le plan de jeu sort des leaks : il est
   montré à part, avec ce qu'il rapporte. Une perte face au solveur qui vient d'un tel écart est relativisée. Là où
   il joue comme la théorie, leur fold face à ses mises et relances montre les exploitations possibles (ils se
   couchent trop face à ses opens : ouvrir plus large rapporterait).
6. Les leaks sont rangés par catégorie (préflop, après le flop à l'initiative, en défense) ; chacun a ses exemples :
   ses décisions où se joue l'écart, les plus parlantes d'abord (analyzer/leak_examples.py).

Contre un récréatif, s'écarter de la théorie n'est pas une erreur en soi : ses stats sont montrées à côté, mais
seules celles contre les réguliers comptent pour les leaks.

Ce module fait le rapport du heads-up ; celui des tables à plusieurs (3 à 9 joueurs, ensemble) est dans
analyzer/ring_leaks.py, avec les mêmes briques (Stat, Leak, Pick, Report, rank) et la même présentation.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

from . import handplay, leak_examples, leakcheck, players
from .insights import wilson
from .models import Hand
from .stats import HandReader, Ratio, analyze
from .theory import coach, exploit, postflop, preflop, review, stake_charts, studyspots

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
# Ce qu'une décision met en jeu, relativement à l'open : le pot double à peu près à chaque street, et il est plus gros
# dans un pot 3bet ou 4bet. Une erreur à la river pèse plus que la même à l'open.
STREET_STAKE = {"preflop": 1.0, "flop": 2.0, "turn": 4.0, "river": 8.0}
POT_STAKE = {"srp": 1.0, "3bet": 2.0, "4bet": 3.0}
PREFLOP_STAKE = {"sb_first": 1.0, "bb_vs_open": 1.0, "sb_vs_3bet": 2.0, "bb_vs_4bet": 3.0}
TOP = 8          # leaks à travailler, toutes catégories
REPORT_TOP = 5   # dans les rapports PDF et PowerPoint
HAND_LEAKS = 3  # mains de départ qui perdent plus que le fold, au plus


@dataclass
class Stat:
    key: str
    section: str          # Préflop, SRP, pot 3bet, pot 4bet
    label: str
    kind: str             # aggr, fold, limp, call, bet, raise : pour le conseil
    ratios: dict          # portée (all, reg, rec) -> Ratio
    reference: Optional[float]   # fréquence de la théorie (0 à 1)
    note: str = ""
    per100: float = 0.0   # occasions pour 100 mains (toutes ses mains)
    fragile: bool = False  # repère du solveur sur trop peu de flops : l'écart reste « indicatif »
    band: Optional[tuple[float, float]] = None  # sans théorie : fourchette indicative d'un régulier (0 à 1)
    versus: str = "le solveur"  # d'où vient le repère : « le solveur », « tes charts »
    advice: Optional[dict] = None  # conseil propre à la stat (« plus », « moins » -> texte), sinon ADVICE
    link: Optional[tuple[str, str]] = None  # où la travailler (adresse, texte), sinon selon la section
    stake: float = 1.0    # ce que la décision met en jeu (STREET_STAKE × POT_STAKE), pour classer les écarts
    exact: bool = False   # repère calculé sur les mêmes cartes que les siennes (charts) : le hasard des cartes n'y
    # est pour rien, la tolérance est plus faible
    refs: Optional[dict] = None  # portée -> repère, quand il change d'une portée à l'autre (mêmes cartes) ; reference
    # est celui de la portée comparée à la théorie
    check: Optional[leakcheck.Check] = None  # l'écart vérifié en jeu, contre les réguliers (leakcheck)
    raw: Optional[float] = None  # le repère du solveur à sa taille, quand reference est ramené à la sienne (sizing)
    sizing: Optional[tuple[float, float, int]] = None  # (taille du solveur, la sienne, décisions mesurées), en part du
    # pot (exploit.size_of) : ses mises ou relances, ou la mise affrontée quand il folde

    def ref_for(self, scope: str) -> Optional[float]:
        return self.refs.get(scope, self.reference) if self.refs else self.reference

    def weight(self, scope: str = "reg") -> float:
        """Le poids d'un écart (≈ bb pour 100 mains, ordre de grandeur seulement) : occasions pour 100 mains × écart ×
        ce que la décision met en jeu."""
        return self.per100 * self.gap(scope) * self.stake * MISTAKE_COST

    def verdict(self, scope: str = "reg") -> Optional[tuple[str, str]]:
        """(« plus » | « moins », « solide » | « indicatif ») face à la théorie (ou à la fourchette), ou None."""
        r = self.ratios.get(scope)
        reference = self.ref_for(scope)
        if (reference is None and self.band is None) or r is None or r.opps < LOOSE_N:
            return None
        lo, hi = (x / 100 for x in wilson(r))
        p = r.hits / r.opps
        if reference is None:  # fourchette : écart dès qu'on en sort, « solide » si le hasard ne l'explique pas
            low, high = self.band
            if low <= p <= high:
                return None
            direction = "plus" if p > high else "moins"
            if r.opps >= SOLID_N and (lo > high if direction == "plus" else hi < low):
                return direction, "solide"
            return (direction, "indicatif") if self.gap(scope) >= LOOSE_GAP / 2 else None
        margin, loose = self.tolerances(reference)
        if r.opps >= SOLID_N and not self.fragile and (lo > reference + margin or hi < reference - margin):
            return ("plus" if p > reference else "moins"), "solide"
        if abs(p - reference) >= loose:
            return ("plus" if p > reference else "moins"), "indicatif"
        return None

    def tolerances(self, reference: Optional[float] = None) -> tuple[float, float]:
        """(marge d'un écart « solide », écart d'un « indicatif ») autour du repère : 4 et 8 points, la moitié quand
        le repère est calculé sur les mêmes cartes ; moins près de 0 % ou de 100 %, où quelques points font un gros
        écart (un open à 22 % au lieu de 16 %)."""
        reference = self.reference if reference is None else reference
        scale = 0.5 if self.exact else 1.0
        edge = min(reference, 1 - reference)
        return max(0.01, min(SOLID_MARGIN * scale, 0.2 * edge)), max(0.02, min(LOOSE_GAP * scale, 0.4 * edge))

    def gap(self, scope: str = "reg") -> float:
        """L'écart à la théorie (ou à la fourchette), de 0 à 1."""
        r = self.ratios.get(scope)
        if r is None or not r.opps:
            return 0.0
        p = r.hits / r.opps
        reference = self.ref_for(scope)
        if reference is not None:
            return abs(p - reference)
        if self.band is not None:
            return max(self.band[0] - p, p - self.band[1], 0.0)
        return 0.0

    def ref_text(self) -> str:
        """« 17 % pour tes charts », « un repère de 18 à 23 % », « 19 % pour le solveur à ta taille » (size_note dit
        pourquoi)."""
        if self.reference is not None:
            sized = f" {self.size_text()}" if self.sizing else ""
            return f"{round(100 * self.reference)} % pour {self.versus}{sized}"
        if self.band is not None:
            return f"un repère de {round(100 * self.band[0])} à {round(100 * self.band[1])} %"
        return "pas de repère"

    def size_text(self) -> str:
        """« à ta taille », « face aux mises que tu affrontes » : le repère est ramené à sa taille (sizing) ;
        "" sinon."""
        if not self.sizing or self.raw is None:
            return ""
        return "face aux mises que tu affrontes" if self.kind == "fold" else "à ta taille"

    def size_note(self) -> str:
        """Pourquoi et comment le repère est ramené à sa taille, en une phrase ; "" sinon."""
        if not self.sizing or self.raw is None:
            return ""
        theory, mine, n = self.sizing
        raw, ref = round(100 * self.raw), round(100 * self.reference)
        if self.kind == "fold":
            return (f"Repère ramené aux mises que tu affrontes : {self.versus} folde {raw} % face à une mise de "
                    f"{round(100 * theory)} % du pot ; celles que tu affrontes font {round(100 * mine)} % du pot en "
                    f"moyenne ({n} fois) : face à une mise plus {'grosse' if mine > theory else 'petite'}, il folde "
                    f"{'plus' if mine > theory else 'moins'} ({ref} %), comme le veut la défense minimale.")
        what = "relances" if self.kind == "raise" else "mises"
        return (f"Repère ramené à ta taille : {self.versus} le fait {raw} % du temps à {round(100 * theory)} % du "
                f"pot ; tes {what} font {round(100 * mine)} % du pot en moyenne ({n} fois). Plus gros et moins "
                "souvent, ou plus petit et plus souvent, sont deux stratégies théoriques : à volume de mise égal "
                f"(fréquence × taille, mesuré sur le solveur), {ref} %.")


def sized(stat: Stat, theory: Optional[float], mine: list[float], measure: str) -> None:
    """Ramène le repère du solveur à sa taille (exploit.at_size) quand elle s'en écarte assez, sur assez de décisions :
    une autre stratégie théorique (plus petit et plus souvent, ou plus gros et moins souvent) n'est pas une fuite ; et
    face à des mises plus grosses, le solveur folde plus."""
    size = exploit.comparable(theory, mine, measure)
    if stat.reference is None or size is None:
        return
    stat.raw, stat.sizing = stat.reference, (theory, size, len(mine))
    stat.reference = exploit.at_size(stat.reference, measure, theory, size, stat.key.rsplit(":", 1)[-1])


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
    check: Optional[leakcheck.Check] = None  # écart à la théorie vérifié en jeu (leakcheck)
    confirmed: bool = False          # la perte est mesurée (en jeu, face au fold, au solveur) ou confirmée par leurs
    # réponses : ces leaks passent devant les écarts encore à vérifier
    note: str = ""                   # une réserve (une perte face au solveur qui vient d'un écart qui rapporte…)
    key: str = ""                    # la stat de l'écart (Stat.key), pour relier les leaks entre eux
    direction: str = ""              # « plus », « moins »
    causes: list = field(default_factory=list)   # les écarts qui l'expliquent sans doute (Link) : à corriger d'abord
    focus: frozenset = frozenset()   # les situations après le flop (clés de Stat) où il se joue : ses mains au solveur
    targets: list = field(default_factory=list)  # ses mains pas encore analysées, les plus chères d'abord
    # (main, minutes de résolution)
    effects: list = field(default_factory=list)  # ceux qu'il entraîne sans doute (leur titre)
    freq: Optional[tuple] = None     # (sa fréquence, celle de la théorie ou None, sa fourchette ou None, occasions)
    examples: Optional[leak_examples.Examples] = None  # ses décisions où se joue l'écart (le détail du leak)
    headline: str = ""               # le leak en une phrase, en clair (« Tu mises trop souvent : 2e barrel… »)

    @property
    def summary(self) -> str:
        """Le leak en une phrase (headline), sinon son titre."""
        return self.headline or self.title

    @property
    def verified(self) -> str:
        """La preuve, puis ce qu'en dit la vérification en jeu, l'éventuelle réserve et les écarts liés (rapports PDF et
        PowerPoint)."""
        parts = ([self.evidence] + ([self.check.summary] if self.check else []) + ([self.note] if self.note else [])
                 + [x.text() for x in self.causes])
        return " ".join(t if t.endswith((".", "!", "?")) else t + "." for t in (x.strip() for x in parts) if t)


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
    why: str = ""                    # pourquoi elle ne se résout pas (pas de range…)


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
    table_format: str = "HU"    # HU, ring (les tables à plusieurs)
    scopes: tuple = SCOPES      # portées des stats
    solver_scope: str = "reg"   # la portée comparée à la théorie (leaks, mains passées au solveur)
    context: dict = field(default_factory=dict)  # tables à plusieurs : charts présents, flops 6-max résolus…
    exploits: list = field(default_factory=list)  # les écarts qui rapportent ou suivent le plan de jeu (Leak)
    opportunities: list = field(default_factory=list)  # là où il joue comme la théorie, un écart qui rapporterait (Leak)
    solve_orders: dict = field(default_factory=dict)   # les mains à passer au solveur, par façon de les choisir
    # (SOLVE_ORDERS -> [(main, minutes)])


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

def street_stake(key: str) -> float:
    """Le poids de la street d'une stat (« vs_cbet_turn » : la turn)."""
    street = next((s for s in ("river", "turn", "flop") if s in key), "preflop")
    return STREET_STAKE[street]


def preflop_stats(stats: dict, hero: str, hands: dict) -> list[Stat]:
    """Ses stats préflop face à la solution heads-up : celle de chaque niveau de limites (theory/stake_charts.py),
    pondérée par ses mains de chaque niveau."""
    out = []
    total = len(hands["all"]) or 1
    weights = stake_charts.mix(hands["all"])
    for key, node, action, label, kind in PREFLOP:
        ratios = {scope: (stats[scope].get(hero).r(key) if stats[scope].get(hero) else Ratio()) for scope, _ in SCOPES}
        if not ratios["all"].opps:
            continue
        ref = stake_charts.hu_value(node, action, weights) / 100
        out.append(Stat(key, "Préflop", label, kind, ratios, ref, per100=100 * ratios["all"].opps / total,
                        stake=PREFLOP_STAKE.get(key.split(".")[0], 1.0)))
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
            theory = exploit.size_baselines(family, role)
            mine = exploit.observed_sizes(hands["all"], [hero], family, role)
            for s in exploit.situations(family, role):
                hits, opps = counts["all"].get(s.key, (0, 0))
                if not opps:
                    continue
                ratios = {scope: Ratio(*counts[scope].get(s.key, (0, 0))) for scope, _ in SCOPES}
                ref, flops = base.get(s.key, (None, 0))
                note = (f"repère du solveur tiré de {flops} flop(s) résolu(s)" if ref is not None and flops < exploit.MIN_FLOPS
                        else "" if ref is not None else "pas encore de repère du solveur (aucun plan de jeu lu)")
                kind = "bet" if s.measure == "bet" else s.measure
                stat = Stat(f"{family}:{s.key}", studyspots.FAMILIES[family]["name"], f"{seat} : {s.label}", kind,
                            ratios, ref, note, per100=100 * opps / total,
                            fragile=ref is not None and flops < exploit.MIN_FLOPS,
                            stake=street_stake(s.stats[0]) * POT_STAKE[family],
                            link=(f"plan#famille={family}", "Voir le plan de jeu"))
                sized(stat, theory.get(s.key, (None, 0))[0], mine.get(s.key, []), s.measure)
                out.append(stat)
    return out


def stats_for(hands: list[Hand], hero: str, kinds: dict) -> list[Stat]:
    """Ses stats face à la théorie sur ces mains, dans chaque portée (comme build, sans le solveur ni la vérification
    en jeu) : pour les suivre période par période (analyzer/tracking.py)."""
    parts = split(hands, hero, kinds)
    stats = {scope: analyze(parts[scope]) for scope, _ in SCOPES}
    return preflop_stats(stats, hero, parts) + postflop_stats(parts, hero)


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
    reader = HandReader.of(hand)
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


# --- Le leak en une phrase, en clair -------------------------------------------------------------------------------

VERBS = {  # (sens de l'action, sens de l'écart) -> ce qu'il fait
    ("bet", "plus"): "Tu mises trop souvent", ("bet", "moins"): "Tu ne mises pas assez",
    ("fold", "plus"): "Tu te couches trop souvent", ("fold", "moins"): "Tu ne te couches pas assez",
    ("raise", "plus"): "Tu relances trop souvent", ("raise", "moins"): "Tu ne relances pas assez",
    ("call", "plus"): "Tu paies trop souvent", ("call", "moins"): "Tu ne paies pas assez",
    ("aggr", "plus"): "Range trop large", ("aggr", "moins"): "Range trop serrée",
    ("limp", "plus"): "Trop de limps", ("limp", "moins"): "Pas assez de limps",
}
SOLVER_VERBS = {  # catégorie d'actions du solveur (review.CATEGORIES), jouée plus ou moins que lui
    ("aggressive", True): "Tu mises ou relances trop souvent", ("aggressive", False): "Tu ne mises pas assez",
    ("passive", True): "Tu checkes ou paies trop souvent", ("passive", False): "Tu ne checkes ou ne paies pas assez",
    ("fold", True): "Tu te couches trop souvent", ("fold", False): "Tu ne te couches pas assez",
}
ERROR_VERBS = {  # (ce qu'il a joué, ce que voulait le solveur) dans ses erreurs, les catégories de review.CATEGORIES
    ("aggressive", "passive"): "Tu mises ou relances là où le solveur checke ou paie",
    ("aggressive", "fold"): "Tu relances là où le solveur se couche",
    ("passive", "aggressive"): "Tu checkes ou paies là où le solveur mise ou relance",
    ("passive", "fold"): "Tu paies là où le solveur se couche",
    ("fold", "passive"): "Tu te couches là où le solveur paie",
    ("fold", "aggressive"): "Tu te couches là où le solveur relance",
    ("aggressive", "aggressive"): "Tes mises ne sont pas à la bonne taille",
}
PLACES = {"Bouton": "au bouton", "BTN": "au bouton", "SB": "en SB", "BB": "en BB", "CO": "au CO", "HJ": "au HJ",
          "LJ": "au LJ", "UTG": "en UTG", "UTG+1": "en UTG+1", "UTG+2": "en UTG+2"}
LEADS = {"fold": ("fold ",), "raise": ("relance ", "sur-relance "), "call": ("call ",)}  # le verbe déjà dit


def lower_first(text: str) -> str:
    """« C-bet » -> « c-bet », « Pot 3bet » -> « pot 3bet » ; « SRP », « BTN » restent."""
    return text if text[:2].isupper() and text[:2].isalpha() else text[:1].lower() + text[1:]


def headline(title: str, kind: str, direction: str) -> str:
    """Un écart à la théorie en une phrase : ce qu'il fait, puis où (« Tu mises trop souvent : 2e barrel (turn), au
    bouton, en SRP » ; « Range trop large : open, au bouton »). title : « section · position : situation » (la
    section : le type de pot, ou « Préflop » et sa situation aux tables à plusieurs)."""
    verb = VERBS.get((kind, direction))
    if verb is None:
        return ""
    section, _, label = title.rpartition(" · ")
    who, _, what = label.rpartition(" : ")
    what = lower_first(what)
    place = PLACES.get(who, "")
    # le type de pot ; avant le flop, la situation quand la section la dit (« Préflop · face à l'open »)
    pot = section.partition(" · ")[2] if section.startswith("Préflop") else f"en {lower_first(section)}"
    lead = next((w for w in LEADS.get(kind, ()) if what.startswith(w)), None)
    if lead:  # « fold face à l'open » : « Tu te couches trop souvent face à l'open »
        text = f"{verb} {what[len(lead):]}"
    elif kind == "limp":
        text = verb
    else:
        text = f"{verb} : {what}"
    where = ", ".join(x for x in (place, pot) if x)
    return f"{text}, {where}" if where else text


def error_kind(digests: list[dict], group: dict) -> Optional[tuple[str, str]]:
    """Ses erreurs d'une situation face au solveur, en gros : (ce qu'il joue, ce que joue le solveur) qui lui coûte
    le plus d'EV, en catégories d'actions (fold, check ou call, mise ou relance) ; None sans erreur lisible."""
    costs: dict[tuple[str, str], float] = {}
    for g, d in review.decisions_of(digests, "H"):
        if g["family"] != group["family"] or d["key"] != group["key"] or (review.loss(d) or 0.0) < review.ERROR:
            continue
        lost, evs, actions = review.loss(d), d.get("evs") or [], d.get("actions") or []
        known = [k for k, e in enumerate(evs) if e is not None]
        if d.get("chosen") is None or not known or len(evs) != len(actions):
            continue
        best = max(known, key=lambda k: evs[k])
        pair = (review.category(actions[d["chosen"]]["kind"]), review.category(actions[best]["kind"]))
        costs[pair] = costs.get(pair, 0.0) + lost
    return max(costs, key=costs.get) if costs else None


def solver_headline(group: dict, devs: list[dict], pot: str, errors: Optional[tuple[str, str]] = None) -> str:
    """Une situation qui coûte face au solveur, en une phrase : son écart de fréquence le plus net (« Tu mises ou
    relances trop souvent : c-bet, en pot 3bet »), sinon ce qui coûte dans ses erreurs (errors : error_kind), sinon ce
    qu'elle coûte."""
    label = group["label"]
    what = f"face à {lower_first(label[len('Face à : '):])}" if label.startswith("Face à : ") else lower_first(label)
    if devs:
        dev = max(devs, key=lambda d: abs(d["z"]))
        verb = SOLVER_VERBS.get((dev["category"], dev["observed"] > dev["expected"]))
        if verb:
            return f"{verb} : {what}, en {pot}"
    if errors in ERROR_VERBS:
        return f"{ERROR_VERBS[errors]} : {what}, en {pot}"
    return f"Tu y perds de l'EV face au solveur : {what}, en {pot}"


STREET_CODES = {"f": "flop", "t": "turn", "r": "river"}
SITUATION_STREETS = {"cbet": "flop", "barrel": "turn", "delayed": "turn", "barrel3": "river", "stab": "flop"}
STREET_NAMES = {"flop": "Au flop", "turn": "À la turn", "river": "À la river", "": "Après le flop"}


def street_of(leak: Leak) -> str:
    """La street où se joue un leak (« preflop », « flop », « turn », « river ») ; "" quand elle ne se dit pas (face à
    une relance, à toutes les streets)."""
    if leak.source == "préflop":
        return "preflop"
    if leak.key.startswith("solveur:"):
        situation = leak.key.split(":", 2)[2]
        return STREET_CODES.get(situation.split(":")[1][:1], "") if ":" in situation else ""
    text = f"{leak.key} {leak.title}".lower()
    found = next((s for s in ("river", "turn", "flop") if s in text), None)
    return found or SITUATION_STREETS.get(leak.key.rsplit(":", 1)[-1], "")


SCOPE_WORDS = {"reg": " contre les réguliers", "rec": " contre les récréatifs", "all": ""}


KEPT = ("exploit", "plan")  # les verdicts d'un écart qui sort des leaks (leakcheck)


def _checked(stat: Stat, scope: str) -> Optional[leakcheck.Check]:
    """La vérification en jeu de l'écart, quand elle porte sur cette portée (celle comparée à la théorie)."""
    return stat.check if scope == "reg" else None


def _stat_leak(stat: Stat, scope: str = "reg") -> Optional[Leak]:
    """L'écart d'une stat à la théorie, comme leak ; None sans écart net, ou quand il exploite ses adversaires ou suit
    le plan de jeu (vérifié en jeu : voir kept). Une fuite mesurée pèse ce qu'elle coûte (bb/100)."""
    v = stat.verdict(scope)
    if not v:
        return None
    direction, conf = v
    check = _checked(stat, scope)
    if check is not None and check.verdict in KEPT:
        return None
    r = stat.ratios[scope]
    p = r.hits / r.opps
    preflop_stat = stat.section.startswith("Préflop")
    link = stat.link or (("preflop", "Voir l'onglet Préflop") if preflop_stat else ("plan", "Voir le plan de jeu"))
    advice = (stat.advice or {}).get(direction) or ADVICE.get((stat.kind, direction), "écart à la théorie à corriger")
    title = f"{stat.section} · {stat.label}"
    return Leak(title,
                f"{round(100 * p)} % sur {r.opps} occasions{SCOPE_WORDS.get(scope, '')}, contre {stat.ref_text()}"
                + (f" ({stat.note})" if stat.note else ""),
                _cap(advice) + ".", "préflop" if preflop_stat else "postflop", conf, importance(stat, scope),
                link[0], link[1], check=check, confirmed=check is not None and check.verdict == "fuite",
                note=stat.size_note(), key=stat.key, direction=direction,
                focus=frozenset() if preflop_stat else frozenset({stat.key}),
                freq=(p, stat.ref_for(scope), stat.band, r.opps), headline=headline(title, stat.kind, direction))


KEPT_ADVICE = {
    "exploit": "Garde-le contre ces adversaires : il rapporte plus que la théorie. Surveille leurs réponses (les preuves "
               "ci-dessus) : s'ils s'adaptent, reviens vers la théorie.",
    "justifié": "Garde-le contre ces adversaires, sans forcer : leurs réponses le justifient, mais son gain n'est pas "
                "chiffré. S'ils s'adaptent, reviens vers la théorie.",
    "plan": "Garde-le : c'est le plan de jeu suggéré, plus simple à jouer que la stratégie mixte du solveur et proche "
            "de ce qu'elle rapporte.",
}


def _kept_leak(stat: Stat, scope: str = "reg") -> Optional[Leak]:
    """Un écart à la théorie qui exploite ses adversaires ou suit le plan de jeu (vérifié en jeu)."""
    v = stat.verdict(scope)
    check = _checked(stat, scope)
    if not v or check is None or check.verdict not in KEPT:
        return None
    r = stat.ratios[scope]
    preflop_stat = stat.section.startswith("Préflop")
    link = stat.link or (("preflop", "Voir l'onglet Préflop") if preflop_stat else ("plan", "Voir le plan de jeu"))
    gain = check.bb100 if check.bb100 is not None else 0.0
    return Leak(f"{stat.section} · {stat.label}",
                f"{round(100 * r.hits / r.opps)} % sur {r.opps} occasions{SCOPE_WORDS.get(scope, '')}, contre "
                f"{stat.ref_text()}" + (f" ({stat.note})" if stat.note else ""),
                KEPT_ADVICE["justifié" if check.verdict == "exploit" and not check.measured else check.verdict],
                check.verdict, v[1], gain, link[0], link[1], check=check, confirmed=True, note=stat.size_note(),
                key=stat.key, direction=v[0])


OPPORTUNITY_ADVICE = {
    ("bet", "plus"): "Mise plus souvent ici contre eux : ajoute des mains faibles (bluffs, semi-bluffs) ; reviens vers "
                     "la théorie s'ils s'adaptent.",
    ("bet", "moins"): "Mise moins souvent en bluff ici contre eux : ils ne se couchent pas assez ; garde tes mises pour "
                      "la value.",
    ("raise", "plus"): "Relance plus souvent ici contre eux, en bluff : ils se couchent trop face à tes relances.",
    ("raise", "moins"): "Relance moins souvent en bluff ici contre eux : ils ne se couchent pas assez.",
    ("aggr", "plus"): "Relance plus large ici contre eux : ils se couchent trop face à tes relances (ajoute des mains "
                      "faibles) ; reviens vers la théorie s'ils s'adaptent.",
    ("aggr", "moins"): "Relance moins large ici contre eux : ils ne se couchent pas assez face à tes relances.",
}


def _chance_leak(stat: Stat, scope: str = "reg") -> Optional[Leak]:
    """Là où il joue comme la théorie, un écart qui rapporterait (leakcheck.chance)."""
    check = _checked(stat, scope)
    if check is None or check.verdict != "opportunité":
        return None
    r = stat.ratios[scope]
    preflop_stat = stat.section.startswith("Préflop")
    link = stat.link or (("preflop", "Voir l'onglet Préflop") if preflop_stat else ("plan", "Voir le plan de jeu"))
    advice = OPPORTUNITY_ADVICE.get((stat.kind, check.direction), "Écarte-toi de la théorie dans ce sens contre eux.")
    return Leak(f"{stat.section} · {stat.label}",
                f"{round(100 * r.hits / r.opps)} % sur {r.opps} occasions{SCOPE_WORDS.get(scope, '')}, proche de "
                f"{stat.ref_text()}", advice, "opportunité", "solide", check.bb100, link[0], link[1], check=check,
                confirmed=True, note=stat.size_note(), key=stat.key)


def chances(stats: list[Stat], scope: str = "reg") -> list[Leak]:
    """Les exploitations possibles : là où il joue comme la théorie, les écarts qui rapporteraient le plus d'abord."""
    return sorted((x for x in (_chance_leak(s, scope) for s in stats) if x), key=lambda x: -x.weight)


def gap_advice(stat: Stat, direction: str, scope: str) -> str:
    """Ce qu'il faut faire d'un écart : le corriger, ou le garder quand il est justifié en jeu (kept)."""
    check = _checked(stat, scope)
    if check is not None and check.verdict in KEPT:
        return "À garder : " + ("il exploite tes adversaires" if check.verdict == "exploit" else
                                "c'est le plan de jeu suggéré")
    return _cap((stat.advice or {}).get(direction) or ADVICE.get((stat.kind, direction), "écart à corriger"))


def kept(stats: list[Stat], scope: str = "reg") -> list[Leak]:
    """Ses écarts à la théorie qui rapportent (ou suivent le plan de jeu) : ceux qui rapportent le plus d'abord."""
    found = [x for x in (_kept_leak(s, scope) for s in stats) if x]
    found.sort(key=lambda x: (x.check.verdict != "exploit", x.check.bb100 is None, -x.weight))
    return found


def top_gaps(report: "Report", n: int = 8) -> list[tuple[Stat, str, str]]:
    """Les écarts à la théorie les plus importants, contre les réguliers (à une table à plusieurs : toutes ses mains)."""
    return top_stat_gaps(report.stats, report.solver_scope, n)


CHECK_ORDER = {"fuite": 0, "": 1, "plan": 2, "exploit": 3}  # vérifiés en jeu : les fuites d'abord, ce qui rapporte
# en dernier


def importance(stat: Stat, scope: str) -> float:
    """Le poids d'un écart (≈ bb/100) : ce qu'il coûte, mesuré en jeu ; sans mesure nette, Stat.weight, mais jamais plus
    que ce que la mesure incertaine lui permet de coûter (la borne de son intervalle à 95 %)."""
    weight = stat.weight(scope)
    check = _checked(stat, scope)
    if check is None or check.bb100 is None:
        return weight
    return check.cost if check.measured else min(weight, check.max_cost)


def top_stat_gaps(stats: list[Stat], scope: str, n: int = 8) -> list[tuple[Stat, str, str]]:
    """Les écarts de ces stats dans cette portée : les fuites vérifiées en jeu d'abord, puis ceux encore à confirmer,
    ceux qui suivent le plan de jeu et ceux qui exploitent ses adversaires ; les solides avant les indicatifs ; enfin
    du plus lourd au plus léger (importance : ce qu'ils coûtent vraiment, ou fréquence de la situation × écart × ce que
    la décision met en jeu) ; (stat, « plus » | « moins », confiance)."""
    found = [(s, *s.verdict(scope)) for s in stats if s.verdict(scope)]

    def order(item: tuple) -> tuple:
        check = _checked(item[0], scope)
        return CHECK_ORDER.get(check.verdict if check else "", 1), item[2] != "solide", -importance(item[0], scope)

    found.sort(key=order)
    return found[:n]


def _worst(digests: list[dict], group: dict) -> Optional[tuple]:
    rows = [(review.loss(d) or 0.0, g["hand"], d["d"]) for g, d in review.decisions_of(digests, "H")
            if g["family"] == group["family"] and d["key"] == group["key"]]
    if not rows:
        return None
    lost, hand, index = max(rows)
    return hand, index, lost


def family_name(family: str) -> str:
    """Le type de pot d'une situation du solveur : « SRP », « pot 3bet » ; à une table à plusieurs, avec les positions
    (« SRP BB c. CO »)."""
    return studyspots.pot_name(family)


def _exploited(group: dict, devs: list[dict], stats: dict[str, Stat]) -> Optional[Stat]:
    """La stat dont l'écart, vérifié en jeu, exploite ses adversaires (ou suit le plan de jeu) et explique un écart de
    la situation du solveur dans le même sens (il mise plus que le solveur à la c-bet, et la c-bet plus large
    rapporte) ; None sinon."""
    for dev in devs:
        stat = stats.get(leakcheck.solver_stat(group["family"], group["key"], dev["category"]) or "")
        check = stat.check if stat is not None else None
        if check is None or check.verdict not in KEPT:
            continue
        more = dev["observed"] > dev["expected"]
        if ("plus" if more != (dev["category"] == "passive") else "moins") == check.direction:
            return stat
    return None


def _solver_leak(group: dict, analyzed: int, digests: Optional[list[dict]] = None,
                 stats: Optional[dict[str, Stat]] = None) -> Optional[Leak]:
    if group["known"] < 2 or group["lost"] < 0.5 or group["errors"] < 1:
        return None
    devs = review.deviations(group, min_n=5)
    conf = "solide" if group["errors"] >= 3 and group["lost"] >= 2 else "indicatif"
    evidence = (f"{group['lost']:.1f} bb perdus en {group['known']} décisions ({group['errors']} erreur(s) de plus de "
                f"{review.ERROR:.2f} bb), sur {analyzed} mains analysées").replace(".", ",")
    advice = " ".join(review.exploit(group, d, villain=False) for d in devs) or \
        "Revois ces décisions dans l'explorateur : l'EV de chaque action y est donnée."
    exploited = _exploited(group, devs, stats or {})
    note = ""
    if exploited is not None:
        note = (f"À relativiser : le solveur suppose des adversaires qui jouent bien ; contre les tiens, l'écart « "
                f"{exploited.label} » est justifié (vérifié en jeu, voir « Les écarts justifiés ») : une partie de ces "
                "bb n'est pas perdue.")
    focus = frozenset(x for x in (leakcheck.solver_stat(group["family"], group["key"], c)
                                  for c in ("fold", "passive", "aggressive")) if x)
    pot = family_name(group["family"])
    return Leak(f"{pot} · {group['label']}", evidence, advice, "solveur", conf,
                100 * group["lost"] / max(analyzed, 1), f"drill:{group['family']}:{group['key']}", "S'entraîner",
                _worst(digests or [], group), confirmed=exploited is None, note=note,
                key=f"solveur:{group['family']}:{group['key']}", focus=focus,
                headline=solver_headline(group, devs, lower_first(pot), error_kind(digests or [], group)))


def _hand_leak(x: "handplay.Loser", hands: int, scope: str = "reg", fmt: str = "HU", plays: Optional[list] = None,
               hero: str = "") -> Leak:
    """Une main (ou une famille) jouée ainsi perd nettement plus que le fold, contre les réguliers (fmt « ring » : à
    une table à plusieurs, face aux charts) : d'où vient la perte (le type de pot qui coûte le plus), ce qu'en dit la
    théorie, et le coup le plus cher à revoir ; plays : ses mains de départ de cette portée, pour ses exemples."""
    fr = lambda text: text.replace(".", ",")  # noqa: E731  (les nombres à la française ; le point final après)
    evidence = fr(f"{x.n} fois{SCOPE_WORDS.get(scope, '')} : {x.mean:+.2f} bb par main (± {x.half_width:.1f}), contre "
                  f"{x.fold:+.2f} bb pour le fold, soit {100 * x.gap:+.0f} bb/100 par rapport au fold") + "."
    costly = [src for src in x.sources if src.contribution < 0][:2]
    if costly:
        parts = [fr(f"{handplay.POT_SOURCES[src.pot]} ({src.n} fois, {src.mean:+.1f} bb par main : "
                    f"{100 * src.contribution:+.0f} bb/100)") for src in costly]
        evidence += " La perte vient surtout " + ", puis ".join(parts) + "."
        worst = costly[0]
        if worst.solver_n:
            evidence += fr(f" Le solveur y voit {worst.solver_loss:.2f} bb perdus par coup après le flop "
                           f"({worst.solver_n} coups analysés)") + "."
    t = None if x.theory is None else round(100 * x.theory)
    who, s = ("La théorie", "") if fmt == "HU" else ("Tes charts", "nt")  # « la joue », « la jouent »
    where = handplay.POT_WORDS.get(costly[0].pot, "") if costly else ""
    if t is not None and t < 10:
        advice = f"{who} ne la joue{s} presque jamais ainsi ({t} %) : folde-la ici."
    elif costly and costly[0].pot == "fold":
        advice = ("Tu la joues puis l'abandonnes trop souvent face à la relance : défends-la plus, ou ne l'engage pas "
                  "(onglet Mains de départ, « Tes choix face à la théorie »).")
    elif costly and costly[0].pot in handplay.REVIEW_POTS:
        said = f"{who} la joue{s} ainsi ({t} %) : " if t is not None and t >= 50 else (
            f"{who} la mélange{s} ({t} %) : " if t is not None else "")
        advice = f"{said}la perte vient de la suite du coup, {where} ; revois ces coups (Mains de départ, puis le solveur)."
    elif t is not None and t < 50:
        advice = f"{who} la mélange{s} ({t} %) : joue-la moins souvent ainsi."
    else:
        advice = "Joue-la moins souvent ainsi, ou revois la suite de ces coups."
    group = "fold" if x.action == "fold" else "pas" if x.action in ("call", "check") else "agg"
    query = {"fmt": fmt, "kind": scope} if scope in ("reg", "rec") else {"fmt": fmt}
    link = "mains#" + urlencode(dict(query, pos=x.position, sit=x.situation, main=x.name, act=group))
    example = None
    if x.worst and x.worst[0][1] < 0:
        hand_id, ev, _ = x.worst[0]
        example = (hand_id, 0, -ev)
    return Leak(f"Préflop · {x.label}", evidence, advice, "préflop", "solide", -100 * x.gap * x.n / max(hands, 1),
                link, "Voir d'où vient la perte", example, confirmed=True,
                examples=leak_examples.hand_loser(x, plays, hero) if plays else None,
                headline=f"Une main de départ qui perd plus que le fold : {x.label}")


# --- Leaks liés -----------------------------------------------------------------------------------------------------
#
# Un écart en entraîne souvent un autre : une BB qui défend trop large avant le flop arrive au flop avec plus de mains
# faibles, et folde plus face à la c-bet sans que ce fold soit une fuite à part ; payer trop la c-bet amène plus de
# mains faibles à la turn… L'écart qui explique l'autre passe devant, l'autre le rappelle (« Lié à »).

WHY = {  # (situation de la cause, sens) -> ce que l'écart change pour la suite du coup
    "defend_wide": "ta range est plus faible à la street suivante : folder plus en découle",
    "defend_tight": "ta range est plus forte à la street suivante : folder moins en découle",
    "range_weak": "ta range est plus faible ensuite (plus de mains marginales) : miser moins souvent et folder plus "
                  "face aux relances en découlent",
    "range_strong": "ta range est plus forte ensuite : miser plus souvent et folder moins face aux relances en "
                    "découlent",
    "checks_more": "les mains que tu ne mises pas renforcent ta range de check : miser plus ensuite en découle",
}
# (situation de la cause, sens, situation de la conséquence, sens, pourquoi) : d'une street à la suivante, dans un même
# type de pot et un même rôle (la clé de la stat, sa situation remplacée)
CHAINS = (
    ("fold_flop", "moins", "fold_turn", "plus", "defend_wide"),
    ("fold_flop", "plus", "fold_turn", "moins", "defend_tight"),
    ("fold_turn", "moins", "fold_river", "plus", "defend_wide"),
    ("fold_turn", "plus", "fold_river", "moins", "defend_tight"),
    ("cbet", "plus", "barrel", "moins", "range_weak"),
    ("cbet", "plus", "fold_raise", "plus", "range_weak"),
    ("cbet", "moins", "barrel", "plus", "range_strong"),
    ("cbet", "moins", "fold_raise", "moins", "range_strong"),
    ("cbet", "moins", "delayed", "plus", "checks_more"),
    ("barrel", "plus", "barrel3", "moins", "range_weak"),
    ("barrel", "moins", "barrel3", "plus", "range_strong"),
)
# Du préflop au flop : (stat de la cause, sens, stat de la conséquence, sens, pourquoi) ; les tables à plusieurs
# s'écrivent avec des jokers (fnmatch : toutes les positions, toutes les structures de ce type de pot).
PREFLOP_LINKS = (
    ("bb_vs_open.fold", "moins", "srp:fold_flop", "plus", "defend_wide"),
    ("bb_vs_open.fold", "plus", "srp:fold_flop", "moins", "defend_tight"),
    ("sb_first.raise", "plus", "srp:cbet", "moins", "range_weak"),
    ("sb_first.raise", "plus", "srp:fold_raise", "plus", "range_weak"),
    ("sb_first.raise", "moins", "srp:cbet", "plus", "range_strong"),
    ("sb_first.raise", "moins", "srp:fold_raise", "moins", "range_strong"),
    ("bb_vs_open.raise", "plus", "3bet:cbet", "moins", "range_weak"),
    ("bb_vs_open.raise", "plus", "3bet:fold_raise", "plus", "range_weak"),
    ("bb_vs_open.raise", "moins", "3bet:cbet", "plus", "range_strong"),
    ("sb_vs_3bet.fold", "moins", "3bet:fold_flop", "plus", "defend_wide"),
    ("sb_vs_3bet.fold", "plus", "3bet:fold_flop", "moins", "defend_tight"),
    ("sb_vs_3bet.raise", "plus", "4bet:cbet", "moins", "range_weak"),
    ("sb_vs_3bet.raise", "plus", "4bet:fold_raise", "plus", "range_weak"),
    ("bb_vs_4bet.fold", "moins", "4bet:fold_flop", "plus", "defend_wide"),
    ("bb_vs_4bet.fold", "plus", "4bet:fold_flop", "moins", "defend_tight"),
    ("pre:vs_open:*:fold", "moins", "srp_*:defenseur:fold_flop", "plus", "defend_wide"),
    ("pre:vs_open:*:call", "plus", "srp_*:defenseur:fold_flop", "plus", "defend_wide"),
    ("pre:vs_open:*:fold", "plus", "srp_*:defenseur:fold_flop", "moins", "defend_tight"),
    ("pre:open:*:open", "plus", "srp_*:agresseur:cbet", "moins", "range_weak"),
    ("pre:open:*:open", "plus", "srp_*:agresseur:fold_raise", "plus", "range_weak"),
    ("pre:open:*:open", "moins", "srp_*:agresseur:cbet", "plus", "range_strong"),
    ("pre:open:*:open", "moins", "srp_*:agresseur:fold_raise", "moins", "range_strong"),
    ("pre:vs_open:*:3bet", "plus", "3bet_*:agresseur:cbet", "moins", "range_weak"),
    ("pre:vs_open:*:3bet", "plus", "3bet_*:agresseur:fold_raise", "plus", "range_weak"),
    ("pre:vs_open:*:3bet", "moins", "3bet_*:agresseur:cbet", "plus", "range_strong"),
    ("pre:vs_3bet:*:fold", "moins", "3bet_*:defenseur:fold_flop", "plus", "defend_wide"),
    ("pre:vs_3bet:*:fold", "plus", "3bet_*:defenseur:fold_flop", "moins", "defend_tight"),
    ("pre:vs_3bet:*:4bet", "plus", "4bet_*:agresseur:cbet", "moins", "range_weak"),
)
DIRECTION_WORDS = {"plus": "plus que la théorie", "moins": "moins que la théorie"}


@dataclass(frozen=True)
class Link:
    """Un écart (cause) qui en explique sans doute un autre."""
    cause: Stat
    direction: str      # le sens de l'écart de la cause
    why: str            # WHY

    def text(self) -> str:
        kept = self.cause.check is not None and self.cause.check.verdict in KEPT  # la cause est un écart justifié
        return (f"Lié à « {self.short()} » ({DIRECTION_WORDS[self.direction]}) : {WHY[self.why]} ; "
                + ("ce n'est sans doute pas une fuite à part." if kept else "corrige d'abord celui-là."))

    def short(self) -> str:
        return f"{self.cause.section} · {self.cause.label}"


def links(stats: list[Stat], scope: str = "reg") -> dict[str, list[Link]]:
    """Les écarts de ces stats qui en expliquent sans doute d'autres : stat de la conséquence -> ses causes (Link)."""
    seen = {s.key: (s, v[0]) for s in stats for v in [s.verdict(scope)] if v}
    out: dict[str, list[Link]] = {}

    def add(effect: str, direction: str, cause: Stat, cause_direction: str, why: str) -> None:
        found = seen.get(effect)
        if found and found[1] == direction and all(x.cause.key != cause.key for x in out.get(effect, [])):
            out.setdefault(effect, []).append(Link(cause, cause_direction, why))

    for key, (stat, direction) in seen.items():
        base, _, situation = key.rpartition(":")
        for cause, cause_dir, effect, effect_dir, why in CHAINS:
            if base and situation == cause and direction == cause_dir:
                add(f"{base}:{effect}", effect_dir, stat, direction, why)
        for cause, cause_dir, effect, effect_dir, why in PREFLOP_LINKS:
            if direction == cause_dir and fnmatch.fnmatchcase(key, cause):
                for other in seen:
                    if fnmatch.fnmatchcase(other, effect):
                        add(other, effect_dir, stat, direction, why)
    return out


def rank(stats: list[Stat], groups: list[dict], analyzed: int, top: int = TOP,
         digests: Optional[list[dict]] = None, extra: Optional[list[Leak]] = None, scope: str = "reg") -> list[Leak]:
    """Les leaks à travailler, les grosses erreurs d'abord : les pertes confirmées (mesurées en jeu, face au fold ou au
    solveur, ou confirmées par leurs réponses) avant les écarts encore à vérifier ; les solides avant les indicatifs ;
    puis du plus lourd au plus léger. Les écarts qui exploitent ses adversaires n'y sont pas (kept)."""
    by_key = {s.key: s for s in stats}
    leaks = [x for x in (_stat_leak(s, scope) for s in stats) if x] + list(extra or [])
    leaks += [x for x in (_solver_leak(g, analyzed, digests, by_key) for g in groups) if x]
    found = links(stats, scope)
    for x in leaks:
        if x.key:
            x.causes = found.get(x.key, [])
            x.effects = [f"{by_key[effect].section} · {by_key[effect].label}" for effect, causes in found.items()
                         if any(link.cause.key == x.key for link in causes)]
    # un écart qui en explique sans doute un autre passe devant : corrigé, il corrige l'autre
    leaks.sort(key=lambda x: (not x.confirmed, bool(x.causes), x.confidence != "solide", -x.weight))
    return leaks[:top]


# --- Catégories et exemples ---------------------------------------------------------------------------------------
#
# Les leaks se lisent d'abord par catégorie (où ils se jouent), puis un par un, avec leurs exemples.

CATEGORIES = (
    ("preflop", "Préflop", "ses ranges : ouverture, défense, 3bet, 4bet, et les mains de départ qui perdent"),
    ("initiative", "Après le flop, à l'initiative", "c-bet, barrels, face aux relances"),
    ("defense", "Après le flop, en défense", "face aux mises : fold, relance ; mises quand l'agresseur checke"),
    ("postflop", "Après le flop", "les autres situations jugées par le solveur"),
)
AGGRESSOR_KEYS = frozenset(s.key for s in exploit.situations("srp", "agresseur"))


def _role_of(key: str) -> Optional[str]:
    """Le rôle d'une stat après le flop (« srp:cbet », « srp_ip:defenseur:fold_flop ») : « agresseur » ou
    « defenseur »."""
    parts = key.split(":")
    for role in ("agresseur", "defenseur"):
        if role in parts:
            return role
    return ("agresseur" if parts[-1] in AGGRESSOR_KEYS else "defenseur") if len(parts) == 2 else None


def category(leak: Leak) -> str:
    """La catégorie d'un leak (CATEGORIES) : le préflop, après le flop à l'initiative ou en défense."""
    if leak.source == "préflop":
        return "preflop"
    role = None
    if leak.source == "solveur" and leak.key.startswith("solveur:"):
        _, family, key = leak.key.split(":", 2)
        role = leakcheck.solver_role(family, key)
    else:
        role = _role_of(leak.key)
    return {"agresseur": "initiative", "defenseur": "defense"}.get(role or "", "postflop")


def themes(items: list[tuple[int, Leak]]) -> list[tuple[str, list[tuple[int, Leak]]]]:
    """Les leaks d'une catégorie (avec leur rang) regroupés par street, dans l'ordre de leur leak le plus important :
    [(street, leaks)] (street_of)."""
    groups: dict[str, list[tuple[int, Leak]]] = {}
    for k, x in items:
        groups.setdefault(street_of(x), []).append((k, x))
    return sorted(groups.items(), key=lambda kv: kv[1][0][0])


def theme_name(category_key: str, street: str) -> str:
    """« Préflop », « À la turn, à l'initiative », « Au flop, en défense »."""
    if street == "preflop" or category_key == "preflop":
        return "Préflop"
    role = {"initiative": "à l'initiative", "defense": "en défense"}.get(category_key, "")
    return STREET_NAMES.get(street, "Après le flop") + (f", {role}" if role else "")


def overview(found: list[Leak], n: int = 3) -> list[tuple[str, list[tuple[int, Leak]]]]:
    """L'essentiel des leaks : leurs thèmes (catégorie et street) les plus importants d'abord, au plus n :
    [(nom du thème, ses leaks avec leur rang)]."""
    out = []
    for (key, _, _), items in by_category(found):
        out += [(theme_name(key, street), group) for street, group in themes(items)]
    return sorted(out, key=lambda t: t[1][0][0])[:n]


def by_category(found: list[Leak]) -> list[tuple[tuple[str, str, str], list[tuple[int, Leak]]]]:
    """Les leaks par catégorie, avec leur rang (1 : le plus important) ; les catégories dans l'ordre de leur leak le
    plus important."""
    groups: dict[str, list[tuple[int, Leak]]] = {}
    for k, x in enumerate(found, start=1):
        groups.setdefault(category(x), []).append((k, x))
    names = {c[0]: c for c in CATEGORIES}
    return [(names[c], items) for c, items in sorted(groups.items(), key=lambda kv: kv[1][0][0])]


def attach_examples(found: list[Leak], stats: list[Stat], hero: str, plays: list, points: dict[str, list],
                    digests: list[dict]) -> None:
    """Les exemples de chaque leak (leak_examples) : plays, ses mains de départ de la portée comparée à la théorie ;
    points, ses décisions après le flop par stat (leakcheck.hu_points, ring_points) ; digests, ses mains au solveur."""
    by_key = {s.key: s for s in stats}
    for x in found:
        if x.examples is not None or not x.key:
            continue
        if x.source == "solveur" and x.key.startswith("solveur:"):
            _, family, key = x.key.split(":", 2)
            x.examples = leak_examples.solver(family, key, digests)
            continue
        stat = by_key.get(x.key)
        if stat is None or not x.direction:
            continue
        if stat.section.startswith("Préflop"):
            x.examples = leak_examples.preflop(stat.key, x.direction, plays, hero)
        elif stat.kind in ("bet", "fold", "raise"):
            x.examples = leak_examples.postflop(stat.kind, x.direction, points.get(stat.key, []), hero)


# --- 5. Les mains à passer au solveur ----------------------------------------------------------------------
#
# Résoudre toutes ses mains prend des heures : on en choisit une partie. Les plus chères d'abord (ce qu'il y a perdu),
# celles où se joue un leak d'abord (ses situations après le flop), ou une par ligne (les mains à revoir).

SOLVE_ORDERS = (("leaks", "liées à ses leaks, les plus chères d'abord"), ("pertes", "les plus chères"),
                ("lignes", "les mains à revoir (les plus gros pots de chaque ligne)"))
LEAK_SOLVES = 10  # les mains d'un leak passées au solveur d'un clic


def hand_keys(hand: Hand, hero: str) -> set[str]:
    """Les situations après le flop où il a décidé dans cette main (clés de Stat : « srp:cbet »…)."""
    reader = HandReader.of(hand)
    family = exploit.hand_family(reader)
    spots = reader.spots.get(hero) if family else None
    if not spots:
        return set()
    role = "agresseur" if reader.pfa == hero else "defenseur"
    seen = {ctx for ctx, _ in spots}
    return {f"{family}:{s.key}" for s in exploit.situations(family, role) if seen.intersection(s.stats)}


def solve_orders(todo: review.PendingSpots, hero: str, keys_of, found: list[Leak], picks: list[Pick]) -> dict:
    """Les mains encore à résoudre, dans chaque ordre (SOLVE_ORDERS) : [(main, minutes)] ; et, pour chaque leak qui se
    joue après le flop, les siennes (Leak.targets), les plus chères d'abord. keys_of : main -> ses situations."""
    entries = sorted(zip(todo.hands, todo.pot_types),
                     key=lambda e: (e[0].net(hero) / e[0].bb, -sum(a.amount for a in e[0].actions)))
    keys = {h.hand_id: keys_of(h) for h, _ in entries}
    merged, seen = [], set()
    for leak in found:
        if not leak.focus:
            continue
        leak.targets = [(h.hand_id, review.minutes(t)) for h, t in entries if keys[h.hand_id] & leak.focus]
        for target in leak.targets:
            if target[0] not in seen:
                seen.add(target[0])
                merged.append(target)
    lines = [(p.hand.hand_id, review.minutes(getattr(p.spot, "pot_type", "SRP"))) for p in picks
             if p.digest is None and p.spot is not None]
    return {"leaks": merged, "pertes": [(h.hand_id, review.minutes(t)) for h, t in entries], "lignes": lines}


def solve_candidates(report: Report, order: str = "leaks", leak: Optional[str] = None) -> list[tuple[str, float]]:
    """Les mains à passer au solveur pour cet ordre (ou ce leak : sa clé), dans l'ordre : [(main, minutes)]."""
    if leak is not None:
        return next((x.targets for x in report.leaks if x.key == leak), [])
    return report.solve_orders.get(order, [])


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


def build(hands: list[Hand], hero: str, kinds: Optional[dict] = None, plays: Optional[list] = None,
          stats_all: Optional[dict] = None) -> Report:
    """Le rapport complet d'un joueur (ses mains, ses adversaires classés réguliers ou récréatifs). plays : ses mains de
    départ déjà lues (handplay.collect avec la solution préflop, sur ces mains ou plus), stats_all : stats.analyze de
    ces mains — de quoi ne pas les relire."""
    if kinds is None:
        names = sorted({h.opponent_of(hero) for h in hands if h.opponent_of(hero)})
        kinds = players.classify(names, analyze(hands))
    parts = split(hands, hero, kinds)
    stats = {scope: stats_all if scope == "all" and stats_all is not None else analyze(parts[scope])
             for scope, _ in SCOPES}
    winrate = {scope: stats[scope][hero].bb_per_100 if hero in stats[scope] else None for scope, _ in SCOPES}
    info = {scope: _pct_info(stats[scope].get(hero)) for scope, _ in SCOPES}
    rows = preflop_stats(stats, hero, parts) + postflop_stats(parts, hero)
    solver = solver_review(parts["reg"], hero)
    picks = interesting(parts, hero, solver["digests"])
    losses = review.hero_losses(solver["digests"])
    plays = (handplay.with_losses(plays, parts["reg"], losses) if plays is not None else
             handplay.collect(parts["reg"], hero, stake_charts.theory(ring=False), losses))
    points = leakcheck.hu_points(parts["reg"], hero)
    leakcheck.attach(rows, leakcheck.hu_checks(rows, parts["reg"], hero, stats["reg"], plays, points))
    hand_leaks = [_hand_leak(x, len(parts["reg"]), plays=plays, hero=hero) for x in handplay.losers(plays)[:HAND_LEAKS]]
    opponents: dict = {"reg": [], "rec": []}
    for name, info_k in kinds.items():
        opponents["rec" if info_k.get("kind") == "rec" else "reg"].append(name)
    found = rank(rows, solver["groups"], solver["analyzed"], digests=solver["digests"], extra=hand_leaks)
    attach_examples(found, rows, hero, plays, points, solver["digests"])
    return Report(hero, len(hands), {s: len(parts[s]) for s, _ in SCOPES}, winrate, info, rows, solver, picks,
                  found, rec_notes(rows), opponents, exploits=kept(rows), opportunities=chances(rows),
                  solve_orders=solve_orders(solver["todo"], hero, lambda h: hand_keys(h, hero), found,
                                            picks.get("reg", [])),
                  context={"stake_charts": stake_charts.used("HU", hands)})


def selection_spots(report: Report) -> list:
    """Les mains choisies contre les réguliers (à une table à plusieurs : toutes), encore à passer au solveur."""
    return [p.spot for p in report.picks.get(report.solver_scope, []) if p.digest is None and p.spot is not None]


def _check_summary(check: leakcheck.Check) -> dict:
    """Une vérification en jeu, en données simples."""
    return {"verdict": check.verdict or "à confirmer", "bb_100": None if check.bb100 is None else round(check.bb100, 2),
            "resume": check.summary, "preuves": check.proofs}


def summary(report: Report) -> dict:
    """Le rapport en données simples (pour le coach et l'export)."""
    pct = lambda r: None if not r or not r.opps else round(100 * r.hits / r.opps)  # noqa: E731
    scopes = report.scopes
    return {
        "joueur": report.hero, "format": report.table_format, "mains": report.hands, "mains_par_type": report.scope_hands,
        "bb_100": {k: None if v is None else round(v, 1) for k, v in report.winrate.items()},
        "leaks": [{"titre": x.title, "preuve": x.evidence, "conseil": x.advice, "source": x.source,
                   "confiance": x.confidence, "main_la_plus_chere": x.example[0] if x.example else None,
                   "categorie": dict((c[0], c[1]) for c in CATEGORIES)[category(x)],
                   **({"exemples": {"mains": [e.hand for e in x.examples.shown], "en_tout": x.examples.total}}
                      if x.examples else {}),
                   **({"verifie_en_jeu": _check_summary(x.check)} if x.check else {}),
                   **({"reserve": x.note} if x.note else {}),
                   **({"lie_a": [link.text() for link in x.causes]} if x.causes else {}),
                   **({"entraine_sans_doute": x.effects} if x.effects else {})}
                  for x in report.leaks],
        "ecarts_qui_rapportent": [{"titre": x.title, "preuve": x.evidence, "conseil": x.advice,
                                   "verifie_en_jeu": _check_summary(x.check)} for x in report.exploits],
        "exploitations_possibles": [{"titre": x.title, "preuve": x.evidence, "conseil": x.advice,
                                     "verifie_en_jeu": _check_summary(x.check)} for x in report.opportunities],
        "stats": [{"section": s.section, "stat": s.label, "solveur_pct": None if s.reference is None else round(100 * s.reference),
                   **({"repere_pct": [round(100 * x) for x in s.band]} if s.band else {}),
                   **({"a_ta_taille": {"solveur_a_sa_taille_pct": round(100 * s.raw),
                                       "taille_du_solveur_pct_du_pot": round(100 * s.sizing[0]),
                                       "ta_taille_pct_du_pot": round(100 * s.sizing[1]), "decisions": s.sizing[2],
                                       "lecture": s.size_note()}} if s.sizing and s.raw is not None else {}),
                   **{f"{scope}_pct": pct(s.ratios[scope]) for scope, _ in scopes},
                   **{f"{scope}_n": s.ratios[scope].opps for scope, _ in scopes},
                   "ecart" if report.solver_scope == "all" else "ecart_reguliers":
                       "/".join(s.verdict(report.solver_scope)) if s.verdict(report.solver_scope) else None,
                   **({"en_jeu": _check_summary(s.check)} if _checked(s, report.solver_scope) else {})}
                  for s in report.stats],
        "solveur": {"mains_analysees": report.review["analyzed"], "a_analyser": len(report.review["todo"]),
                    "ev_perdue_bb": round(report.review["lost"], 1),
                    "situations": [{"situation": g["label"], "pot": family_name(g["family"]),
                                    "decisions": g["known"], "erreurs": g["errors"], "ev_perdue_bb": round(g["lost"], 2)}
                                   for g in report.review["groups"][:12] if g["known"]]},
        "mains_a_revoir": {scope: [{"main": p.hand.hand_id, "ligne": p.line, "pot_bb": p.pot_bb, "resultat_bb": p.net_bb,
                                    "analysee": p.digest is not None} for p in picks]
                           for scope, picks in report.picks.items()},
        "recreatifs": report.rec_notes,
    }
