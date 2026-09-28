"""Modèle de données commun à tous les parseurs d'historiques."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

STREETS = ("preflop", "flop", "turn", "river")
POSTFLOP = STREETS[1:]

# Types d'action normalisés
POST_SB, POST_BB = "post_sb", "post_bb"
FOLD, CHECK, CALL, BET, RAISE = "fold", "check", "call", "bet", "raise"
VOLUNTARY = (FOLD, CHECK, CALL, BET, RAISE)
AGGRESSIVE = (BET, RAISE)


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


@dataclass
class Hand:
    site: str
    hand_id: str
    table_id: str
    game_name: str
    date: datetime
    sb: float
    bb: float
    total_pot: float
    rake: float
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
        return next((s.name for s in self.seats.values() if not s.is_button), None)

    def opponent_of(self, name: str) -> Optional[str]:
        return next((p for p in self.seats if p != name), None)

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
