"""Étude du field : le leakfinding de tes adversaires, pour les exploiter.

Pour un adversaire que tu croises assez (Paramètres : mains minimum), ou un groupe de joueurs :

- ses leaks à exploiter : ses fréquences qui s'écartent nettement de celles d'un régulier solide (intervalle de
  confiance à 90 % hors du repère ; repères d'insights en heads-up, RING_DEFS aux tables à plusieurs), chacune avec
  l'exploit qui en découle ;
- sa value et ses bluffs, ligne par ligne : chaque mise ou relance après le flop est rangée par street, ligne
  (c-bet, barrel, mise après check, check-raise…) et taille. Quand ses cartes sont montrées, son intention au moment
  de miser : value (top paire ou mieux), value fine (paire moyenne ou faible), semi-bluff (un tirage, sans main faite,
  avant la river), bluff (rien). Ce qui distingue sa value de ses bluffs (tells) : la taille, le temps de réflexion,
  la carte qui vient de tomber, le nombre de joueurs dans le pot ;
- ce qu'il montre quand il checke, et ses mains montrées par ligne préflop.

Les récréatifs se rangent par style (style_of) : passifs (ils paient beaucoup et misent peu), agressifs (ils misent et
relancent beaucoup), prudents (ils jouent peu de mains ou abandonnent vite) ; chaque groupe a son plan (STYLES).

Heads-up : les stats de stats.HandReader (PlayerStats). Tables à plusieurs (3 à 9 joueurs ensemble) : ring.read et
les fréquences d'après le flop lues ici (postflop_read) ; toutes les mains allées à l'abattage comptent, même celles
où tu n'étais plus (l'historique montre les cartes).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Optional

from . import ring
from .cards import combo_notation, describe_holding
from .insights import Finding, R, StatDef, deviation, findings, strength_class, wilson
from .lines import BOARD_SIZE, PREVIOUS_PHRASE, size_class
from .models import BET, CALL, CHECK, FOLD, POSTFLOP, RAISE, VOLUNTARY, Hand
from .stats import PlayerStats, Ratio, think_times
from .theory import coach, studyspots

INTENTS = ("value", "thin", "semi", "bluff")
INTENT_LABELS = {"value": "Value", "thin": "Value fine", "semi": "Semi-bluff", "bluff": "Bluff"}
VALUE, BLUFF = ("value", "thin"), ("semi", "bluff")
CARD_TEXT = {"over": "une overcard", "brick": "une brique", "paired": "une carte qui paire le board",
             "flush": "une carte de couleur", "straight": "une carte de quinte"}
MIN_SHOWN = 3        # mises montrées minimum pour juger une ligne
TELL_SIZE = 20       # écart de taille médiane (points de % du pot) entre value et bluffs qui en fait un tell
LEAKS_SHOWN = 3      # leaks par adversaire dans les listes


# --- Intention d'une mise montrée -------------------------------------------------------------------------------------

def intent_of(cards: list[str], board: list[str], street: str) -> tuple[str, str]:
    """(intention, description de sa main) au moment de miser, d'après sa main faite : value (top paire ou mieux),
    value fine (paire moyenne ou faible), semi-bluff (un tirage avant la river), bluff."""
    description = describe_holding(cards, board)
    strength = strength_class(description)
    if strength in ("Top paire / overpair", "Deux paires +"):
        return "value", description
    if strength == "Paire moyenne/faible":
        return "thin", description
    if strength == "Tirage" and street != "river":
        return "semi", description
    return "bluff", description


def known(hand: Hand, player: str) -> bool:
    """Ses cartes sont connues : montrées à l'abattage."""
    return hand.showdown and len(hand.hole_cards.get(player, [])) == 2


# --- Ses mises après le flop, ligne par ligne -------------------------------------------------------------------------

@dataclass
class Bet:
    """Une mise ou une relance du joueur après le flop."""
    hand: Hand
    index: int
    player: str
    street: str
    label: str              # « C-bet », « Barrel (a misé le flop) », « Check-raise »…
    size: str               # petite, grosse, overbet ; rien pour une relance
    pct: Optional[float]    # taille en % du pot (mises)
    think: Optional[float]  # temps de réflexion (s)
    card: Optional[str]     # flop : texture ; turn, river : ce que la carte tombée change (coach.card_class)
    multiway: bool          # plus de deux joueurs dans le pot
    intent: Optional[str] = None
    description: str = ""

    @property
    def bluffish(self) -> bool:
        return self.intent in BLUFF


def preflop_aggressor(hand: Hand) -> Optional[str]:
    return next((a.player for a in reversed(hand.actions) if a.street == "preflop" and a.kind == RAISE), None)


def _previous(hand: Hand, player: str, street: str) -> Optional[str]:
    prev = POSTFLOP[POSTFLOP.index(street) - 1]
    kinds = [a.kind for a in hand.actions if a.street == prev and a.player == player]
    return next((k for k in (RAISE, BET, CALL, CHECK) if k in kinds), None)


def line_of(hand: Hand, index: int, pfa: Optional[str]) -> tuple[str, str]:
    """(ligne, classe de taille) de la mise ou relance hand.actions[index], quel que soit le nombre de joueurs."""
    a = hand.actions[index]
    before = [b for b in hand.actions[:index] if b.street == a.street and b.kind in VOLUNTARY]
    if a.kind == RAISE:
        checked_first = any(b.player == a.player and b.kind == CHECK for b in before)
        return ("Check-raise" if checked_first else "Relance"), ""
    size = size_class(100.0 * a.amount / a.pot_before) if a.pot_before else ""
    if a.street == "flop":
        if pfa is None:
            return "Mise (pot limpé)", size
        if pfa == a.player:
            return "C-bet", size
        folded = {b.player for b in hand.actions[:index] if b.kind == FOLD}
        if pfa not in folded and pfa not in {b.player for b in before}:
            return "Donk (avant l'agresseur)", size
        return "Mise après check", size
    previous = _previous(hand, a.player, a.street)
    phrase = PREVIOUS_PHRASE.get(previous, "Mise")
    return phrase.format(prev=POSTFLOP[POSTFLOP.index(a.street) - 1]), size


def _feature(board: list[str], street: str) -> Optional[str]:
    n = BOARD_SIZE[street]
    if len(board) < n:
        return None
    if street == "flop":
        return studyspots.flop_texture(board[:3])
    return coach.card_class(board[:n - 1], board[n - 1])


def player_bets(hands: Iterable[Hand], names: Iterable[str], all_cards: bool = False) -> list[Bet]:
    """Toutes les mises et relances de ces joueurs après le flop, avec leur intention quand les cartes sont montrées
    (all_cards : dès qu'elles sont connues, même sans abattage — les mains d'un héros)."""
    names = set(names)
    out = []
    for h in hands:
        mine = names & set(h.seats)
        if not mine or len(h.board) < 3:
            continue
        pfa = preflop_aggressor(h)
        times = think_times(h)
        folded: set[str] = set()
        for i, a in enumerate(h.actions):
            if a.kind == FOLD:
                folded.add(a.player)
            if a.player not in mine or a.street == "preflop" or a.kind not in (BET, RAISE):
                continue
            label, size = line_of(h, i, pfa)
            live = [p for p in h.seats if p not in folded]
            pct = 100.0 * a.amount / a.pot_before if a.kind == BET and a.pot_before else None
            bet = Bet(h, i, a.player, a.street, label, size, pct, times[i], _feature(h.board, a.street), len(live) > 2)
            if known(h, a.player) or (all_cards and len(h.hole_cards.get(a.player, [])) == 2):
                bet.intent, bet.description = intent_of(h.hole_cards[a.player], h.board[:BOARD_SIZE[a.street]], a.street)
            out.append(bet)
    return out


@dataclass
class Line:
    street: str
    label: str
    size: str = ""
    bets: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return " · ".join(part for part in (self.label, self.size) if part)

    @property
    def count(self) -> int:
        return len(self.bets)

    @property
    def shown(self) -> list[Bet]:
        return [b for b in self.bets if b.intent]

    @property
    def intents(self) -> Counter:
        return Counter(b.intent for b in self.shown)


def lines_of(bets: list[Bet]) -> list[Line]:
    """Les mises par ligne (street, ligne, taille), les plus fréquentes d'abord à chaque street."""
    out: dict[tuple, Line] = {}
    for b in bets:
        out.setdefault((b.street, b.label, b.size), Line(b.street, b.label, b.size)).bets.append(b)
    order = {s: k for k, s in enumerate(POSTFLOP)}
    return sorted(out.values(), key=lambda ln: (order[ln.street], -ln.count, ln.label))


def by_size(bets: list[Bet]) -> list[Line]:
    """Toutes lignes confondues, par street et par taille (relances à part) : des échantillons plus gros."""
    out: dict[tuple, Line] = {}
    for b in bets:
        label = "Relances" if b.size == "" else "Mises"
        out.setdefault((b.street, b.size), Line(b.street, label, b.size)).bets.append(b)
    order = {s: k for k, s in enumerate(POSTFLOP)}
    sizes = {"petite (≤ 55 %)": 0, "grosse (56–120 %)": 1, "overbet (> 120 %)": 2, "": 3}
    return sorted(out.values(), key=lambda ln: (order[ln.street], sizes.get(ln.size, 9)))


def required(pct: Optional[float]) -> Optional[float]:
    """Équité qu'il te faut pour payer une mise de pct % du pot (en %)."""
    return None if pct is None else 100.0 * pct / (100.0 + 2 * pct)


@dataclass
class Verdict:
    kind: str     # plus : il bluffe plus qu'il ne faut (paie) ; moins : surtout de la value (folde) ; "" : mixte, peu vu
    text: str


def verdict(line: Line) -> Verdict:
    """Ce que dit une ligne, d'après ses mises montrées : à la river, la part de bluffs face à l'équité qu'il te faut
    pour payer (taille médiane de la ligne) ; avant, la part de mises sans main faite."""
    shown = line.shown
    n = len(shown)
    if n < MIN_SHOWN:
        return Verdict("", "jamais montrée" if not n else f"{n} montrée{'s' if n > 1 else ''} : trop peu pour juger")
    bluffs = sum(b.bluffish for b in shown)
    if line.street == "river":
        pcts = [b.pct for b in line.bets if b.pct is not None]
        need = required(median(pcts)) if pcts else 33.0
        lo, hi = wilson(Ratio(bluffs, n))
        if lo > need:
            return Verdict("plus", f"Paie tes bluff-catchers : au moins {lo:.0f} % de bluffs, il en faut {need:.0f} %")
        if hi < need:
            return Verdict("moins", f"Folde tes bluff-catchers : au plus {hi:.0f} % de bluffs, il en faut {need:.0f} %")
        return Verdict("", f"{bluffs} bluff(s) sur {n} : pas encore tranché ({need:.0f} % de bluffs pour payer)")
    share = bluffs / n
    if share >= 0.6:
        return Verdict("plus", f"Souvent sans main faite ({bluffs}/{n}) : défends plus large, relance")
    if share <= 0.25:
        return Verdict("moins", f"Presque toujours une main faite ({n - bluffs}/{n}) : folde tes mains faibles")
    return Verdict("", f"Mixte : {bluffs} sans main faite sur {n}")


def _median(values: list) -> Optional[float]:
    values = [v for v in values if v is not None]
    return median(values) if len(values) >= 2 else None


def tells(bets: list[Bet]) -> list[str]:
    """Ce qui distingue sa value de ses bluffs dans ces mises montrées (au moins deux de chaque) : taille, temps de
    réflexion, carte tombée, nombre de joueurs."""
    shown = [b for b in bets if b.intent]
    value = [b for b in shown if b.intent in VALUE]
    bluff = [b for b in shown if b.intent in BLUFF]
    if len(value) < 2 or len(bluff) < 2:
        return []
    out = []
    sv, sb = _median([b.pct for b in value]), _median([b.pct for b in bluff])
    if sv is not None and sb is not None and abs(sv - sb) >= TELL_SIZE:
        bigger = "la value" if sv > sb else "les bluffs"
        out.append(f"Taille : plus grosse pour {bigger} ({sv:.0f} % du pot en value, {sb:.0f} % en bluff)")
    tv, tb = _median([b.think for b in value]), _median([b.think for b in bluff])
    if tv is not None and tb is not None and abs(tv - tb) >= 2 and max(tv, tb) >= 1.5 * min(tv, tb):
        out.append(f"Temps : {'plus long' if tb > tv else 'plus court'} pour les bluffs ({tb:.0f} s, contre {tv:.0f} s "
                   "en value)")
    cards = Counter(b.card for b in bluff if b.card and b.street != "flop")
    if cards:
        card, k = cards.most_common(1)[0]
        n_bluff = sum(1 for b in bluff if b.street != "flop")
        n_value = sum(1 for b in value if b.street != "flop")
        in_value = sum(1 for b in value if b.card == card and b.street != "flop")
        if k >= 2 and k >= 0.6 * n_bluff and (not n_value or in_value <= 0.3 * n_value):
            out.append(f"Carte : les bluffs viennent surtout quand {CARD_TEXT.get(card, card)} tombe ({k}/{n_bluff})")
    multi_value = sum(b.multiway for b in value)
    if not any(b.multiway for b in bluff) and multi_value >= max(2, 0.4 * len(value)):
        out.append(f"Joueurs : bluffs seulement dans les pots à deux ({multi_value}/{len(value)} value à plusieurs)")
    return out


def size_tell(lines: list[Line], street: str) -> Optional[str]:
    """À une street, des tailles qui disent sa main : la part de mises sans main faite selon la taille."""
    rows = [(ln.size, ln.shown) for ln in lines if ln.street == street and ln.size and len(ln.shown) >= MIN_SHOWN]
    if len(rows) < 2:
        return None
    shares = [(size, sum(b.bluffish for b in shown), len(shown)) for size, shown in rows]
    hi = max(shares, key=lambda r: r[1] / r[2])
    lo = min(shares, key=lambda r: r[1] / r[2])
    if hi[1] / hi[2] - lo[1] / lo[2] < 0.4:
        return None
    word = "bluffs" if street == "river" else "mises sans main faite"
    return (f"Ses mises {hi[0].split(' (')[0]}s sont souvent des {word} ({hi[1]}/{hi[2]}), "
            f"ses mises {lo[0].split(' (')[0]}s rarement ({lo[1]}/{lo[2]})")


# --- Quand il checke, avant le flop --------------------------------------------------------------------------------

def checks(hands: Iterable[Hand], names: Iterable[str]) -> dict[str, Counter]:
    """Ce que ces joueurs montrent quand ils checkent une street (sans y miser ni relancer ensuite)."""
    names = set(names)
    out: dict[str, Counter] = defaultdict(Counter)
    for h in hands:
        for player in names & set(h.seats):
            if not known(h, player):
                continue
            for street in POSTFLOP:
                acts = [a for a in h.actions if a.street == street and a.player == player and a.kind in VOLUNTARY]
                if acts and acts[0].kind == CHECK and not any(a.kind in (BET, RAISE) for a in acts):
                    out[street][intent_of(h.hole_cards[player], h.board[:BOARD_SIZE[street]], street)[0]] += 1
    return out


PREFLOP_TOKENS = {"open": "Open", "iso": "Iso (relance d'un limp)", "limp": "Limp", "call_open": "Call d'une relance",
                  "3bet": "3bet", "call_3bet": "call du 3bet", "4bet": "4bet", "call_4bet": "call du 4bet",
                  "5bet": "5bet", "check": "Check (pot limpé)"}


def preflop_line(hand: Hand, player: str) -> Optional[str]:
    """Sa ligne avant le flop (« Open, puis call du 3bet »), ou None s'il a foldé avant le flop."""
    raises = limps = 0
    tokens = []
    for a in hand.actions:
        if a.street != "preflop" or a.kind not in VOLUNTARY:
            continue
        if a.player == player:
            if a.kind == FOLD:
                return None
            if a.kind == RAISE:
                tokens.append({0: "iso" if limps else "open", 1: "3bet", 2: "4bet"}.get(raises, "5bet"))
            elif a.kind == CALL:
                tokens.append({0: "limp", 1: "call_open", 2: "call_3bet"}.get(raises, "call_4bet"))
            elif a.kind == CHECK:
                tokens.append("check")
        if a.kind == RAISE:
            raises += 1
        elif a.kind == CALL and raises == 0:
            limps += 1
    if not tokens:
        return None
    first = PREFLOP_TOKENS[tokens[0]]
    rest = [PREFLOP_TOKENS[t].lower() if t != "check" else "check" for t in tokens[1:]]
    return first + (", puis " + ", puis ".join(rest) if rest else "")


def preflop_shown(hands: Iterable[Hand], names: Iterable[str]) -> dict[str, list[tuple[str, Hand]]]:
    """Ses mains montrées, par ligne préflop : {ligne: [(main, coup)]}, les lignes les plus vues d'abord."""
    names = set(names)
    out: dict[str, list] = defaultdict(list)
    for h in hands:
        for player in names & set(h.seats):
            if known(h, player):
                line = preflop_line(h, player)
                if line:
                    out[line].append((combo_notation(h.hole_cards[player]), h))
    return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))


# --- Fréquences aux tables à plusieurs ------------------------------------------------------------------------------

def postflop_read(hand: Hand, player: str) -> dict[str, bool]:
    """Ses fréquences d'après le flop dans une main à plusieurs : 2e et 3e barrels (après sa c-bet), mise quand
    l'agresseur checke le flop, fold face à une mise à la turn et à la river, check-raise au flop."""
    out: dict[str, bool] = {}
    pfa = preflop_aggressor(hand)
    if any(a.player == player and a.kind == FOLD and a.street == "preflop" for a in hand.actions) or len(hand.board) < 3:
        return out
    barrel = {"flop": False, "turn": False}  # il a misé la street d'avant en premier (c-bet, puis 2e barrel)
    for street in POSTFLOP:
        acts = [a for a in hand.actions if a.street == street and a.kind in VOLUNTARY]
        if not acts:
            break
        level = 0
        checked = faced = False
        pfa_checked = False
        for a in acts:
            if a.player == player:
                if level == 0 and not checked:
                    if street == "flop" and pfa == player:
                        barrel["flop"] = a.kind == BET
                    elif street == "turn" and pfa == player and barrel["flop"]:
                        out["cbet_turn"] = a.kind == BET
                        barrel["turn"] = a.kind == BET
                    elif street == "river" and pfa == player and barrel["turn"]:
                        out["cbet_river"] = a.kind == BET
                    if street == "flop" and pfa and pfa != player and pfa_checked:
                        out["stab_flop"] = a.kind == BET
                    checked = a.kind == CHECK
                elif level == 1 and not faced:
                    faced = True
                    if street in ("turn", "river"):
                        out[f"fold_{street}"] = a.kind == FOLD
                    if street == "flop" and checked:
                        out["xr_flop"] = a.kind == RAISE
                if a.kind == FOLD:
                    return out
            elif a.player == pfa and a.kind == CHECK and level == 0:
                pfa_checked = True
            if a.kind in (BET, RAISE):
                level += 1
    return out


def aggression(hand: Hand, player: str) -> Counter:
    """Ses actions après le flop : mises et relances, calls, folds (pour l'agressivité)."""
    return Counter("aggr" if a.kind in (BET, RAISE) else a.kind for a in hand.actions
                   if a.player == player and a.street != "preflop" and a.kind in (BET, RAISE, CALL, FOLD))


def ring_ratios(hands: Iterable[Hand], names: Iterable[str]) -> dict[str, Ratio]:
    """Les fréquences de ces joueurs aux tables à plusieurs, toutes tailles de table ensemble (ring.read et
    postflop_read), plus « afq » (part de mises et relances parmi ses actions d'après le flop) et « fold_bet » (folds
    face à une mise, toutes streets)."""
    names = set(names)
    out: dict[str, Ratio] = defaultdict(Ratio)
    counts: Counter = Counter()
    for h in hands:
        if h.size <= 2 or not h.bb:
            continue
        for player in names & set(h.seats):
            for key, made in list(ring.read(h, player).items()) + list(postflop_read(h, player).items()):
                out[key].add(made)
                if key in ("fold_cbet", "fold_turn", "fold_river"):
                    out["fold_bet"].add(made)
            counts.update(aggression(h, player))
    aggr = counts["aggr"]
    out["afq"] = Ratio(aggr, aggr + counts[CALL] + counts[FOLD])
    return dict(out)


def _r(fact: str, exploit: str) -> R:
    return R(fact, exploit, "")


# Repères d'un régulier solide en 6-max à 100 bb (fourchettes indicatives de tracker), et l'exploit de chaque écart.
RING_DEFS = [
    StatDef("vpip", "VPIP", (20, 28), _r("Joue beaucoup de mains", "isole-le en position et value-bet plus large : il "
                                         "paie avec plus faible"),
            _r("Joue très peu de mains", "vole ses blindes plus souvent et respecte ses relances")),
    StatDef("pfr", "PFR", (16, 23), _r("Relance souvent avant le flop", "3bet-le plus en value et paie plus large en "
                                       "position"),
            _r("Relance rarement avant le flop", "ses relances sont fortes ; ses calls et ses limps sont des mains "
                                                 "moyennes : isole-le")),
    StatDef("limp", "Limp d'entrée", (0, 4), _r("Limpe souvent", "isole ses limps (relance plus gros) avec une range "
                                                 "large"), None),
    StatDef("threebet", "3bet", (6, 11), _r("3bet souvent", "4bet-le plus léger ou paie en position ; ouvre un peu plus "
                                            "serré devant lui"),
            _r("3bet rarement", "ses 3bets sont forts : folde tes opens marginaux")),
    StatDef("fold_3bet", "Fold vs 3bet", (45, 62), _r("Folde trop face au 3bet", "3bet-le plus light (bloqueurs A ou K, "
                                                      "mains jouables)"),
            _r("Défend beaucoup face au 3bet", "3bet-le surtout en value")),
    StatDef("fold_steal", "Fold de ses blindes face au vol", (55, 75),
            _r("Lâche trop ses blindes", "vole plus large contre lui (CO, BTN, SB)"),
            _r("Défend beaucoup ses blindes", "vole moins large, ouvre plus en value")),
    StatDef("threebet_steal", "3bet face au vol", (7, 15), _r("Re-vole beaucoup", "ouvre plus serré devant lui, 4bet "
                                                              "plus léger"),
            _r("Re-vole peu", "vole plus : il ne te punit pas")),
    StatDef("cbet_hu", "C-bet flop (pot à deux)", (50, 72), _r("C-bet presque tout", "float et check-raise plus "
                                                               "souvent"),
            _r("C-bet peu", "ses c-bets sont forts ; attaque quand il checke")),
    StatDef("fold_cbet", "Fold vs c-bet", (35, 52), _r("Folde trop face à la c-bet", "c-bet large et petit contre lui"),
            _r("Paie beaucoup les c-bets", "c-bet moins en bluff, plus en value fine")),
    StatDef("cbet_turn", "2e barrel", (42, 62), _r("Double-barrel beaucoup", "paie plus large à la turn avec tes "
                                                   "bluff-catchers"),
            _r("Abandonne souvent à la turn", "paie le flop plus large (float) et attaque la turn quand il checke")),
    StatDef("fold_turn", "Fold vs mise turn", (35, 52), _r("Folde trop face aux mises turn", "barrel la turn plus "
                                                          "souvent"),
            _r("Paie beaucoup à la turn", "barrel la turn surtout en value")),
    StatDef("fold_river", "Fold vs mise river", (38, 58), _r("Folde trop à la river", "bluffe plus la river"),
            _r("Paie trop à la river", "value-bet fin, ne bluffe pas la river")),
    StatDef("xr_flop", "Check-raise flop", (6, 15), _r("Check-raise souvent", "c-bet plus polarisé, défends plus "
                                                       "contre ses check-raises"),
            _r("Check-raise rarement", "c-bet large : ses check-raises sont forts")),
    StatDef("stab_flop", "Mise quand l'agresseur checke (flop)", (35, 65),
            _r("Attaque dès que l'agresseur checke", "check-raise ou check-call plus souvent quand tu checkes"),
            _r("Attaque peu quand l'agresseur checke", "checke tes mains moyennes sans crainte")),
    StatDef("wtsd", "Abattage (WTSD)", (24, 32), _r("Va beaucoup à l'abattage", "value-bet large, bluffe moins"),
            _r("Abandonne vite avant l'abattage", "bluffe plus, surtout aux dernières streets")),
    StatDef("afq", "Agressivité après le flop", (35, 52),
            _r("Très agressif après le flop", "paie plus large et laisse-le bluffer ; piège avec tes mains fortes"),
            _r("Passif après le flop", "quand il mise, il a une main : folde tes mains moyennes ; value-bet contre lui")),
]
RING_DEF = {d.key: d for d in RING_DEFS}


def ring_findings(ratios: dict[str, Ratio]) -> list[Finding]:
    """Ses écarts nets aux repères d'un régulier 6-max (comme insights.findings), du plus marqué au moins marqué."""
    out = []
    for stat in RING_DEFS:
        r = ratios.get(stat.key)
        dev = deviation(stat, r) if r else None
        if dev is None:
            continue
        direction, strong = dev
        reading = stat.high if direction == "haut" else stat.low
        if reading:
            out.append(Finding(stat, r, direction, strong, reading))
    return sorted(out, key=lambda f: f.score, reverse=True)


def merge_stats(stats: Iterable[PlayerStats], name: str = "groupe") -> PlayerStats:
    """Les stats heads-up de plusieurs joueurs réunies (occasions et actions additionnées)."""
    out = PlayerStats(name)
    for ps in stats:
        out.hands += ps.hands
        for key, r in ps.ratios.items():
            out.ratios[key].hits += r.hits
            out.ratios[key].opps += r.opps
        for street, counts in ps.street_actions.items():
            out.street_actions[street].update(counts)
    return out


def hu_ratios(ps: PlayerStats) -> dict[str, Ratio]:
    """Les fréquences heads-up qui décrivent son style : VPIP, PFR, 3bet, agressivité, folds face aux mises, abattage."""
    counts = Counter()
    for street in POSTFLOP:
        counts.update(ps.street_actions[street])
    aggr = counts[BET] + counts[RAISE]
    fold = Ratio()
    for street in POSTFLOP:
        r = ps.r(f"vs_bet_{street}.fold")
        fold.hits += r.hits
        fold.opps += r.opps
    return {"vpip": ps.r("vpip"), "pfr": ps.r("pfr"), "threebet": ps.r("bb_vs_open.raise"),
            "afq": Ratio(aggr, aggr + counts[CALL] + counts[FOLD]), "fold_bet": fold, "wtsd": ps.r("wtsd")}


# --- Style (récréatifs) ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Style:
    key: str
    name: str      # « Passifs »
    word: str      # « passif »
    title: str     # « Récréatifs passifs : ils paient beaucoup et misent peu »
    plan: tuple    # le plan contre ce style


STYLES = {
    "passif": Style("passif", "Passifs", "passif", "Récréatifs passifs : ils paient beaucoup et misent peu", (
        "Value-bet plus fin et plus gros : ils paient avec des paires faibles.",
        "Ne bluffe pas, surtout à la river : ils ne foldent pas assez.",
        "Quand ils misent ou relancent, ils ont souvent une main : folde tes mains moyennes.",
        "Isole leurs limps et leurs calls avant le flop, en position.")),
    "agressif": Style("agressif", "Agressifs", "agressif", "Récréatifs agressifs : ils misent et relancent beaucoup", (
        "Laisse-les bluffer : paie plus large avec tes bluff-catchers, check-call plutôt que miser.",
        "Piège avec tes mains fortes (check-raise, slowplay) : ils misent pour toi.",
        "Bluffe moins : ils paient ou relancent.",
        "3bet et 4bet plus large en value avant le flop.")),
    "prudent": Style("prudent", "Prudents", "prudent", "Récréatifs prudents : ils jouent peu de mains ou abandonnent vite", (
        "Vole leurs blindes et c-bet souvent : ils foldent trop.",
        "Bluffe plus aux streets suivantes quand ils ne font que payer.",
        "Respecte leurs relances : elles sont fortes.",
        "Ne paie pas leurs grosses mises sans une bonne main.")),
    "autre": Style("autre", "Sans style marqué", "", "Récréatifs sans style marqué (ou trop peu de mains)", (
        "Exploite-les d'après leurs leaks, joueur par joueur.",)),
}
STYLE_ORDER = ("passif", "agressif", "prudent", "autre")


# Seuils des styles : (agressif à partir de, passif sous, VPIP « serré » sous, abattage fréquent au-dessus), en % ; le
# heads-up se joue plus large et plus agressif qu'une table à plusieurs.
STYLE_LIMITS = {"ring": (50, 30, 18, 33), "HU": (60, 35, 50, 40)}


def style_of(ratios: dict[str, Ratio], ring_game: bool) -> tuple[str, list[str]]:
    """Le style d'un joueur d'après ses fréquences (assez d'occasions pour chacune) : agressif (agressivité d'après le
    flop élevée), passif (faible, et il folde peu face aux mises ou va souvent à l'abattage), prudent (il folde
    beaucoup face aux mises, ou joue très peu de mains) ; « autre » sinon. Seuils : STYLE_LIMITS."""
    def pct(key: str, minimum: int) -> Optional[float]:
        r = ratios.get(key)
        return r.pct if r and r.opps >= minimum else None
    afq, fold, vpip, wtsd = pct("afq", 20), pct("fold_bet", 15), pct("vpip", 30), pct("wtsd", 15)
    aggressive, passive, tight, showdown = STYLE_LIMITS["ring" if ring_game else "HU"]
    reasons = []
    if afq is not None and afq >= aggressive:
        return "agressif", [f"agressivité après le flop de {afq:.0f} %"]
    calls = (fold is not None and fold < 40) or (wtsd is not None and wtsd > showdown)
    if afq is not None and afq < passive and calls:
        reasons.append(f"agressivité après le flop de {afq:.0f} %")
        reasons.append(f"folde {fold:.0f} % face aux mises" if fold is not None and fold < 40
                       else f"va à l'abattage {wtsd:.0f} % du temps")
        return "passif", reasons
    if (fold is not None and fold >= 55) or (vpip is not None and vpip < tight):
        reasons.append(f"folde {fold:.0f} % face aux mises" if fold is not None and fold >= 55
                       else f"joue {vpip:.0f} % de ses mains")
        return "prudent", reasons
    if afq is not None and afq < passive:
        return "passif", [f"agressivité après le flop de {afq:.0f} %"]
    return "autre", ["pas assez de mains" if afq is None and fold is None else "rien de très marqué"]


# --- L'étude d'un joueur (ou d'un groupe) -----------------------------------------------------------------------------

@dataclass
class Study:
    names: list
    table_format: str         # « HU » ou « ring »
    hands: int
    ratios: dict              # fréquences de style (vpip, pfr, threebet, afq, fold_bet, wtsd)
    leaks: list               # insights.Finding, du plus marqué au moins marqué
    style: tuple              # (clé de STYLES, raisons)
    bets: list
    lines: list
    sizes: list
    checks: dict
    preflop: dict

    @property
    def ring_game(self) -> bool:
        return self.table_format != "HU"

    @property
    def shown(self) -> int:
        return sum(1 for b in self.bets if b.intent)

    def top_leaks(self, n: int = LEAKS_SHOWN) -> list[Finding]:
        """Les leaks les plus nets : les solides d'abord."""
        return sorted(self.leaks, key=lambda f: (not f.strong, -f.score))[:n]

    def street_tells(self) -> dict[str, list[str]]:
        """Ce qui distingue sa value de ses bluffs, street par street (toutes lignes ensemble), et ce que disent ses
        tailles."""
        out = {}
        for street in POSTFLOP:
            found = tells([b for b in self.bets if b.street == street])
            by_size = size_tell(self.sizes, street)
            if by_size:
                found.insert(0, by_size)
            if found:
                out[street] = found
        return out

    def key_lines(self, n: int = 2) -> list[tuple[Line, Verdict]]:
        """Les lignes qui disent le plus clairement value ou bluff (assez montrées), les plus vues d'abord."""
        found = [(ln, verdict(ln)) for ln in self.lines if len(ln.shown) >= MIN_SHOWN]
        found = [(ln, v) for ln, v in found if v.kind]
        return sorted(found, key=lambda lv: -len(lv[0].shown))[:n]


def study(hands: list[Hand], names: Iterable[str], table_format: str,
          stats: Optional[PlayerStats] = None) -> Study:
    """L'étude de ces joueurs (un adversaire, ou un groupe) dans leurs mains du format (« HU » : stats, ses stats
    heads-up, déjà calculées ou réunies ; « ring » : tables de 3 à 9 joueurs)."""
    names = list(names)
    mine = [h for h in hands if set(names) & set(h.seats)]
    if table_format == "HU":
        ps = stats if stats is not None else PlayerStats(names[0])
        ratios, leaks = hu_ratios(ps), findings(ps)
    else:
        ratios = ring_ratios(mine, names)
        leaks = ring_findings(ratios)
    bets = player_bets(mine, names)
    return Study(names, table_format, len(mine), ratios, leaks, style_of(ratios, table_format != "HU"), bets,
                 lines_of(bets), by_size(bets), checks(mine, names), preflop_shown(mine, names))


def finding_text(f: Finding) -> tuple[str, str, str]:
    """(constat, chiffres, exploit) d'un leak : « Folde trop face à la c-bet », « 62 % sur 45 (repère 35–52 %) »,
    « c-bet large et petit contre lui »."""
    lo, hi = f.stat.ref
    numbers = f"{f.stat.label} {f.ratio.pct:.0f} % sur {f.ratio.opps} (repère {lo:.0f}–{hi:.0f} %)"
    return f.reading.fact, numbers, f.reading.exploit

