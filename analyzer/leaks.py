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

Contre un récréatif, s'écarter de la théorie n'est pas une erreur en soi : ses stats sont montrées à côté, mais
seules celles contre les réguliers comptent pour les leaks.

Ce module fait le rapport du heads-up ; celui des tables à plusieurs (3 à 9 joueurs, ensemble) est dans
analyzer/ring_leaks.py, avec les mêmes briques (Stat, Leak, Pick, Report, rank) et la même présentation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

from . import handplay, leakcheck, players
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
# Ce qu'une décision met en jeu, relativement à l'open : le pot double à peu près à chaque street, et il est plus gros
# dans un pot 3bet ou 4bet. Une erreur à la river pèse plus que la même à l'open.
STREET_STAKE = {"preflop": 1.0, "flop": 2.0, "turn": 4.0, "river": 8.0}
POT_STAKE = {"srp": 1.0, "3bet": 2.0, "4bet": 3.0}
PREFLOP_STAKE = {"sb_first": 1.0, "bb_vs_open": 1.0, "sb_vs_3bet": 2.0, "bb_vs_4bet": 3.0}
TOP = 5
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
        """« 17 % pour tes charts », « un repère de 18 à 23 % »."""
        if self.reference is not None:
            return f"{round(100 * self.reference)} % pour {self.versus}"
        if self.band is not None:
            return f"un repère de {round(100 * self.band[0])} à {round(100 * self.band[1])} %"
        return "pas de repère"


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

    @property
    def verified(self) -> str:
        """La preuve, puis ce qu'en dit la vérification en jeu et l'éventuelle réserve (rapports PDF et PowerPoint)."""
        parts = [self.evidence] + ([self.check.summary] if self.check else []) + ([self.note] if self.note else [])
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
    out = []
    total = len(hands["all"]) or 1
    for key, node, action, label, kind in PREFLOP:
        ratios = {scope: (stats[scope].get(hero).r(key) if stats[scope].get(hero) else Ratio()) for scope, _ in SCOPES}
        if not ratios["all"].opps:
            continue
        ref = preflop.gto_value(node, action) / 100
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
                                fragile=ref is not None and flops < exploit.MIN_FLOPS,
                                stake=street_stake(s.stats[0]) * POT_STAKE[family],
                                link=(f"plan#famille={family}", "Voir le plan de jeu")))
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
    return Leak(f"{stat.section} · {stat.label}",
                f"{round(100 * p)} % sur {r.opps} occasions{SCOPE_WORDS.get(scope, '')}, contre {stat.ref_text()}"
                + (f" ({stat.note})" if stat.note else ""),
                _cap(advice) + ".", "préflop" if preflop_stat else "postflop", conf, importance(stat, scope),
                link[0], link[1], check=check, confirmed=check is not None and check.verdict == "fuite")


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
                check.verdict, v[1], gain, link[0], link[1], check=check, confirmed=True)


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
                confirmed=True)


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
    return Leak(f"{family_name(group['family'])} · {group['label']}", evidence, advice, "solveur", conf,
                100 * group["lost"] / max(analyzed, 1), f"drill:{group['family']}:{group['key']}", "S'entraîner",
                _worst(digests or [], group), confirmed=exploited is None, note=note)


def _hand_leak(x: "handplay.Loser", hands: int, scope: str = "reg", fmt: str = "HU") -> Leak:
    """Une main (ou une famille) jouée ainsi perd nettement plus que le fold, contre les réguliers (fmt « ring » : à
    une table à plusieurs, face aux charts) : d'où vient la perte (le type de pot qui coûte le plus), ce qu'en dit la
    théorie, et le coup le plus cher à revoir."""
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
                link, "Voir d'où vient la perte", example, confirmed=True)


def rank(stats: list[Stat], groups: list[dict], analyzed: int, top: int = TOP,
         digests: Optional[list[dict]] = None, extra: Optional[list[Leak]] = None, scope: str = "reg") -> list[Leak]:
    """Les leaks à travailler, les grosses erreurs d'abord : les pertes confirmées (mesurées en jeu, face au fold ou au
    solveur, ou confirmées par leurs réponses) avant les écarts encore à vérifier ; les solides avant les indicatifs ;
    puis du plus lourd au plus léger. Les écarts qui exploitent ses adversaires n'y sont pas (kept)."""
    by_key = {s.key: s for s in stats}
    leaks = [x for x in (_stat_leak(s, scope) for s in stats) if x] + list(extra or [])
    leaks += [x for x in (_solver_leak(g, analyzed, digests, by_key) for g in groups) if x]
    leaks.sort(key=lambda x: (not x.confirmed, x.confidence != "solide", -x.weight))
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
    plays = handplay.collect(parts["reg"], hero, handplay.Theory(preflop.load_solution()),
                             review.hero_losses(solver["digests"]))
    leakcheck.attach(rows, leakcheck.hu_checks(rows, parts["reg"], hero, stats["reg"], plays))
    hand_leaks = [_hand_leak(x, len(parts["reg"])) for x in handplay.losers(plays)[:HAND_LEAKS]]
    opponents: dict = {"reg": [], "rec": []}
    for name, info_k in kinds.items():
        opponents["rec" if info_k.get("kind") == "rec" else "reg"].append(name)
    return Report(hero, len(hands), {s: len(parts[s]) for s, _ in SCOPES}, winrate, info, rows, solver, picks,
                  rank(rows, solver["groups"], solver["analyzed"], digests=solver["digests"], extra=hand_leaks),
                  rec_notes(rows),
                  opponents, exploits=kept(rows), opportunities=chances(rows))


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
                   **({"verifie_en_jeu": _check_summary(x.check)} if x.check else {}),
                   **({"reserve": x.note} if x.note else {})}
                  for x in report.leaks],
        "ecarts_qui_rapportent": [{"titre": x.title, "preuve": x.evidence, "conseil": x.advice,
                                   "verifie_en_jeu": _check_summary(x.check)} for x in report.exploits],
        "exploitations_possibles": [{"titre": x.title, "preuve": x.evidence, "conseil": x.advice,
                                     "verifie_en_jeu": _check_summary(x.check)} for x in report.opportunities],
        "stats": [{"section": s.section, "stat": s.label, "solveur_pct": None if s.reference is None else round(100 * s.reference),
                   **({"repere_pct": [round(100 * x) for x in s.band]} if s.band else {}),
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
