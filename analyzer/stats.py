"""Calcul des statistiques Heads-Up par joueur.

Chaque main est « lue » action par action : à chaque décision on enregistre la
situation (ex. `bb_vs_open`) et l'option choisie. Une stat est donc toujours un
ratio « nombre de fois où il l'a fait / nombre de fois où il pouvait le faire ».
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from .cards import combo_notation, equity
from .models import AGGRESSIVE, BET, CALL, CHECK, FOLD, POSTFLOP, RAISE, VOLUNTARY, Hand

OUTCOMES = {FOLD: "fold", CALL: "call", RAISE: "raise", CHECK: "check", BET: "bet"}
PF_LEVEL_NAMES = {1: "3bet", 2: "4bet"}


@dataclass
class Ratio:
    hits: int = 0
    opps: int = 0

    def add(self, made: bool) -> None:
        self.opps += 1
        self.hits += bool(made)

    @property
    def pct(self) -> Optional[float]:
        return 100.0 * self.hits / self.opps if self.opps else None


@dataclass
class ShowdownEntry:
    hand: Hand
    player: str
    cards: list[str]
    combo: str
    position: str  # "BTN" ou "BB"
    preflop_line: str
    made_hand: str
    net_bb: float


@dataclass
class PlayerStats:
    name: str
    hands: int = 0
    hands_by_pos: Counter = field(default_factory=Counter)
    ratios: defaultdict = field(default_factory=lambda: defaultdict(Ratio))
    sizes: defaultdict = field(default_factory=lambda: defaultdict(list))
    times: defaultdict = field(default_factory=lambda: defaultdict(list))
    street_actions: defaultdict = field(default_factory=lambda: defaultdict(Counter))
    preflop_lines: Counter = field(default_factory=Counter)
    net: float = 0.0
    net_bb: float = 0.0
    net_bb_by_pos: Counter = field(default_factory=Counter)
    net_bb_showdown: float = 0.0
    net_bb_no_showdown: float = 0.0
    ev_adjust_bb: float = 0.0
    allin_hands: int = 0
    curve: list = field(default_factory=list)  # (cumul réel bb, cumul EV bb, cumul SD, cumul non-SD)
    showdowns: list = field(default_factory=list)
    eff_stacks_bb: list = field(default_factory=list)

    def r(self, key: str) -> Ratio:
        return self.ratios[key] if key in self.ratios else Ratio()

    def pct(self, key: str) -> Optional[float]:
        return self.r(key).pct

    def aggression(self, street: Optional[str] = None) -> tuple[Optional[float], Optional[float]]:
        """(AF, AFq) postflop, globalement ou pour une street."""
        streets = [street] if street else list(POSTFLOP)
        c = Counter()
        for s in streets:
            c.update(self.street_actions[s])
        aggr = c[BET] + c[RAISE]
        af = aggr / c[CALL] if c[CALL] else None
        denom = aggr + c[CALL] + c[FOLD]
        afq = 100.0 * aggr / denom if denom else None
        return af, afq

    def median_time(self, key: str) -> Optional[float]:
        values = self.times.get(key)
        return median(values) if values else None

    @property
    def bb_per_100(self) -> Optional[float]:
        return 100.0 * self.net_bb / self.hands if self.hands else None


class HandReader:
    """Lit une main HU et produit, pour chaque joueur, ses situations et décisions. HandReader.of(main) : la lecture
    faite une fois par main (gardée sur la main), à préférer quand on ne la modifie pas."""

    @classmethod
    def of(cls, hand: Hand) -> "HandReader":
        reader = hand.__dict__.get("_reader")
        if reader is None:
            reader = hand.__dict__["_reader"] = cls(hand)
        return reader

    def __init__(self, hand: Hand):
        self.h = hand
        self.sb = hand.button
        self.bb = hand.big_blind
        self.events: dict[str, list[tuple[str, bool]]] = defaultdict(list)
        self.sizes: dict[str, list[tuple[str, float]]] = defaultdict(list)
        self.tokens: dict[str, list[str]] = defaultdict(list)
        self.pfa: Optional[str] = None
        self.pf_raises = 0
        self._read_preflop()
        self._read_postflop()

    # -- utilitaires --------------------------------------------------------
    def _record(self, player: str, situation: str, choice: str, options: tuple[str, ...]) -> None:
        for option in options:
            self.events[player].append((f"{situation}.{option}", choice == option))

    def _flag(self, player: str, key: str, made: bool) -> None:
        self.events[player].append((key, made))

    def pos(self, player: str) -> str:
        return "sb" if player == self.sb else "bb"

    # -- préflop -------------------------------------------------------------
    def _read_preflop(self) -> None:
        h, sb = self.h, self.sb
        raises, last_to, last_level = 0, h.bb, None
        acted: dict[str, list[str]] = {p: [] for p in h.seats}
        for a in h.street_actions("preflop"):
            p, kind = a.player, a.kind
            pos = self.pos(p)
            choice = OUTCOMES[kind]
            first = not acted[p]
            # Situation vue par le joueur
            if raises == 0:
                situation = "sb_first" if p == sb and first else "bb_vs_limp"
                options = ("fold", "call", "raise") if situation == "sb_first" else ("check", "raise")
            else:
                if raises == 1:
                    situation = "bb_vs_open" if p != sb else "sb_vs_iso"
                elif raises == 2:
                    situation = "sb_vs_3bet" if p == sb else "bb_vs_limp3bet"
                elif raises == 3:
                    situation = "bb_vs_4bet" if p != sb else "sb_vs_4bet"
                else:
                    situation = f"{pos}_vs_5bet"
                options = ("fold", "call", "raise")
                generic = {1: "vs_2bet", 2: "vs_3bet", 3: "vs_4bet"}.get(raises, "vs_5bet")
                self._record(p, generic, choice, options)
            self._record(p, situation, choice, options)

            # Jetons de ligne préflop (ex. « open → call 3bet »)
            if kind == RAISE:
                level = ("open" if p == sb else "iso") if raises == 0 else PF_LEVEL_NAMES.get(raises, "5bet+")
                token = level
                if not a.all_in:
                    self.sizes[p].append((f"pf_{level}_bb", a.to / h.bb))
                    if raises >= 1:
                        self.sizes[p].append((f"pf_{level}_x", a.to / last_to))
                else:
                    token += " (tapis)"
                raises += 1
                last_to, last_level = a.to, level
                self.pfa = p
            elif kind == CALL:
                token = "limp" if raises == 0 else f"call {last_level}"
                if a.all_in:
                    token += " (tapis)"
            elif kind == FOLD:
                token = "fold" if raises == 0 else f"fold vs {last_level}"
            else:
                token = "check"
            acted[p].append(kind)
            self.tokens[p].append(token)

        self.pf_raises = raises
        for p, kinds in acted.items():
            if not kinds:
                continue  # BB sans décision (walk)
            pos = self.pos(p)
            vpip = any(k in (CALL, RAISE) for k in kinds)
            pfr = RAISE in kinds
            for key, made in (("vpip", vpip), ("pfr", pfr), (f"vpip_{pos}", vpip), (f"pfr_{pos}", pfr)):
                self._flag(p, key, made)

    @property
    def pot_type(self) -> str:
        return {0: "limp", 1: "srp", 2: "3bp"}.get(self.pf_raises, "4bp")

    def preflop_line(self, player: str) -> str:
        tokens = self.tokens.get(player) or ["walk"]
        return " → ".join(tokens)

    # -- postflop ------------------------------------------------------------
    def saw_flop(self, player: str) -> bool:
        folded_pf = any(a.kind == FOLD and a.street == "preflop" for a in self.h.actions)
        return not folded_pf and len(self.h.board) >= 3

    def _read_postflop(self) -> None:
        h, pfa = self.h, self.pfa
        if not self.saw_flop(self.sb):
            return
        made_cbet: dict[str, bool] = {}
        checked_through: dict[str, bool] = {}
        called_cbet: dict[str, bool] = {}
        for street in POSTFLOP:
            acts = h.street_actions(street)
            if not acts:
                break
            prev = POSTFLOP[POSTFLOP.index(street) - 1] if street != "flop" else None
            level, last_to = 0, 0.0
            checked: set[str] = set()
            bet_ctx: Optional[str] = None
            for i, a in enumerate(acts):
                p, kind = a.player, a.kind
                choice = OUTCOMES[kind]
                if level == 0:
                    contexts = ["lead_" + street if i == 0 else "stab_" + street]
                    if pfa:
                        contexts += self._pfa_contexts(p, street, i, prev, made_cbet, checked_through, called_cbet)
                    for ctx in contexts:
                        self._flag(p, ctx, kind == BET)
                    if kind == BET:
                        pct = 100.0 * a.amount / a.pot_before if a.pot_before else 0.0
                        bet_ctx = contexts[-1]
                        if not a.all_in or pct <= 150:
                            self.sizes[p].append((f"bet_{street}", pct))
                            for ctx in contexts[1:]:
                                self.sizes[p].append((ctx, pct))
                        if bet_ctx == f"cbet_{street}":
                            made_cbet[street] = True
                    else:
                        checked.add(p)
                else:
                    facing = "vs_bet" if level == 1 else "vs_raise"
                    self._record(p, f"{facing}_{street}", choice, ("fold", "call", "raise"))
                    if level == 1:
                        if p in checked:
                            self._flag(p, f"xr_{street}", kind == RAISE)
                        if bet_ctx and not bet_ctx.startswith(("lead_", "stab_")):
                            self._record(p, f"vs_{bet_ctx}", choice, ("fold", "call", "raise"))
                            if bet_ctx == f"cbet_{street}" and kind == CALL:
                                called_cbet[street] = True
                    if kind == RAISE and last_to and not a.all_in:
                        self.sizes[p].append((f"raise_{street}_x", a.to / last_to))
                if kind in AGGRESSIVE:
                    level += 1
                    last_to = a.to
            checked_through[street] = all(a.kind == CHECK for a in acts)

    def _pfa_contexts(self, p, street, index, prev, made_cbet, checked_through, called_cbet) -> list[str]:
        """Situations spécifiques liées à l'agresseur préflop (c-bet, donk, probe...)."""
        pfa, ip = self.pfa, self.sb
        side = "ip" if p == ip else "oop"
        if p == pfa:
            if street == "flop":
                return ["cbet_flop_" + side, "cbet_flop"]
            if made_cbet.get(prev) and (street == "turn" or made_cbet.get("flop")):
                return [f"cbet_{street}"]
            if street == "turn" and checked_through.get("flop"):
                return ["delayed_cbet_turn"]
            return []
        # Joueur non agresseur préflop
        if street == "flop":
            return ["donk_flop"] if index == 0 else ["float_flop"]
        if street == "turn":
            if index == 0 and checked_through.get("flop") and pfa == ip:
                return ["probe_turn"]
            if index == 0 and called_cbet.get("flop"):
                return ["donk_turn"]
            if index > 0 and called_cbet.get("flop"):
                return ["float_turn"]
        return []


def think_times(hand: Hand) -> list[Optional[float]]:
    """Temps de réflexion (s) de chaque action, mesuré depuis l'action précédente (calculé une fois par main)."""
    known = hand.__dict__.get("_think_times")
    if known is None:
        known = hand.__dict__["_think_times"] = _think_times(hand)
    return known


def _think_times(hand: Hand) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    prev = None
    for a in hand.actions:
        if a.time is None or prev is None or a.kind not in VOLUNTARY:
            out.append(None)
        else:
            out.append(max((a.time - prev).total_seconds(), 0.0))
        prev = a.time or prev
    return out


def allin_ev(hand: Hand) -> Optional[dict[str, float]]:
    """Gain net espéré (en monnaie) de chaque joueur pour un all-in payé avant la river (calculé une fois par main)."""
    if "_allin_ev" not in hand.__dict__:
        hand.__dict__["_allin_ev"] = _allin_ev(hand)
    return hand.__dict__["_allin_ev"]


def _allin_ev(hand: Hand) -> Optional[dict[str, float]]:
    if not hand.showdown or not any(a.all_in for a in hand.actions):
        return None
    if any(len(hand.hole_cards.get(p, [])) != 2 for p in hand.seats):
        return None
    last = [a for a in hand.actions if a.kind in VOLUNTARY][-1]
    board_size = {"preflop": 0, "flop": 3, "turn": 4}.get(last.street)
    if board_size is None:
        return None
    p1, p2 = hand.players
    eq = equity(hand.hole_cards[p1], hand.hole_cards[p2], hand.board[:board_size], seed=hand.hand_id)
    pot = hand.total_pot - hand.rake
    invested = {p: hand.put[p] - hand.uncalled[p] for p in hand.seats}
    return {p1: eq * pot - invested[p1], p2: (1 - eq) * pot - invested[p2]}


def analyze(hands: list[Hand]) -> dict[str, PlayerStats]:
    """Statistiques de chaque joueur sur l'ensemble des mains (ordre chronologique)."""
    stats: dict[str, PlayerStats] = {}
    for hand in hands:
        if not hand.button or not hand.big_blind or not hand.bb:
            continue
        reader = HandReader.of(hand)
        times = think_times(hand)
        ev = allin_ev(hand)
        eff_bb = hand.effective_stack() / hand.bb
        for p in hand.seats:
            st = stats.setdefault(p, PlayerStats(p))
            pos = reader.pos(p)
            st.hands += 1
            st.hands_by_pos[pos] += 1
            st.eff_stacks_bb.append(eff_bb)
            for key, made in reader.events.get(p, []):
                st.ratios[key].add(made)
            for key, value in reader.sizes.get(p, []):
                st.sizes[key].append(value)
            st.preflop_lines[f"{pos}:{reader.preflop_line(p)}"] += 1

            for a, t in zip(hand.actions, times):
                if a.player != p or a.kind not in VOLUNTARY:
                    continue
                if a.street != "preflop":
                    st.street_actions[a.street][a.kind] += 1
                if t is not None:
                    group = "pf" if a.street == "preflop" else "post"
                    st.times[f"{group}_{a.kind}"].append(t)
                    st.times[f"{a.street}_{a.kind}"].append(t)

            # Abattage
            saw = reader.saw_flop(p)
            won = hand.winnings.get(p, 0.0) > 0
            st.ratios["saw_flop"].add(saw)
            if saw:
                st.ratios["wtsd"].add(hand.showdown)
                st.ratios["wwsf"].add(won)
                if hand.showdown:
                    st.ratios["wsd"].add(won)

            # Résultats
            net = hand.net(p)
            net_bb = net / hand.bb
            st.net += net
            st.net_bb += net_bb
            st.net_bb_by_pos[pos] += net_bb
            if hand.showdown:
                st.net_bb_showdown += net_bb
            else:
                st.net_bb_no_showdown += net_bb
            if ev is not None:
                st.allin_hands += 1
                st.ev_adjust_bb += (ev[p] - net) / hand.bb
            prev = st.curve[-1] if st.curve else (0.0, 0.0, 0.0, 0.0)
            ev_bb = ev[p] / hand.bb if ev is not None else net_bb
            st.curve.append(
                (
                    prev[0] + net_bb,
                    prev[1] + ev_bb,
                    prev[2] + (net_bb if hand.showdown else 0.0),
                    prev[3] + (0.0 if hand.showdown else net_bb),
                )
            )

            cards = hand.hole_cards.get(p)
            if hand.showdown and cards and len(cards) == 2:
                st.showdowns.append(
                    ShowdownEntry(
                        hand=hand,
                        player=p,
                        cards=cards,
                        combo=combo_notation(cards),
                        position="BTN" if pos == "sb" else "BB",
                        preflop_line=reader.preflop_line(p),
                        made_hand=hand.shown_hand.get(p, ""),
                        net_bb=net_bb,
                    )
                )
    return stats
