"""Modèle de données commun à tous les parseurs d'historiques."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

STREETS = ("preflop", "flop", "turn", "river")
POSTFLOP = STREETS[1:]

# Types d'action normalisés
POST_SB, POST_BB, POST_ANTE = "post_sb", "post_bb", "post_ante"
FOLD, CHECK, CALL, BET, RAISE = "fold", "check", "call", "bet", "raise"
VOLUNTARY = (FOLD, CHECK, CALL, BET, RAISE)
AGGRESSIVE = (BET, RAISE)

# Positions entre le bouton et les blindes, selon le nombre de joueurs restants (de la première à parler au cutoff)
MIDDLE = {0: (), 1: ("CO",), 2: ("HJ", "CO"), 3: ("UTG", "HJ", "CO"), 4: ("UTG", "LJ", "HJ", "CO"),
          5: ("UTG", "UTG+1", "LJ", "HJ", "CO"), 6: ("UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO")}


@dataclass
class Action:
    player: str
    kind: str
    street: str
    amount: float  # montant ajouté au pot par cette action
    to: float  # total engagé par le joueur sur la street après l'action
    all_in: bool = False
    time: Optional[datetime] = None
    pot_before: float = 0.0  # pot (toutes streets) juste avant l'action
    facing: float = 0.0  # montant à payer pour suivre avant l'action


@dataclass
class Seat:
    name: str
    seat: int
    stack: float
    is_button: bool = False  # en HU le bouton est aussi la SB
    is_hero: bool = False
    position: str = ""  # BTN, SB, BB, CO, HJ, UTG… (calculée par Hand.finalize)


@dataclass
class Hand:
    site: str
    hand_id: str
    table_id: str
    game_name: str
    date: datetime
    sb: float
    bb: float
    total_pot: float  # rake compris
    rake: float
    max_seats: int = 0  # places à la table (0 : inconnu)
    seats: dict[str, Seat] = field(default_factory=dict)
    hole_cards: dict[str, list[str]] = field(default_factory=dict)
    board: list[str] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    winnings: dict[str, float] = field(default_factory=dict)
    showdown: bool = False
    shown_hand: dict[str, str] = field(default_factory=dict)  # "Two Pair", ...
    # Calculés par finalize()
    put: dict[str, float] = field(default_factory=dict)
    uncalled: dict[str, float] = field(default_factory=dict)

    def __getstate__(self) -> dict:
        """Sans les lectures gardées sur la main (attributs « _… », comme HandReader.of) : le cache des mains lues
        (analyzer.store) n'enregistre que la main."""
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    # --- Accès pratiques -------------------------------------------------
    @property
    def players(self) -> list[str]:
        return list(self.seats)

    @property
    def hero(self) -> Optional[str]:
        return next((s.name for s in self.seats.values() if s.is_hero), None)

    @property
    def button(self) -> Optional[str]:
        return next((s.name for s in self.seats.values() if s.is_button), None)

    @property
    def big_blind(self) -> Optional[str]:
        """Le joueur qui a posté la grosse blinde (en HU, à défaut, celui qui n'a pas le bouton)."""
        poster = next((a.player for a in self.actions if a.kind == POST_BB), None)
        if poster is not None or len(self.seats) != 2:
            return poster
        return next((s.name for s in self.seats.values() if not s.is_button), None)

    @property
    def small_blind(self) -> Optional[str]:
        return next((a.player for a in self.actions if a.kind == POST_SB), None)

    @property
    def size(self) -> int:
        """Nombre de joueurs servis."""
        return len(self.seats)

    @property
    def table_format(self) -> str:
        """« HU », « 3-max », « 6-max »… d'après le nombre de joueurs servis (deux joueurs : heads-up)."""
        n = len(self.seats)
        return "HU" if n <= 2 else "3-max" if n == 3 else "6-max" if n <= 6 else f"{n} joueurs"

    def position(self, name: str) -> str:
        seat = self.seats.get(name)
        return seat.position if seat else ""

    def opponent_of(self, name: str) -> Optional[str]:
        """L'adversaire en heads-up (pour une table à plusieurs, voir opponents_of)."""
        return next((p for p in self.seats if p != name), None)

    def opponents_of(self, name: str) -> list[str]:
        return [p for p in self.seats if p != name]

    def rename(self, old: str, new: str) -> None:
        """Renomme un joueur partout dans la main (ex. le héros d'un autre site ramené à son pseudo principal)."""
        if old not in self.seats or new in self.seats:
            return
        self.seats = {(new if k == old else k): v for k, v in self.seats.items()}
        self.seats[new].name = new
        for table in (self.hole_cards, self.winnings, self.shown_hand, self.put, self.uncalled):
            if old in table:
                table[new] = table.pop(old)
        for a in self.actions:
            if a.player == old:
                a.player = new

    def street_actions(self, street: str, voluntary_only: bool = True) -> list[Action]:
        return [
            a
            for a in self.actions
            if a.street == street and (not voluntary_only or a.kind in VOLUNTARY)
        ]

    def folded(self) -> Optional[str]:
        return next((a.player for a in self.actions if a.kind == FOLD), None)

    def net(self, name: str) -> float:
        """Gain net du joueur sur la main (rake déduit)."""
        return self.winnings.get(name, 0.0) - self.put.get(name, 0.0) + self.uncalled.get(name, 0.0)

    def effective_stack(self) -> float:
        return min(s.stack for s in self.seats.values())

    def finalize(self) -> None:
        """Calcule les mises totales et la part non payée rendue au joueur.

        Selon les sites, un surplus non payé (ex. tapis supérieur au tapis adverse)
        n'apparaît pas dans le pot total : on le rend au plus gros contributeur.
        """
        put = {p: 0.0 for p in self.seats}
        for a in self.actions:
            put[a.player] = put.get(a.player, 0.0) + a.amount
        self.put = {p: round(v, 2) for p, v in put.items()}
        self.uncalled = {p: 0.0 for p in self.seats}
        excess = round(sum(self.put.values()) - self.total_pot, 2)
        if excess > 0.005 and self.put:
            biggest = max(self.put, key=self.put.get)
            self.uncalled[biggest] = excess
        self._assign_positions()

    def _assign_positions(self) -> None:
        """BTN, SB et BB d'après le bouton et les blindes postées ; les autres, du cutoff vers la première parole,
        dans l'ordre des places après le bouton."""
        order = sorted(self.seats.values(), key=lambda s: s.seat)
        button = self.button
        if button is None or len(order) < 2:
            return
        if len(order) == 2:
            for s in order:
                s.position = "BTN" if s.name == button else "BB"
            return
        start = next(k for k, s in enumerate(order) if s.name == button)
        after = [order[(start + k) % len(order)] for k in range(1, len(order))]  # de la SB au cutoff
        sb, bb = self.small_blind, self.big_blind
        if bb is None:  # blindes absentes : les deux places après le bouton
            sb, bb = after[0].name, after[1].name
        rest = [s for s in after if s.name not in (sb, bb)]
        labels = MIDDLE.get(len(rest)) or tuple(f"EP{k + 1}" for k in range(len(rest) - 2)) + ("HJ", "CO")
        self.seats[button].position = "BTN"
        if sb in self.seats and sb != button:
            self.seats[sb].position = "SB"
        self.seats[bb].position = "BB"
        for s, label in zip(rest, labels):
            s.position = label
