"""Ce que rapporte chaque main de départ : en tout, par position, puis à chaque décision préflop comparée au fold
et à la théorie.

Pour chaque main où tes cartes sont connues : son résultat (en bb, et en EV all-in quand un tapis payé avant la
river a été montré, pour ôter la chance), ta position, et chacune de tes décisions préflop :

  open     tu parles le premier (personne n'est entré) : relancer, limper ou folder ;
  vs_limp  quelqu'un a limpé avant toi ;
  vs_open  face à une ouverture : payer, relancer (3bet) ou folder ;
  vs_3bet  ton open a été relancé ;
  vs_4bet  ton 3bet a été relancé.

Le fold d'une décision coûte ce que tu as déjà mis au pot (la SB 0,5 bb, la BB 1 bb, ton open face au 3bet…) :
jouer la main est rentable si elle rapporte en moyenne plus que ce coût (mieux que -100 bb/100 pour défendre la
BB, que -250 bb/100 pour payer un 3bet après un open à 2,5 bb). Le résultat d'une décision est celui de toute la
main qui suit.

Théorie : en heads-up, la fréquence de la solution préflop (theory/preflop.py) ; à une table à plusieurs, celle de
tes charts (ring_ranges), retrouvée à partir des ranges de leurs lignes.

D'où vient la perte : la suite du coup après la décision (Outcome) découpe le résultat — sans flop (ils foldent, tu
foldes ensuite, tapis préflop), puis par type de pot au flop (limpé, SRP, 3bet, 4bet, à plusieurs) ; dans chaque pot,
ta main au flop, ta position et la fin du coup (fold, abattage). Les mains déjà passées au solveur (theory/review.py)
y ajoutent l'EV perdue après le flop.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .cards import combo_notation
from .models import CALL, CHECK, FOLD, RAISE, VOLUNTARY, Hand
from .stats import allin_ev
from .theory import handclass

SITUATIONS = (("open", "Premier à parler"), ("vs_limp", "Après un limp"), ("vs_open", "Face à une ouverture"),
              ("vs_3bet", "Face au 3bet"), ("vs_4bet", "Face au 4bet"))
ACTIONS = ("raise", "allin", "call", "check", "fold")
ALLIN_SHARE = 0.6  # une relance qui engage plus de 60 % du tapis effectif compte comme un tapis
HU_NODES = {"open": "sb_open", "vs_open": "bb_vs_open", "vs_3bet": "sb_vs_3bet", "vs_4bet": "bb_vs_4bet"}
GROUPS = {"raise": 0, "allin": 0, "call": 1, "check": 1, "fold": 2}  # agressif, passif, fold
NEXT = {"open": "vs_3bet", "vs_open": "vs_4bet"}  # la décision préflop suivante, quand on relance
POTS = (("won", "Sans flop : ils foldent"), ("fold", "Sans flop : tu foldes ensuite"), ("allin", "Tapis préflop"),
        ("limp", "Pot limpé"), ("srp", "SRP"), ("3bet", "Pot 3bet"), ("4bet", "Pot 4bet+"), ("multi", "Pot à plusieurs"))
FLOP_POTS = ("limp", "srp", "3bet", "4bet")  # pots à deux au flop : le solveur peut les résoudre
ENDS = (("win_f", "Il folde au flop"), ("win_t", "Il folde à la turn"), ("win_r", "Il folde à la river"),
        ("fold_f", "Tu foldes au flop"), ("fold_t", "Tu foldes à la turn"), ("fold_r", "Tu foldes à la river"),
        ("sd_win", "Abattage gagné"), ("sd_lose", "Abattage perdu (ou partagé)"))
FLOPS = (("strong", "Deux paires ou mieux, overpair"), ("toppair", "Top pair"), ("pair", "Paire moyenne ou faible"),
         ("draw", "Tirage (couleur, quinte ouverte)"), ("air", "Rien (hauteur, petit tirage)"))
REVIEW = 10  # coups à revoir par pot au flop (les plus chers)
REVIEW_POTS = FLOP_POTS + ("multi",)


@dataclass
class Outcome:
    """La suite du coup : comment il s'est fini, et où."""
    pot: str  # voir POTS
    end: Optional[str] = None  # voir ENDS (le coup est allé au flop, ou tapis préflop)
    flop: Optional[str] = None  # ta main au flop, voir FLOPS
    ip: Optional[bool] = None  # en position au flop (pot à deux)


@dataclass
class Decision:
    situation: str
    action: str
    fold_bb: Optional[float]  # résultat d'un fold à ce moment (-ce que tu as déjà mis) ; None : pas de fold possible
    theory: Optional[dict[str, float]] = None  # fréquence de chaque action selon la théorie, pour ta main


@dataclass
class Played:
    hand: Hand
    combo: str  # classe de la main : AKs, 72o…
    position: str
    net_bb: float
    ev_bb: float  # résultat EV all-in (le réel sans tapis montré)
    opponent: Optional[str] = None  # en heads-up, l'adversaire
    decisions: list[Decision] = field(default_factory=list)
    outcome: Optional[Outcome] = None
    solver_loss: Optional[float] = None  # EV perdue après le flop selon le solveur (main analysée)

    @property
    def hand_id(self) -> str:
        return self.hand.hand_id if self.hand is not None else ""


def _action(a, effective: float) -> Optional[str]:
    if a.kind == FOLD:
        return "fold"
    if a.kind == CALL:
        return "call"
    if a.kind == CHECK:
        return "check"
    if a.kind == RAISE:
        return "allin" if a.all_in or (effective and a.to >= ALLIN_SHARE * effective) else "raise"
    return None


def read(hand: Hand, hero: str, theory: Optional["Theory"] = None) -> Optional[Played]:
    """Ta main, son résultat et tes décisions préflop ; None sans tes cartes ou sans blindes connues."""
    cards = hand.hole_cards.get(hero, [])
    if len(cards) != 2 or hero not in hand.seats or not hand.bb or not hand.big_blind:
        return None
    bb = hand.bb
    ev = allin_ev(hand) if len(hand.seats) == 2 else None
    played = Played(hand, combo_notation(cards), hand.position(hero), round(hand.net(hero) / bb, 2),
                    round((ev[hero] if ev else hand.net(hero)) / bb, 2),
                    hand.opponent_of(hero) if len(hand.seats) == 2 else None)
    effective = hand.effective_stack()
    put = defaultdict(float)
    raises, limpers, raisers = 0, 0, []
    for a in hand.actions:
        if a.street != "preflop":
            continue
        if a.kind not in VOLUNTARY:  # blindes et antes
            put[a.player] += a.amount
            continue
        if a.player == hero:
            situation = _situation(raises, limpers, raisers, hero)
            action = _action(a, effective)
            if situation and action:
                fold = None if situation == "vs_limp" and hero == hand.big_blind and raises == 0 else \
                    round(-put[hero] / bb, 2) + 0.0
                played.decisions.append(Decision(situation, action, fold, theory.strategy(
                    hand, hero, situation, raisers, played.combo) if theory else None))
        if a.kind in (RAISE, CALL):
            put[a.player] = a.to  # ce qu'il a mis dans la street (blinde comprise)
        if a.kind == RAISE:
            raises += 1
            raisers.append(a.player)
        elif a.kind == CALL and raises == 0:
            limpers += 1
    played.outcome = outcome(hand, hero, raises, cards)
    return played


POSTFLOP_ORDER = ("SB", "BB", "UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN")  # le bouton parle le dernier


def outcome(hand: Hand, hero: str, raises: int, cards: list[str]) -> Outcome:
    """Comment le coup s'est fini pour toi : sans flop, ou dans quel pot, avec quelle main, et comment."""
    pre = [a for a in hand.actions if a.street == "preflop"]
    folded = {a.player for a in pre if a.kind == FOLD}
    if hero in folded:
        return Outcome("fold")
    alive = {a.player for a in pre} - folded
    if alive == {hero}:
        return Outcome("won")
    post = [a for a in hand.actions if a.street != "preflop" and a.kind in VOLUNTARY]
    if not post and any(a.all_in for a in pre if a.player in alive):
        pot = "allin"
    elif len(alive) > 2:
        pot = "multi"
    else:
        pot = "limp" if raises == 0 else "srp" if raises == 1 else "3bet" if raises == 2 else "4bet"
    ip = None
    if len(alive) == 2 and pot != "allin":  # en position : tu parles après lui au flop
        other = next(p for p in alive if p != hero)
        order = lambda p: POSTFLOP_ORDER.index(hand.position(p)) if hand.position(p) in POSTFLOP_ORDER else -1  # noqa: E731
        ip = order(hero) > order(other)
    end, left = None, set(alive)
    for a in post:
        if a.kind != FOLD:
            continue
        if a.player == hero:
            end = "fold_" + a.street[0]
            break
        left.discard(a.player)
        if left == {hero}:
            end = "win_" + a.street[0]
            break
    if end is None:
        end = "sd_win" if hand.net(hero) > 0 else "sd_lose"
    flop = _flop_class(cards, hand.board[:3]) if pot != "allin" and len(hand.board) >= 3 else None
    return Outcome(pot, end, flop, ip)


def _flop_class(cards: list[str], board: list[str]) -> Optional[str]:
    try:
        made, draws = handclass.classify("".join(cards), tuple(board))
    except (KeyError, IndexError, ValueError):
        return None
    if made <= handclass.MADE_INDEX["overpair"]:
        return "strong"
    if made == handclass.MADE_INDEX["toppair"]:
        return "toppair"
    if made <= handclass.MADE_INDEX["weakpair"]:
        return "pair"
    if draws & (handclass.DRAW_BIT["fd"] | handclass.DRAW_BIT["oesd"]):
        return "draw"
    return "air"


def _situation(raises: int, limpers: int, raisers: list[str], hero: str) -> Optional[str]:
    if raises == 0:
        return "vs_limp" if limpers else "open"
    if raises == 1 and raisers[0] != hero:
        return "vs_open"
    if raises == 2 and raisers[0] == hero:
        return "vs_3bet"
    if raises == 3 and raisers[1] == hero:
        return "vs_4bet"
    return None  # squeeze, cold 4bet… : hors des situations suivies


class Theory:
    """La fréquence de chaque action selon la théorie, pour une main à une décision préflop."""

    def __init__(self, heads_up_solution=None, ring_lines: Optional[dict[str, dict[str, dict[str, float]]]] = None):
        self.solution = heads_up_solution
        self.lines = ring_lines or {}  # format -> clé de ligne -> {position: range}

    def strategy(self, hand: Hand, hero: str, situation: str, raisers: list[str], combo: str
                 ) -> Optional[dict[str, float]]:
        if len(hand.seats) == 2:
            node = self.solution.nodes.get(HU_NODES.get(situation, "")) if self.solution else None
            if node is None or (situation != "open" and raisers and raisers[0] != hand.button):
                return None  # l'arbre de référence part de l'open du bouton
            return node.strategy(combo)
        lines = self.lines.get(hand.table_format)
        if not lines:
            return None
        me = hand.position(hero)
        if situation == "open":
            return _chart_open(lines, me, combo)
        if situation == "vs_open":
            return _chart_vs_open(lines, hand.position(raisers[0]), me, combo)
        if situation == "vs_3bet":
            return _chart_vs_3bet(lines, me, hand.position(raisers[1]), combo)
        return None  # face au 4bet : les charts ne donnent que les calls (pas les tapis)


def _range(lines: dict, key: str, position: str) -> Optional[dict[str, float]]:
    entry = lines.get(key)
    return entry.get(position) if entry else None


def _open_range(lines: dict, opener: str) -> Optional[dict[str, float]]:
    """L'ouverture d'une position : sa range dans un pot simple qu'elle a ouvert (le call ne la change pas)."""
    for key, ranges in lines.items():
        steps = key.split()
        if len(steps) == 2 and steps[0] == f"{opener}:raise" and steps[1].endswith(":call"):
            return ranges.get(opener)
    return None


def _chart_open(lines: dict, me: str, combo: str) -> Optional[dict[str, float]]:
    rng = _open_range(lines, me)
    if rng is None:
        return None
    w = rng.get(combo, 0.0)
    return {"raise": w, "fold": 1 - w}


def _chart_vs_open(lines: dict, opener: str, me: str, combo: str) -> Optional[dict[str, float]]:
    call = _range(lines, f"{opener}:raise {me}:call", me)
    raise_ = _range(lines, f"{opener}:raise {me}:raise {opener}:call", me)
    if call is None and raise_ is None:
        return None
    c, r = (call or {}).get(combo, 0.0), (raise_ or {}).get(combo, 0.0)
    return {"raise": r, "call": c, "fold": max(0.0, 1 - r - c)}


def _chart_vs_3bet(lines: dict, me: str, threebettor: str, combo: str) -> Optional[dict[str, float]]:
    opened = (_open_range(lines, me) or {}).get(combo, 0.0)
    call = _range(lines, f"{me}:raise {threebettor}:raise {me}:call", me)
    four = _range(lines, f"{me}:raise {threebettor}:raise {me}:raise {threebettor}:call", me)
    if not opened or (call is None and four is None):
        return None
    c = min(1.0, (call or {}).get(combo, 0.0) / opened)  # dans la ligne, la range est celle qui ouvre ET paie
    r = min(1.0, (four or {}).get(combo, 0.0) / opened)
    return {"raise": r, "call": c, "fold": max(0.0, 1 - r - c)}


def ring_lines(formats: tuple[str, ...] = ("6-max", "3-max")) -> dict[str, dict]:
    """Les ranges de tes charts (ring_ranges), ligne par ligne, pour chaque format présent."""
    from .theory import ring_ranges
    out = {}
    for table_format in formats:
        data = ring_ranges.solution(table_format)
        if data:
            out[table_format] = {key: {pos: ring_ranges.parse_range(text) for pos, text in entry["ranges"].items()}
                                 for key, entry in data["lines"].items() if isinstance(entry.get("ranges"), dict)}
    return out


def collect(hands: list[Hand], hero: str, theory: Optional[Theory] = None,
            losses: Optional[dict[str, float]] = None) -> list[Played]:
    """Tes mains ; losses : EV perdue après le flop des mains déjà passées au solveur (main -> bb)."""
    out = []
    for hand in hands:
        played = read(hand, hero, theory)
        if played is not None:
            played.solver_loss = (losses or {}).get(hand.hand_id)
            out.append(played)
    return out


def _branches(o: Optional[Outcome]) -> list[str]:
    """Les branches d'une suite de coup : le pot, puis dans ce pot la fin, la main au flop et la position."""
    if o is None:
        return []
    names = ["p:" + o.pot]
    if o.end:
        names.append(f"e:{o.pot}:{o.end}")
    if o.flop:
        names.append(f"f:{o.pot}:{o.flop}")
    if o.ip is not None:
        names.append(f"i:{o.pot}:{'ip' if o.ip else 'oop'}")
    return names


def _group_freqs(theory: dict[str, float]) -> list[float]:
    out = [0.0, 0.0, 0.0]
    for action, freq in theory.items():
        if action in GROUPS:
            out[GROUPS[action]] += freq
    return out


def _freq(d: Decision) -> Optional[float]:
    """Fréquence théorique de l'action jouée (un tapis compte avec les relances)."""
    if d.theory is None:
        return None
    if d.action == "allin":
        return d.theory.get("allin", 0.0) + d.theory.get("raise", 0.0)
    return d.theory.get(d.action, 0.0)


def _kind(p: Played, kinds: dict[str, str]) -> str:
    return kinds.get(p.opponent or "", "") if p.opponent else ""


def aggregate(plays: list[Played], kinds: Optional[dict[str, str]] = None) -> dict:
    """Sommes par (position, situation, action, type d'adversaire, main) pour la page : l'application y choisit ses
    filtres et calcule moyennes et intervalles. Situation « all » : chaque main une fois (son résultat).

    cells : clé -> main -> [n, Σréel, Σréel², ΣEV, ΣEV², Σfold, Σthéorie, n théorie]. Le détail d'une main (d'où
    vient son résultat) se calcule à la demande : detail()."""
    kinds = kinds or {}
    cells: dict[str, dict[str, list[float]]] = defaultdict(dict)

    def add(key: str, combo: str, net: float, ev: float, fold: Optional[float], freq: Optional[float]) -> None:
        cell = cells[key].setdefault(combo, [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0])
        cell[0] += 1
        cell[1] += net
        cell[2] += net * net
        cell[3] += ev
        cell[4] += ev * ev
        cell[5] += fold or 0.0
        if freq is not None:
            cell[6] += freq
            cell[7] += 1

    for p in plays:
        kind = _kind(p, kinds)
        add(f"{p.position}|all|all|{kind}", p.combo, p.net_bb, p.ev_bb, None, None)
        for d in p.decisions:
            add(f"{p.position}|{d.situation}|{d.action}|{kind}", p.combo, p.net_bb, p.ev_bb, d.fold_bb, _freq(d))
    return {"cells": {k: {c: [round(x, 3) for x in v] for c, v in cell.items()} for k, cell in cells.items()},
            "positions": sorted({p.position for p in plays}, key=_position_order),
            "hands": len(plays)}


def _record(p: Played) -> list:
    """Un coup à revoir : [main, partie, date, EV, réel, pot, fin, main au flop, EV perdue selon le solveur]."""
    o = p.outcome or Outcome("fold")
    date = p.hand.date.strftime("%d/%m/%y") if p.hand is not None and p.hand.date else ""
    return [p.combo, p.hand_id, date, round(p.ev_bb, 2), round(p.net_bb, 2), o.pot, o.end, o.flop,
            None if p.solver_loss is None else round(p.solver_loss, 2)]


def _mix_add(m: list[float], d: Decision) -> None:
    """[n, n théorie, agressif, passif, fold, Σthéorie agressif, passif, fold]"""
    m[0] += 1
    m[2 + GROUPS[d.action]] += 1
    if d.theory is not None:
        m[1] += 1
        for k, freq in enumerate(_group_freqs(d.theory)):
            m[5 + k] += freq


def is_combo(name: str) -> bool:
    return len(name) in (2, 3) and all(c in "AKQJT98765432" for c in name[:2]) and name[2:] in ("", "s", "o")


def detail(plays: list[Played], kinds: Optional[dict[str, str]] = None, *, name: str, kind: str = "",
           position: str = "", situation: str = "all", actions: Optional[tuple[str, ...]] = None) -> dict:
    """D'où vient le résultat d'une main (ou d'une famille), toutes ses mains ou à une décision (actions : celles
    retenues, hors fold par défaut) : ses suites de coup (_branches), tes choix face à la théorie à cette décision et à
    la suivante, et les coups allés au flop les plus chers (par pot, en EV et en réel).

    branches : branche -> [n, Σréel, Σréel², ΣEV, ΣEV², Σfold, ΣEV perdue (solveur), n analysées]"""
    kinds = kinds or {}
    actions = actions or ("raise", "allin", "call", "check")
    match = (lambda c: c == name) if is_combo(name) else (lambda c: family(c) == name)  # noqa: E731
    branches: dict[str, list[float]] = {}
    here, after = [0.0] * 8, [0.0] * 8
    by_pot: dict[str, list[Played]] = defaultdict(list)
    n = 0
    for p in plays:
        if not match(p.combo) or (position and p.position != position) or (kind and _kind(p, kinds) != kind):
            continue
        if situation == "all":
            fold = None
        else:
            chosen = next((d for d in p.decisions if d.situation == situation), None)
            if chosen is None:
                continue
            _mix_add(here, chosen)
            if chosen.action not in actions:
                continue
            for d in p.decisions:
                if d.situation == NEXT.get(situation):
                    _mix_add(after, d)
            fold = chosen.fold_bb
        n += 1
        for branch in _branches(p.outcome):
            cell = branches.setdefault(branch, [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0])
            cell[0] += 1
            cell[1] += p.net_bb
            cell[2] += p.net_bb ** 2
            cell[3] += p.ev_bb
            cell[4] += p.ev_bb ** 2
            cell[5] += fold or 0.0
            if p.solver_loss is not None:
                cell[6] += p.solver_loss
                cell[7] += 1
        if p.outcome is not None and p.outcome.pot in REVIEW_POTS:
            by_pot[p.outcome.pot].append(p)
    review = []
    for pot_plays in by_pot.values():  # les plus chers de chaque pot, en EV comme en réel
        worst = {id(p): p for key in ("ev_bb", "net_bb")
                 for p in sorted(pot_plays, key=lambda q: getattr(q, key))[:REVIEW]}
        review += [_record(p) for p in worst.values()]
    return {"n": n, "branches": {b: [round(x, 3) for x in c] for b, c in branches.items()},
            "mix": {"here": here if here[0] else None, "next": after if after[0] else None},
            "review": sorted(review, key=lambda r: r[3])}


POSITION_ORDER = ("UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN", "SB", "BB")


def _position_order(pos: str) -> int:
    return POSITION_ORDER.index(pos) if pos in POSITION_ORDER else len(POSITION_ORDER)


# --- Leaks : les mains jouées qui perdent nettement plus que le fold --------------------------------------------

WORDS = {"open": {"raise": "open", "allin": "tapis", "call": "limp"},
         "vs_limp": {"raise": "relance", "allin": "tapis", "check": "check", "call": "limp"},
         "vs_open": {"raise": "3bet", "allin": "tapis", "call": "call"},
         "vs_3bet": {"raise": "4bet", "allin": "tapis", "call": "call"},
         "vs_4bet": {"allin": "tapis", "call": "call", "raise": "5bet"}}
SITUATION_WORDS = {"open": "en premier", "vs_limp": "après un limp", "vs_open": "face à l'open",
                   "vs_3bet": "face au 3bet", "vs_4bet": "face au 4bet"}
MIN_LEAK = 15  # fois minimum pour un leak


def family(combo: str) -> str:
    """La famille d'une main (comme dans la page) : plus de mains par groupe, des verdicts plus sûrs."""
    ranks = "AKQJT98765432"
    hi, lo = combo[0], combo[1]
    suited = combo.endswith("s")
    if len(combo) == 2:
        return ("Paires hautes (TT+)" if ranks.index(hi) <= ranks.index("T") else
                "Paires moyennes (66-99)" if ranks.index(hi) <= ranks.index("6") else "Petites paires (22-55)")
    if hi == "A":
        return "As assortis" if suited else "As dépareillés"
    if ranks.index(lo) <= ranks.index("T"):
        return "Broadways assortis" if suited else "Broadways dépareillés"
    if suited and ranks.index(lo) - ranks.index(hi) <= 2:
        return "Connecteurs assortis"
    return "Autres assorties" if suited else "Autres dépareillées"


POT_SOURCES = {"won": "des coups sans flop où ils foldent", "fold": "de tes folds ensuite préflop",
               "allin": "des tapis préflop", "limp": "des pots limpés", "srp": "du SRP", "3bet": "des pots 3bet",
               "4bet": "des pots 4bet", "multi": "des pots à plusieurs"}
POT_WORDS = {"won": "sans flop quand ils foldent", "fold": "quand tu foldes ensuite préflop",
             "allin": "dans les tapis préflop", "limp": "dans les pots limpés", "srp": "en SRP",
             "3bet": "dans les pots 3bet", "4bet": "dans les pots 4bet", "multi": "dans les pots à plusieurs"}


@dataclass
class Source:
    """Une suite du coup (un type de pot) et sa part dans l'écart au fold."""
    pot: str
    n: int
    mean: float  # bb par main (EV all-in)
    contribution: float  # bb par décision (tous les coups de la main), par rapport au fold
    solver_n: int = 0  # mains analysées par le solveur
    solver_loss: float = 0.0  # EV perdue moyenne après le flop sur ces mains


@dataclass
class Loser:
    """Une main (ou une famille) jouée d'une certaine façon qui rapporte nettement moins que le fold."""
    name: str
    situation: str
    position: str
    action: str
    n: int
    mean: float  # bb par main (EV all-in)
    fold: float  # bb du fold à ce moment
    half_width: float  # demi-intervalle à 95 %
    theory: Optional[float]  # fréquence théorique de cette action avec ces mains
    sources: list[Source] = field(default_factory=list)  # d'où vient l'écart, de la pire suite à la meilleure
    worst: list[tuple[str, float, Optional[float]]] = field(default_factory=list)  # coups allés au flop les plus chers
    # (main, EV, EV perdue selon le solveur)

    @property
    def gap(self) -> float:
        return self.mean - self.fold

    @property
    def label(self) -> str:
        word = WORDS.get(self.situation, {}).get(self.action, self.action)
        return f"{self.name} : {word} {SITUATION_WORDS.get(self.situation, '')} ({self.position})"


def sources(rows: list[tuple[Played, Decision]]) -> list[Source]:
    """L'écart au fold découpé par suite du coup : Σ(résultat - fold) de chaque type de pot / toutes les décisions."""
    by_pot: dict[str, list[tuple[Played, Decision]]] = defaultdict(list)
    for p, d in rows:
        by_pot[p.outcome.pot if p.outcome else "fold"].append((p, d))
    out = []
    for pot, items in by_pot.items():
        analysed = [p.solver_loss for p, _ in items if p.solver_loss is not None]
        out.append(Source(pot, len(items), sum(p.ev_bb for p, _ in items) / len(items),
                          sum(p.ev_bb - (d.fold_bb or 0.0) for p, d in items) / len(rows),
                          len(analysed), sum(analysed) / len(analysed) if analysed else 0.0))
    return sorted(out, key=lambda x: x.contribution)


def losers(plays: list[Played], min_n: int = MIN_LEAK) -> list[Loser]:
    """Mains et familles jouées (hors fold) qui perdent plus que le fold, le hasard mis à part (intervalle à 95 %),
    des plus coûteuses aux moins coûteuses ; une main déjà dans une famille signalée n'est pas répétée."""
    groups: dict[tuple, list[tuple[Played, Decision]]] = defaultdict(list)
    for p in plays:
        for d in p.decisions:
            if d.action == "fold" or d.fold_bb is None:
                continue
            for name in (p.combo, family(p.combo)):
                groups[(name, d.situation, p.position, d.action)].append((p, d))
    out = []
    for (name, situation, position, action), rows in groups.items():
        n = len(rows)
        if n < min_n:
            continue
        values = [p.ev_bb for p, _ in rows]
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        half = 1.96 * (var / n) ** 0.5
        fold = sum(d.fold_bb for _, d in rows) / n
        freqs = [f for f in (_freq(d) for _, d in rows) if f is not None]
        if mean - fold + half < 0:
            flop = [r for r in rows if r[0].outcome is not None and r[0].outcome.pot in REVIEW_POTS]
            worst = sorted(flop, key=lambda r: r[0].ev_bb)[:3]  # à revoir : les coups allés au flop
            out.append(Loser(name, situation, position, action, n, mean, fold, half,
                             sum(freqs) / len(freqs) if freqs else None, sources(rows),
                             [(p.hand_id, p.ev_bb, p.solver_loss) for p, _ in worst]))
    out.sort(key=lambda x: x.gap * x.n)
    flagged = {(x.situation, x.position, x.action, x.name) for x in out}
    return [x for x in out if len(x.name) > 3 or (x.situation, x.position, x.action, family(x.name)) not in flagged]
