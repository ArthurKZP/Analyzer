"""Vérifier en jeu les écarts à la théorie : une fuite qui te coûte des bb, ou une exploitation de tes adversaires ?

La théorie (solution préflop, plans de jeu des flops résolus, charts) suppose des adversaires qui jouent bien. Contre de
vrais adversaires, s'en écarter peut rapporter, en plus de simplifier le jeu : c-bet toute sa range quand ils se
couchent trop et check-raisent peu, ouvrir plus large au bouton quand ils 3bet peu, 3bet plus en BB quand ils foldent
trop face au 3bet. Chaque écart net contre les réguliers (leaks.Stat.verdict) passe trois vérifications, de la plus
directe à la plus indirecte :

1. Ce que rapporte, sur tes mains, de faire l'action plus souvent (bb par décision, intervalle de confiance à 95 %) :
   - miser ou relancer (c-bet, barrels, mise quand il checke, relance de la c-bet) : leur fold face à tes mises,
     comparé à celui de la théorie ramené à ta taille (face à une mise plus grosse, on se couche plus). Pour la main
     faible que la théorie mélange entre mise et check, les deux se valent ; chaque point de fold en plus lui rapporte
     le pot et la mise : (leur fold - celui de la théorie) × (pot + mise). Sans repère du solveur (moins de 5 flops
     résolus, ou situation sans miroir), la théorie est le bluff pur : rentable dès taille / (pot + taille) de folds ;
   - folder face à une mise : l'inverse de ce que rapportent, à partir de là, tes calls et relances avec une main
     moyenne ou faible (paire moyenne, tirage, rien), le fold rapportant 0 ;
   - avant le flop, en heads-up, l'open, le 3bet et le 4bet : leur fold face à tes relances, comparé à celui de la
     solution, comme pour une mise ;
   - avant le flop : les mains que tu joues en plus de la théorie (elle les folde le plus souvent), leur résultat (EV
     all-in) comparé au fold ; pour celles que tu joues en moins, une estimation d'après tes mains des mêmes familles.
   La mesure la plus nette décide ; sans mesure nette, ses bornes disent ce que l'écart peut coûter au plus.
   Mesuré, l'écart rapporte ou coûte : occasions pour 100 mains × écart à la théorie × bb par décision (bb/100).
2. Sinon, leurs réponses : leur fréquence dans la situation miroir, face à la théorie (ils se couchent plus que le
   solveur face à tes c-bets, ils 3bet moins que la théorie face à tes opens…).
3. Sinon, pour la c-bet au flop, le plan de jeu suggéré (miser range sur les flops où le solveur mise au moins 70 %,
   checker range où il mise 35 % ou moins) : un écart qui le suit est une simplification voulue.

Verdict : « exploit » (l'écart rapporte, ou leurs réponses le justifient), « fuite » (il coûte, ou leurs réponses le
condamnent), « plan » (conforme au plan de jeu suggéré), ou rien (pas encore de quoi trancher : l'écart reste jugé par
la théorie, mais une mesure incertaine borne déjà ce qu'il peut coûter).

Là où tu joues comme la théorie, la même mesure de leur fold face à tes mises et relances peut montrer un écart qui
rapporterait : une « opportunité » (ils se couchent trop face à tes opens : ouvre plus large), chiffrée pour 10 points
de fréquence en plus ou en moins. Les mesures sont des ordres de grandeur : les
mains jouées en plus ne sont pas tirées au hasard, et une main que la théorie checke toujours rapporte moins à miser
que celle qu'elle mélange.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import field as field_study
from . import handplay, ring
from .insights import wilson
from .lines import BOARD_SIZE
from .models import BET, CALL, FOLD, RAISE, Action, Hand
from .stats import HandReader, PlayerStats, Ratio, allin_ev
from .theory import coach, exploit, preflop, studyspots

Z = 1.96                # intervalle de confiance à 95 % des valeurs mesurées
MIN_N = 15              # décisions minimum pour mesurer une valeur
RESPONSE_N = 20         # occasions minimum pour juger une de leurs fréquences
RESPONSE_MARGIN = 0.03  # leur fréquence doit sortir de la théorie ± 3 points (intervalle de confiance à 90 %)
PLAN_CLOSE = 0.05       # près du plan de jeu : à 5 points, ou dans l'intervalle de confiance
MARGINAL = ("thin", "semi", "bluff")  # une main moyenne ou faible : paire moyenne, tirage, rien

# Les situations heads-up de la solution préflop : stat -> (situation de handplay, position, groupe d'actions de
# handplay.GROUPS : 0 relance, 1 call ou limp, 2 fold), et le nœud de la solution de chaque situation.
HU_PREFLOP = {
    "sb_first.raise": ("open", "BTN", 0), "sb_first.call": ("open", "BTN", 1),
    "bb_vs_open.fold": ("vs_open", "BB", 2), "bb_vs_open.raise": ("vs_open", "BB", 0),
    "sb_vs_3bet.fold": ("vs_3bet", "BTN", 2), "sb_vs_3bet.raise": ("vs_3bet", "BTN", 0),
    "bb_vs_4bet.fold": ("vs_4bet", "BB", 2),
}
HU_NODES = {"sb_first": "sb_open", "bb_vs_open": "bb_vs_open", "sb_vs_3bet": "sb_vs_3bet", "bb_vs_4bet": "bb_vs_4bet"}
HU_NOUNS = {"sb_first.raise": "opens", "sb_first.call": "limps", "bb_vs_open.raise": "3bets",
            "sb_vs_3bet.raise": "4bets"}
# Tes relances heads-up et leur fold : stat -> (relances avant la tienne, au bouton ?, leur fold face à ta relance)
HU_RAISES = {"sb_first.raise": (0, True, "bb_vs_open.fold"), "bb_vs_open.raise": (1, False, "sb_vs_3bet.fold"),
             "sb_vs_3bet.raise": (2, True, "bb_vs_4bet.fold")}
RING_NOUNS = {"open": "opens", "limp": "limps", "call": "calls", "3bet": "3bets", "4bet": "4bets"}
VERBS = {"opens": "ouvre", "limps": "limpe", "calls": "paie", "3bets": "3bet", "4bets": "4bet", "défenses": "défend"}

# Leur fold face à tes mises et relances : ta situation -> la leur, dans l'autre rôle.
FOLD_MIRRORS = {"cbet": "fold_flop", "barrel": "fold_turn", "barrel3": "fold_river", "raise": "fold_raise"}
# Leurs réponses : (ton rôle, ta situation) -> [(leur situation dans l'autre rôle, sens)]. Sens +1 : quand ils la
# jouent plus que la théorie, faire ton action plus souvent rapporte (ils se couchent trop face à tes c-bets : c-bet
# plus) ; -1 : moins souvent (ils check-raisent trop : c-bet moins).
POSTFLOP_MIRRORS = {
    ("agresseur", "cbet"): (("fold_flop", 1), ("raise", -1)),
    ("agresseur", "barrel"): (("fold_turn", 1),),
    ("agresseur", "barrel3"): (("fold_river", 1),),
    ("agresseur", "fold_raise"): (("raise", -1),),
    ("defenseur", "fold_flop"): (("cbet", -1),),
    ("defenseur", "fold_turn"): (("barrel", -1),),
    ("defenseur", "fold_river"): (("barrel3", -1),),
    ("defenseur", "raise"): (("fold_raise", 1),),
}
HU_PREFLOP_MIRRORS = {
    "sb_first.raise": (("bb_vs_open.fold", 1), ("bb_vs_open.raise", -1)),
    "bb_vs_open.raise": (("sb_vs_3bet.fold", 1),),
    "bb_vs_open.fold": (("sb_first.raise", -1),),
    "sb_vs_3bet.fold": (("bb_vs_open.raise", -1),),
    "sb_vs_3bet.raise": (("bb_vs_4bet.fold", 1),),
    "bb_vs_4bet.fold": (("sb_vs_3bet.raise", -1),),
}
STEALS = ("CO", "BTN", "SB")
REPLIES = {  # leur situation -> ce qu'ils font, vu de toi
    "fold_flop": "face à tes c-bets, ils se couchent",
    "fold_turn": "face à tes 2es barrels, ils se couchent",
    "fold_river": "face à tes 3es barrels, ils se couchent",
    "raise": "quand ils continuent face à ta c-bet, ils relancent",
    "cbet": "ils c-bettent le flop",
    "barrel": "ils tirent un 2e barrel",
    "barrel3": "ils tirent un 3e barrel",
    "fold_raise": "face à tes relances, ils se couchent",
    "bb_vs_open.fold": "face à tes opens, ils foldent leur BB",
    "bb_vs_open.raise": "face à tes opens, ils 3bet",
    "sb_vs_3bet.fold": "face à tes 3bets, ils se couchent",
    "sb_first.raise": "au bouton, ils ouvrent",
    "bb_vs_4bet.fold": "face à tes 4bets, ils se couchent",
    "sb_vs_3bet.raise": "face à tes 3bets, ils 4bet",
    "fold_steal": "face aux vols (CO, bouton, SB), leurs blindes se couchent",
    "threebet_steal": "face aux vols, leurs blindes 3bet",
    "threebet": "face à une ouverture, ils 3bet",
    "fold_3bet": "face au 3bet, ils se couchent",
    "pfr": "avant le flop, ils relancent",
}
NOUNS = {"cbet": "tes c-bets", "barrel": "tes 2es barrels", "delayed": "tes c-bets retardées",
         "barrel3": "tes 3es barrels", "stab": "tes mises quand il checke", "raise": "tes relances de la c-bet",
         "reraise": "tes sur-relances"}
FACING = {"fold_flop": "face à la c-bet", "fold_turn": "face au 2e barrel", "fold_river": "face au 3e barrel",
          "fold_raise": "face à une relance"}
UNITS = {"cbet": "mise", "barrel": "mise", "delayed": "mise", "barrel3": "mise", "stab": "mise", "raise": "relance",
         "reraise": "relance"}


def _num(x: float, digits: int = 1, sign: bool = False) -> str:
    """Un nombre à la française (comme report.num)."""
    text = f"{x:+,.{digits}f}" if sign else f"{x:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",").replace("-", "−")


def _pct(x: float) -> str:
    return f"{round(100 * x)} %"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


# --- Valeurs mesurées ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Value:
    """Des bb par décision : la moyenne et son intervalle de confiance à 95 %."""
    n: int
    mean: float
    lo: float
    hi: float

    @property
    def sign(self) -> int:
        """+1 ou -1 quand l'intervalle exclut 0 (le hasard n'explique pas le signe), sinon 0."""
        return 1 if self.lo > 0 else -1 if self.hi < 0 else 0

    def __neg__(self) -> "Value":
        return Value(self.n, -self.mean, -self.hi, -self.lo)

    def minus(self, other: "Value") -> "Value":
        """La différence de deux moyennes indépendantes."""
        half = math.hypot((self.hi - self.lo) / 2, (other.hi - other.lo) / 2)
        mean = self.mean - other.mean
        return Value(min(self.n, other.n), mean, mean - half, mean + half)

    def interval(self) -> str:
        return f"entre {_num(self.lo, 2, sign=True)} et {_num(self.hi, 2, sign=True)}"


def measure(values: list[float]) -> Optional[Value]:
    """La moyenne de ces valeurs (au moins MIN_N) et son intervalle à 95 %."""
    n = len(values)
    if n < MIN_N:
        return None
    mean = sum(values) / n
    half = Z * math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1) / n)
    return Value(n, mean, mean - half, mean + half)


@dataclass
class Point:
    """Une de tes décisions après le flop."""
    action: Action
    value: float           # bb rapportés à partir de là : ce que tu récupères du pot, moins ce que tu y remets
    intent: Optional[str]  # ta main quand tu paies ou relances : value, thin, semi, bluff (field.intent_of)
    pot: float             # le pot avant ta décision (bb)
    size: float            # ta mise ou relance (bb ajoutés)
    reply: Optional[str]   # leur réponse à ta mise ou relance : fold, call, raise


def points(hand: Hand, hero: str, actions: Iterable[Action]) -> dict[int, Point]:
    """Tes décisions à ces actions de la main (par identité d'action) : ce qu'elles ont rapporté à partir de là (en EV
    all-in quand un tapis a été payé avant la river), ta main quand tu paies ou relances (ce qui sert à juger tes
    folds), et leur réponse à tes mises."""
    index = {id(a): i for i, a in enumerate(hand.actions)}
    ev = allin_ev(hand)
    net = ev[hero] if ev and hero in ev else hand.net(hero)
    cards = hand.hole_cards.get(hero, [])
    out = {}
    for a in actions:
        i = index.get(id(a))
        if i is None or not hand.bb:
            continue
        put = sum(b.amount for b in hand.actions[:i] if b.player == hero)
        board = hand.board[:BOARD_SIZE[a.street]]
        intent = (field_study.intent_of(cards, board, a.street)[0]
                  if a.kind in (CALL, RAISE) and len(cards) == 2 and len(board) == BOARD_SIZE[a.street] else None)
        reply = None
        if a.kind in (BET, RAISE):
            reply = next((b.kind for b in hand.actions[i + 1:]
                          if b.street == a.street and b.player != hero and b.kind in (FOLD, CALL, RAISE)), None)
        out[id(a)] = Point(a, (net + put) / hand.bb, intent, a.pot_before / hand.bb, a.amount / hand.bb, reply)
    return out


def _sort(out: dict[str, list[Point]], hand: Hand, hero: str, spots: list[tuple[str, Action]],
          situations: list[exploit.Situation], prefix: str) -> None:
    """Range tes décisions de la main (HandReader.spots) dans les situations mesurées (clé : prefix + situation)."""
    found = points(hand, hero, [a for _, a in spots])
    seen: set[tuple[str, int]] = set()
    for ctx, a in spots:
        point = found.get(id(a))
        if point is None:
            continue
        for s in situations:
            if ctx in s.stats and (s.key, id(a)) not in seen:
                seen.add((s.key, id(a)))
                out[prefix + s.key].append(point)


def hu_points(hands: list[Hand], hero: str) -> dict[str, list[Point]]:
    """Tes décisions après le flop en heads-up, par stat (« srp:cbet »…)."""
    out: dict[str, list[Point]] = defaultdict(list)
    for h in hands:
        reader = HandReader.of(h)
        family = exploit.hand_family(reader)
        if family is None or not reader.spots.get(hero):
            continue
        role = "agresseur" if reader.pfa == hero else "defenseur"
        _sort(out, h, hero, reader.spots[hero], exploit.situations(family, role), f"{family}:")
    return out


# --- Leurs réponses --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Reply:
    """Une de leurs fréquences, dans la situation miroir de la tienne, face à la théorie."""
    text: str                                   # « face à tes c-bets, ils se couchent »
    ratio: Ratio
    ref: Optional[float] = None                 # la théorie (0 à 1)
    band: Optional[tuple[float, float]] = None  # sans théorie exacte : la fourchette d'un régulier solide
    sign: int = 1                               # voir POSTFLOP_MIRRORS
    versus: str = "le solveur"

    @property
    def lean(self) -> int:
        """+1 quand ils le font nettement plus que la théorie, -1 nettement moins, 0 sinon (ou trop peu d'occasions)."""
        if self.ratio.opps < RESPONSE_N or (self.ref is None and self.band is None):
            return 0
        lo, hi = (x / 100 for x in wilson(self.ratio))
        low, high = self.band or (self.ref - RESPONSE_MARGIN, self.ref + RESPONSE_MARGIN)
        return 1 if lo > high else -1 if hi < low else 0

    @property
    def favors(self) -> int:
        """+1 : leur réponse pousse à faire ton action plus souvent ; -1 : moins souvent ; 0 : rien de net."""
        return self.lean * self.sign

    def sentence(self) -> str:
        r = self.ratio
        ref = (_pct(self.ref) if self.ref is not None else
               f"{round(100 * self.band[0])} à {round(100 * self.band[1])} %" if self.band else "?")
        how = {1: "plus que", -1: "moins que", 0: "comme"}[self.lean]
        return f"{_cap(self.text)} {_pct(r.hits / r.opps)} ({r.opps} fois), {how} {self.versus} ({ref})."


# --- Les preuves -----------------------------------------------------------------------------------------------------

@dataclass
class Evidence:
    """Ce que rapporte, par décision, de faire l'action de la stat plus souvent ; et comment on le sait."""
    value: Optional[Value] = None
    lines: list[str] = field(default_factory=list)


def folds_value(bets: list[Point], noun: str, unit: str = "mise", reference: Optional[float] = None,
                ref_alpha: Optional[float] = None, sized: bool = True) -> tuple[Optional[Value], Optional[str]]:
    """Ce que rapporte une mise de plus avec une main faible, d'après leur fold face à tes mises (bb par mise) :
    (leur fold - celui de la théorie) × (pot + mise). reference : le fold de la théorie face à cette mise (le solveur),
    à sa taille ; ref_alpha : le fold qui rend un bluff pur rentable à la taille du solveur, pour ramener son fold à
    ta taille. Sans reference, la théorie est le bluff pur : rentable dès taille / (pot + taille) de folds.
    L'intervalle vient de celui de leur fold (Wilson à 95 %). sized : dire ta taille (en % du pot)."""
    replied = [p for p in bets if p.reply is not None]
    n = len(replied)
    if n < MIN_N:
        return None, None
    folds = sum(p.reply == FOLD for p in replied)
    pot = sum(p.pot for p in replied) / n
    size = sum(p.size for p in replied) / n
    if pot <= 0 or size <= 0:
        return None, None
    alpha = size / (pot + size)  # le fold qui rend un bluff pur rentable, à ta taille
    expected = alpha if reference is None else reference * alpha / ref_alpha if ref_alpha else reference
    expected = min(max(expected, 0.01), 0.99)
    lo, hi = (x / 100 for x in wilson(Ratio(folds, n), Z))
    value = Value(n, (folds / n - expected) * (pot + size), (lo - expected) * (pot + size),
                  (hi - expected) * (pot + size))
    verb = "rapporte" if value.mean >= 0 else "perd"
    where = f", à {_pct(size / pot)} du pot en moyenne" if sized else ""
    seen = f"Face à {noun}, ils se couchent {_pct(folds / n)} ({n} fois{where})"
    if reference is None:
        text = (f"{seen} ; un bluff pur y est rentable dès {_pct(alpha)} de folds : il {verb} "
                f"{_num(abs(value.mean), 2)} bb par {unit} ({value.interval()}).")
    else:
        theory = "attendus face à la théorie à ta taille" if ref_alpha else "face à la théorie"
        text = (f"{seen}, contre {_pct(expected)} {theory} : chaque {unit} d'une main faible en plus de la théorie "
                f"{verb} {_num(abs(value.mean), 2)} bb ({value.interval()}).")
    return value, text


def solver_alpha(families: Iterable[str], node: str) -> Optional[float]:
    """Le fold qui rend un bluff pur rentable à la taille du solveur à ce nœud du plan de jeu (« cbet », « barrel »…),
    en moyenne sur ses flops résolus (coach.max_fold) ; None sans taille connue."""
    found = []
    for family in families:
        for data in coach.plans(family):
            alpha = coach.max_fold(data.get("nodes", {}).get(node, {}).get("sizes") or [])
            if alpha is not None:
                found.append(alpha)
    return sum(found) / len(found) if found else None


def postflop_evidence(s: exploit.Situation, found: list[Point], reference: Optional[float] = None,
                      ref_alpha: Optional[float] = None) -> Evidence:
    """Ce que rapporte de faire l'action de la situation plus souvent : miser ou relancer (folds_value : leur fold face
    à tes mises, face à la théorie), folder (l'inverse de tes calls et relances avec une main moyenne ou faible)."""
    out = Evidence()
    if s.measure in ("bet", "raise"):
        kind = BET if s.measure == "bet" else RAISE
        out.value, text = folds_value([p for p in found if p.action.kind == kind], NOUNS.get(s.key, "tes mises"),
                                      UNITS.get(s.key, "mise"), reference, ref_alpha)
        if text:
            out.lines.append(text)
    elif s.measure == "fold":
        continued = measure([p.value for p in found if p.action.kind in (CALL, RAISE) and p.intent in MARGINAL])
        if continued is not None:
            out.value = -continued
            out.lines.append(f"Tes calls et relances {FACING.get(s.key, 'face à une mise')} avec une main moyenne ou "
                             f"faible (paire moyenne, tirage, rien) : {_num(continued.mean, 2, sign=True)} bb chacun à "
                             f"partir de là ({continued.n} fois, {continued.interval()}) ; le fold rapporte 0.")
    return out


def _theory(d: handplay.Decision) -> list[float]:
    """Fréquences de la théorie par groupe d'actions (relance, call, fold) pour la main de la décision."""
    out = [0.0, 0.0, 0.0]
    for action, freq in (d.theory or {}).items():
        if action in handplay.GROUPS:
            out[handplay.GROUPS[action]] += freq
    return out


def preflop_evidence(rows: list[tuple[handplay.Played, handplay.Decision]], group: int, direction: str,
                     noun: str) -> Evidence:
    """Ce que rapporte, avant le flop, de faire plus souvent l'action du groupe (handplay.GROUPS) : rows, tes décisions
    de la situation ; direction, ton écart à la théorie (« plus », « moins »). Le résultat d'une main jouée est celui de
    toute la main (EV all-in), comparé à ce qu'aurait coûté le fold à ce moment."""
    known = []
    for p, d in rows:
        if d.theory is not None and d.fold_bb is not None and d.action in handplay.GROUPS:
            known.append((p, d, _theory(d), handplay.GROUPS[d.action]))
    out = Evidence()
    playing = group != 2
    if (direction == "plus") == playing:  # tu joues plus de mains que la théorie
        if playing:  # celles que la théorie folde le plus souvent (une relance qu'elle paierait ne se compare pas)
            extra = [(p, d) for p, d, t, g in known
                     if g == group and t[group] < 0.5 and t[2] >= max(t[k] for k in range(2) if k != group)]
            word = noun
        else:
            extra = [(p, d) for p, d, t, g in known if g != 2 and t[2] >= 0.5]
            word = "défenses"
        played = measure([p.ev_bb - d.fold_bb for p, d in extra])
        if played is not None:
            out.value = played if playing else -played
            out.lines.append(f"Tes {word} avec des mains que la théorie folde le plus souvent : "
                             f"{_num(played.mean, 2, sign=True)} bb par main par rapport au fold (EV all-in, "
                             f"{played.n} fois, {played.interval()}).")
        return out
    # tu en joues moins : ce que rapportent tes mains des mêmes familles, jouées ainsi (une estimation)
    if playing:
        missing = [p for p, d, t, g in known if g == 2 and t[group] >= 0.5]
        word = noun
    else:
        missing = [p for p, d, t, g in known if g == 2 and t[2] < 0.5]
        word = "défenses"
    families = {handplay.family(p.combo) for p in missing}
    pool = [(p, d) for p, d, t, g in known if handplay.family(p.combo) in families and
            (g == group if playing else g != 2)]
    played = measure([p.ev_bb - d.fold_bb for p, d in pool]) if missing else None
    if played is not None:
        out.value = played if playing else -played
        out.lines.append(f"Tu foldes {len(missing)} mains que la théorie {VERBS.get(word, 'joue')} le plus souvent ; "
                         f"tes {word} avec des mains des mêmes familles rapportent {_num(played.mean, 2, sign=True)} bb "
                         f"par main par rapport au fold ({played.n} fois, {played.interval()}) : une estimation de ce "
                         "que ces folds te coûtent.")
    return out


# --- Le verdict ------------------------------------------------------------------------------------------------------

@dataclass
class Check:
    """La vérification en jeu d'un écart à la théorie."""
    verdict: str                     # exploit, fuite, plan, ou "" (pas de quoi trancher)
    direction: str                   # l'écart : « plus », « moins »
    bb100: Optional[float] = None    # ce qu'il rapporte (+) ou coûte (-) pour 100 mains contre les réguliers, d'après
    # la valeur mesurée (même incertaine)
    low: Optional[float] = None      # l'intervalle de confiance à 95 % de bb100
    high: Optional[float] = None
    measured: bool = False           # le verdict vient de la mesure (son intervalle exclut 0)
    proofs: list[str] = field(default_factory=list)

    @property
    def cost(self) -> Optional[float]:
        """Ce que l'écart coûte (bb/100, positif quand il coûte), d'après la mesure."""
        return None if self.bb100 is None else -self.bb100

    @property
    def max_cost(self) -> Optional[float]:
        """Ce qu'il peut coûter au plus (bb/100, borne de l'intervalle à 95 %), d'après la mesure."""
        return None if self.low is None else max(-self.low, 0.0)

    @property
    def summary(self) -> str:
        if self.verdict == "opportunité":
            return (f"Tu joues ici comme la théorie, mais leur fold face à tes mises ou relances rend un écart "
                    f"rentable : "
                    f"{'plus' if self.direction == 'plus' else 'moins'} souvent, chaque tranche de 10 points "
                    f"rapporterait environ {_num(self.bb100, 1, sign=True)} bb/100.")
        if self.verdict == "exploit":
            if self.measured:
                return (f"Vérifié en jeu : cet écart te rapporte environ {_num(self.bb100, 1, sign=True)} bb/100 contre "
                        "ces adversaires ; c'est une exploitation, pas une fuite.")
            return "Vérifié en jeu : leurs réponses justifient cet écart (son gain n'est pas chiffré)."
        if self.verdict == "fuite":
            if self.measured:
                return f"Vérifié en jeu : cet écart te coûte environ {_num(-self.bb100, 1)} bb/100."
            return "Vérifié en jeu : leurs réponses le condamnent, cet écart te coûte (son coût n'est pas chiffré)."
        if self.verdict == "plan":
            return "Conforme au plan de jeu suggéré : une simplification voulue, pas une fuite."
        if self.bb100 is not None:
            return (f"Pas encore de quoi trancher en jeu : d'après tes mains, cet écart rapporte entre "
                    f"{_num(self.low, 1, sign=True)} et {_num(self.high, 1, sign=True)} bb/100.")
        return "Pas encore de quoi dire, en jeu, s'il te coûte : l'écart reste jugé par la théorie."

    @property
    def badge(self) -> str:
        """En un mot, pour les tableaux : « coûte 1,2 bb/100 », « rapporte +0,8 bb/100 », « plan de jeu »…"""
        if self.verdict == "exploit":
            return f"rapporte {_num(self.bb100, 1, sign=True)} bb/100" if self.measured else "justifié"
        if self.verdict == "fuite":
            return f"coûte {_num(-self.bb100, 1)} bb/100" if self.measured else "fuite"
        if self.verdict == "plan":
            return "plan de jeu"
        if self.verdict == "opportunité":
            return f"à exploiter : {self.direction} ({_num(self.bb100, 1, sign=True)} bb/100 par 10 pts)"
        if self.bb100 is not None:
            return f"coût ≤ {_num(self.max_cost, 1)} bb/100"
        return "à confirmer"


def judge(stat, evidence: Evidence, replies: list[Reply], hands: int,
          plan: Optional[tuple[float, int]] = None, scope: str = "reg") -> Optional[Check]:
    """Le verdict d'un écart net (stat : leaks.Stat) : la valeur mesurée d'abord, puis leurs réponses, puis le plan de
    jeu (c-bet au flop). hands : tes mains de la portée, pour ramener l'écart à 100 mains."""
    found = stat.verdict(scope)
    if not found:
        return None
    direction = found[0]
    sense = 1 if direction == "plus" else -1
    r = stat.ratios[scope]
    extra = 100 * r.opps / max(hands, 1) * stat.gap(scope)  # décisions jouées autrement que la théorie, pour 100 mains
    proofs = list(evidence.lines) + [x.sentence() for x in replies if x.lean]
    value = evidence.value
    check = Check("", direction, proofs=proofs)
    if value is not None:  # ce que l'écart rapporte pour 100 mains, et son intervalle
        bounds = sorted((sense * value.lo * extra, sense * value.hi * extra))
        check.bb100, check.low, check.high = sense * value.mean * extra, bounds[0], bounds[1]
        if value.sign:
            check.verdict, check.measured = ("exploit" if value.sign == sense else "fuite"), True
            if any(x.favors == -value.sign for x in replies):
                proofs.append("À nuancer : une de leurs réponses va dans l'autre sens.")
            return check
    leans = {x.favors for x in replies if x.favors}
    if len(leans) == 1:
        check.verdict = "exploit" if leans.pop() == sense else "fuite"
        return check
    reference = stat.ref_for(scope)
    if plan is not None and reference is not None and r.opps:
        target, flops = plan
        p = r.hits / r.opps
        lo, hi = (x / 100 for x in wilson(r))
        if (target - reference) * sense >= RESPONSE_MARGIN and (lo <= target <= hi or abs(p - target) <= PLAN_CLOSE):
            proofs.append(f"Le plan de jeu suggéré c-bet {_pct(target)} : il mise range sur les flops où le solveur mise "
                          f"au moins {_pct(coach.RANGE_BET)}, et checke range là où il mise "
                          f"{_pct(coach.CHECK_RANGE)} ou moins ({flops} flops résolus).")
            check.verdict = "plan"
    return check


def _mirrored(stat) -> bool:
    """La stat se mesure par leur fold face à tes mises ou relances (une opportunité possible sans écart)."""
    if stat.key in HU_RAISES:
        return True
    situation = stat.key.rsplit(":", 1)[-1]
    return not stat.key.startswith("pre:") and stat.kind in ("bet", "raise") and situation in NOUNS


def _mirrors(role: str, s: exploit.Situation, evidence: Evidence) -> tuple[tuple[str, int], ...]:
    """Leurs réponses à juger pour ta situation, sans leur fold face à tes mises quand il est déjà mesuré."""
    measured = FOLD_MIRRORS.get(s.key) if s.measure in ("bet", "raise") and evidence.value is not None else None
    return tuple(m for m in POSTFLOP_MIRRORS.get((role, s.key), ()) if m[0] != measured)


def solid(base: dict[str, tuple[float, int]]) -> dict[str, float]:
    """Les repères du solveur tirés d'assez de flops résolus (exploit.MIN_FLOPS) : situation -> fréquence."""
    return {key: value for key, (value, flops) in base.items() if flops >= exploit.MIN_FLOPS}


def chance(stat, evidence: Evidence, hands: int, replies: Iterable[Reply] = (),
           scope: str = "reg") -> Optional[Check]:
    """Une opportunité : là où tu joues comme la théorie (pas d'écart net, mais un repère), leur fold face à tes mises
    ou relances rend un écart nettement rentable, sans qu'une autre de leurs réponses dise le contraire (ils
    relancent plus que la théorie…) ; chiffrée pour 10 points de fréquence en plus ou en moins."""
    r = stat.ratios.get(scope)
    value = evidence.value
    if (value is None or not value.sign or stat.verdict(scope) or r is None or r.opps < RESPONSE_N
            or (stat.ref_for(scope) is None and stat.band is None)):
        return None
    replies = list(replies)
    if any(x.favors == -value.sign for x in replies):
        return None
    per10 = 100 * r.opps / max(hands, 1) * 0.10  # décisions jouées autrement, pour 100 mains, à 10 points d'écart
    bounds = sorted((value.sign * value.lo * per10, value.sign * value.hi * per10))
    return Check("opportunité", "plus" if value.sign > 0 else "moins", abs(value.mean) * per10, bounds[0], bounds[1],
                 True, list(evidence.lines) + [x.sentence() for x in replies if x.lean])


def plan_cbet(families: Iterable[str]) -> Optional[tuple[float, int]]:
    """La c-bet au flop du plan de jeu suggéré, sur les flops résolus de ces familles : 100 % là où il mise range,
    0 % là où il checke range, la fréquence du solveur ailleurs ; et le nombre de flops."""
    shares = []
    for family in families:
        for data in coach.plans(family):
            row = coach.flop_row(data)
            shares.append(1.0 if row["strategy"] == "range" else 0.0 if row["strategy"] == "check" else row["cbet"])
    return (sum(shares) / len(shares), len(shares)) if shares else None


# --- Heads-up --------------------------------------------------------------------------------------------------------

def hu_raises(hands: list[Hand], hero: str, before: int, button: bool) -> list[Point]:
    """Tes relances préflop heads-up après before relances (0 : l'open, 1 : le 3bet, 2 : le 4bet), au bouton ou en BB,
    avec leur réponse."""
    out = []
    for h in hands:
        if not h.bb or (hero == h.button) != button:
            continue
        pre = [(i, a) for i, a in enumerate(h.actions) if a.street == "preflop" and a.kind in (FOLD, CALL, RAISE)]
        raises = 0
        for k, (i, a) in enumerate(pre):
            if a.player == hero and raises == before:
                if a.kind == RAISE:
                    reply = next((b.kind for _, b in pre[k + 1:] if b.player != hero), None)
                    out.append(Point(a, 0.0, None, a.pot_before / h.bb, a.amount / h.bb, reply))
                break
            raises += a.kind == RAISE
    return out


def strongest(*found: Evidence) -> Evidence:
    """Plusieurs mesures d'une même décision : la plus nette décide (la première, à égalité) ; toutes les preuves."""
    measured = [e for e in found if e.value is not None]
    best = next((e for e in measured if e.value.sign), measured[0] if measured else None)
    return Evidence(best.value if best else None, [line for e in found for line in e.lines])


def _hu_theory(key: str) -> Optional[float]:
    situation, action = key.split(".")
    node = HU_NODES.get(situation)
    try:
        return preflop.gto_value(node, action) / 100 if node else None
    except (KeyError, AttributeError):
        return None


def hu_checks(stats: list, hands: list[Hand], hero: str, players: dict[str, PlayerStats],
              plays: list[handplay.Played]) -> dict[str, Check]:
    """Les vérifications des écarts nets contre les réguliers, en heads-up. stats : leaks.Stat ; hands : tes mains
    contre les réguliers ; players : les stats de chacun sur ces mains (stats.analyze) ; plays : tes mains de départ
    (handplay.collect, avec la solution préflop)."""
    by_key = {s.key: s for s in stats}
    flagged = {k: s for k, s in by_key.items() if s.verdict("reg")}
    measured = {k: s for k, s in by_key.items() if not s.verdict("reg") and _mirrored(s)}  # leurs opportunités
    if not flagged and not measured:
        return {}
    opponents = {h.opponent_of(hero) for h in hands} - {None, hero}
    found = hu_points(hands, hero)
    out: dict[str, Optional[Check]] = {}
    for family in studyspots.FAMILIES:
        for role, other in (("agresseur", "defenseur"), ("defenseur", "agresseur")):
            mine = [s for s in exploit.situations(family, role) if f"{family}:{s.key}" in flagged or
                    f"{family}:{s.key}" in measured]
            if not mine:
                continue
            counts = exploit.observed(hands, opponents, family, other)[0]
            base = solid(exploit.baselines(family, other))
            for s in mine:
                stat = by_key[f"{family}:{s.key}"]
                evidence = postflop_evidence(s, found.get(stat.key, []), base.get(FOLD_MIRRORS.get(s.key, "")),
                                             solver_alpha([family], s.plan))
                replies = [Reply(REPLIES[key], Ratio(*counts.get(key, (0, 0))), base[key], sign=sign)
                           for key, sign in _mirrors(role, s, evidence) if key in base]
                if stat.key in measured:
                    out[stat.key] = chance(stat, evidence, len(hands), replies)
                    continue
                out[stat.key] = judge(stat, evidence, replies, len(hands),
                                      plan_cbet([family]) if s.key == "cbet" else None)
    theirs = field_study.merge_stats([players[name] for name in opponents if name in players])
    for key, (situation, position, group) in HU_PREFLOP.items():
        stat = flagged.get(key)
        if stat is None:
            if key in measured and key in HU_RAISES:  # pas d'écart : leur fold face à tes relances, une opportunité ?
                before, button, fold_key = HU_RAISES[key]
                noun = HU_NOUNS[key]
                value, text = folds_value(hu_raises(hands, hero, before, button), "tes " + noun, noun[:-1],
                                          _hu_theory(fold_key), sized=False)
                replies = [Reply(REPLIES[k], theirs.r(k), _hu_theory(k), sign=sign)
                           for k, sign in HU_PREFLOP_MIRRORS.get(key, ()) if k != fold_key]
                out[key] = chance(measured[key], Evidence(value, [text] if text else []), len(hands), replies)
            continue
        rows = [(p, d) for p in plays if p.position == position for d in p.decisions if d.situation == situation]
        noun = HU_NOUNS.get(key, "décisions")
        evidence = preflop_evidence(rows, group, stat.verdict("reg")[0], noun)
        fold_key = None
        if key in HU_RAISES:  # leur fold face à tes relances, face à la solution
            before, button, fold_key = HU_RAISES[key]
            value, text = folds_value(hu_raises(hands, hero, before, button), "tes " + noun, noun[:-1],
                                      _hu_theory(fold_key), sized=False)
            evidence = strongest(Evidence(value, [text] if text else []), evidence)
            if value is None:
                fold_key = None
        replies = [Reply(REPLIES[k], theirs.r(k), _hu_theory(k), sign=sign)
                   for k, sign in HU_PREFLOP_MIRRORS.get(key, ()) if k != fold_key]  # leur fold : déjà mesuré
        out[key] = judge(stat, evidence, replies, len(hands))
    return {k: v for k, v in out.items() if v is not None}


# --- Tables à plusieurs ----------------------------------------------------------------------------------------------

def ring_points(hands: list[Hand], hero: str, pots: dict) -> tuple[dict[str, list[Point]], dict]:
    """Dans tes pots à deux au flop (ring_leaks.Pot) : tes décisions, par stat (« srp_ip:agresseur:cbet »), et les
    fréquences de ton adversaire du flop, par (structure, son rôle)."""
    from . import ring_leaks  # ring_leaks importe ce module
    out: dict[str, list[Point]] = defaultdict(list)
    theirs: dict[tuple[str, str], exploit.Ratios] = {}
    for h in hands:
        pot = pots.get(h.hand_id)
        families = ring_leaks._families(pot.structure) if pot is not None else []
        if not families:
            continue
        reader = HandReader(ring_leaks._view(h, pot))
        role = "agresseur" if hero == pot.aggressor else "defenseur"
        villain = pot.ip if hero == pot.oop else pot.oop
        _sort(out, h, hero, reader.spots.get(hero, []), exploit.situations(families[0], role),
              f"{pot.structure}:{role}:")
        st = theirs.setdefault((pot.structure, "defenseur" if role == "agresseur" else "agresseur"), exploit.Ratios())
        for key, made in reader.events.get(villain, []):
            st.ratios[key].add(made)
    return out, theirs


def population(hands: list[Hand], names: set[str]) -> dict[str, Ratio]:
    """Leurs fréquences avant le flop aux tables à plusieurs (ring.read), ensemble."""
    out: dict[str, Ratio] = defaultdict(Ratio)
    for h in hands:
        for player in names & set(h.seats):
            for key, made in ring.read(h, player).items():
                out[key].add(made)
    return dict(out)


def ring_mirrors(situation: str, position: str, word: str) -> tuple[tuple[str, int], ...]:
    """Leurs réponses à une de tes décisions préflop, dans leurs fréquences d'ensemble (field.RING_DEFS)."""
    if situation == "open" and word == "open":
        return (("fold_steal", 1), ("threebet_steal", -1)) if position in STEALS else (("threebet", -1),)
    if situation == "vs_open":
        return {"3bet": (("fold_3bet", 1),), "fold": (("pfr", -1),)}.get(word, ())
    if situation == "vs_3bet" and word == "fold":
        return (("threebet", -1),)
    return ()


def ring_checks(stats: list, hands: list[Hand], hero: str, pots: dict, plays: list[handplay.Played],
                kinds: Optional[dict] = None) -> dict[str, Check]:
    """Les vérifications des écarts nets contre les réguliers, aux tables à plusieurs. stats : ring_leaks.stats ;
    hands : tes mains contre les réguliers ; pots : tes pots à deux au flop (ring_leaks._pots) ; plays : tes mains de
    départ (handplay.collect, avec tes charts) ; kinds : le type de tes adversaires."""
    from . import ring_leaks  # ring_leaks importe ce module
    by_key = {s.key: s for s in stats}
    flagged = {k: s for k, s in by_key.items() if s.verdict("reg")}
    measured = {k: s for k, s in by_key.items() if not s.verdict("reg") and _mirrored(s)}  # leurs opportunités
    if not flagged and not measured:
        return {}
    kinds = kinds or {}
    out: dict[str, Optional[Check]] = {}
    if measured or any(not k.startswith("pre:") for k in flagged):
        found, theirs = ring_points(hands, hero, pots)
        for structure, _ in ring_leaks.STRUCTURES:
            families = ring_leaks._families(structure)
            if not families:
                continue
            for role, other in (("agresseur", "defenseur"), ("defenseur", "agresseur")):
                mine = [s for s in exploit.situations(families[0], role)
                        if f"{structure}:{role}:{s.key}" in flagged or f"{structure}:{role}:{s.key}" in measured]
                if not mine:
                    continue
                st = theirs.get((structure, other), exploit.Ratios())
                base = solid(ring_leaks._baselines(families, other))
                situations = {s.key: s for s in exploit.situations(families[0], other)}
                for s in mine:
                    stat = by_key[f"{structure}:{role}:{s.key}"]
                    evidence = postflop_evidence(s, found.get(stat.key, []), base.get(FOLD_MIRRORS.get(s.key, "")),
                                                 solver_alpha(families, s.plan))
                    replies = [Reply(REPLIES[key], Ratio(*exploit.tally(st, situations[key])), base[key], sign=sign)
                               for key, sign in _mirrors(role, s, evidence) if key in base and key in situations]
                    if stat.key in measured:
                        out[stat.key] = chance(stat, evidence, len(hands), replies)
                        continue
                    out[stat.key] = judge(stat, evidence, replies, len(hands),
                                          plan_cbet(families) if s.key == "cbet" else None)
    preflop_keys = [k for k in flagged if k.startswith("pre:")]
    if preflop_keys:
        names = {p for h in hands for p in h.seats if p != hero and kinds.get(p, {}).get("kind") != "rec"}
        theirs_pf = population(hands, names)
        regular = {h.hand_id for h in hands}
        mine_plays = [p for p in plays if p.hand_id in regular]
        groups = {(situation, word): group for situation, _, rows in ring_leaks.PREFLOP for group, word, _, _ in rows}
        for key in preflop_keys:
            _, situation, position, word = key.split(":")
            group = groups.get((situation, word))
            if group is None:
                continue
            stat = flagged[key]
            rows = [(p, d) for p in mine_plays if p.position == position for d in p.decisions
                    if d.situation == situation]
            replies = []
            for k, sign in ring_mirrors(situation, position, word):
                d = field_study.RING_DEF.get(k)
                if d is not None:
                    replies.append(Reply(REPLIES[k], theirs_pf.get(k, Ratio()), band=(d.ref[0] / 100, d.ref[1] / 100),
                                         sign=sign, versus="un régulier solide"))
            out[key] = judge(stat, preflop_evidence(rows, group, stat.verdict("reg")[0], RING_NOUNS.get(word, word)),
                             replies, len(hands))
    return {k: v for k, v in out.items() if v is not None}


def attach(stats: list, checks: dict[str, Check]) -> None:
    """Range chaque vérification sur sa stat (Stat.check)."""
    for s in stats:
        s.check = checks.get(s.key)


# --- Les décisions du solveur -------------------------------------------------------------------------------------

def solver_stat(family: str, key: str, category: str) -> Optional[str]:
    """La stat (clé de leaks.Stat) d'une situation du solveur (theory/review.py : famille du pot, clé « bet:fi: »,
    « face:fo::1 »…, catégorie d'action « fold », « passive », « aggressive »), ou None. Sert à relativiser l'EV
    perdue face au solveur quand l'écart en cause exploite tes adversaires."""
    if family in studyspots.FAMILIES:
        side = "o" if coach.aggressor_of(family) == 0 else "i"
        prefix = f"{family}:"
        roles = {"agresseur": "", "defenseur": ""}
    else:
        info = studyspots.ring_family(family)
        if not info:
            return None
        structure = info["structure"]
        side = "i" if structure.endswith("_ip") else "o"
        prefix = f"{structure}:"
        roles = {"agresseur": "agresseur:", "defenseur": "defenseur:"}
    kind, where, past, *level = key.split(":")
    street, actor = where[0], where[1]
    other = "o" if side == "i" else "i"
    situation = None
    if actor == side:
        role = "agresseur"
        if kind == "bet" and category in ("aggressive", "passive"):
            situation = {("f", ""): "cbet", ("t", side): "barrel", ("r", side * 2): "barrel3",
                         ("t", "x"): "delayed"}.get((street, past))
        elif kind == "face" and level and int(level[0]) >= 2:
            situation = "fold_raise" if category == "fold" else "reraise" if category == "aggressive" else None
    else:
        role = "defenseur"
        if kind == "face" and level == ["1"]:
            facing = {("f", ""): "fold_flop", ("t", side): "fold_turn", ("r", side * 2): "fold_river"}.get((street, past))
            situation = facing if category == "fold" else "raise" if category == "aggressive" and street == "f" else None
        elif kind == "bet" and category in ("aggressive", "passive"):
            stab = ("f", "") if other == "i" else ("t", "x")
            situation = "stab" if (street, past) == stab else None
    return None if situation is None else f"{prefix}{roles[role]}{situation}"
