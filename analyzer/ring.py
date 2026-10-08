"""Tables à plusieurs (3-max, 6-max) : tes stats par position.

Lecture de chaque main où tu es servi à une table de 3 joueurs ou plus : ouverture quand on te laisse parler en
premier (open), 3bet et flat face à une ouverture, défense des blindes face à un vol (ouverture du CO, du bouton
ou de la SB, sans caller), réaction au 3bet après ton open, c-bet au flop en pot à deux ou à plusieurs, fold face
à la c-bet de l'agresseur, abattage. Une main sans décision (la BB à qui tout le monde folde) compte dans les mains
et le résultat, pas dans les fréquences.

Repères : fourchettes indicatives d'un régulier en 6-max à 100 bb (stats de tracker courantes) ; pas de repère en
3-max. Les tables de 3 à 9 joueurs se lisent aussi ensemble (analyze(…, merge=True), MERGED), avec les repères du
6-max.

Les adversaires des tables à plusieurs : leurs fréquences à chaque taille de table (profiles, pour les classer
régulier ou récréatif, players.ring_suggest) et les pots disputés avec toi (opponents).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .insights import StatDef
from .models import BET, CALL, FOLD, RAISE, VOLUNTARY, Hand
from .stats import Ratio

ORDER = ("UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN", "SB", "BB")
STEAL_FROM = ("CO", "BTN", "SB")
FORMATS = ("6-max", "3-max")
MERGED = "Tables à plusieurs"  # toutes les tables de 3 joueurs et plus, ensemble

STATS = (
    ("vpip", "VPIP"), ("pfr", "PFR"), ("open", "Open"), ("limp", "Limp d'entrée"), ("threebet", "3bet"),
    ("flat", "Call d'une ouverture"), ("fold_3bet", "Fold vs 3bet"), ("fold_steal", "Fold vs vol"),
    ("threebet_steal", "3bet vs vol"), ("cbet_hu", "C-bet flop (pot à deux)"), ("cbet_multi", "C-bet flop (à plusieurs)"),
    ("fold_cbet", "Fold vs c-bet"), ("wtsd", "Abattage (WTSD)"), ("wsd", "Gagné à l'abattage (W$SD)"),
)
LABEL = dict(STATS)

# Repères indicatifs en 6-max à 100 bb : toutes positions, puis l'open par position.
REF_6MAX = {"vpip": (21, 27), "pfr": (17, 23), "threebet": (7, 11), "fold_3bet": (45, 60), "fold_steal": (55, 75),
            "cbet_hu": (50, 70), "fold_cbet": (35, 50), "wtsd": (25, 31), "wsd": (49, 56)}
OPEN_6MAX = {"UTG": (14, 18), "HJ": (18, 23), "CO": (25, 32), "BTN": (40, 50), "SB": (35, 50)}


@dataclass
class PositionStats:
    position: str
    hands: int = 0
    net_bb: float = 0.0
    ratios: dict[str, Ratio] = field(default_factory=lambda: {k: Ratio() for k, _ in STATS})

    @property
    def bb100(self) -> Optional[float]:
        return 100 * self.net_bb / self.hands if self.hands else None


@dataclass
class FormatStats:
    table_format: str
    total: PositionStats
    positions: list[PositionStats]
    sites: list[str]
    first: Optional[object] = None
    last: Optional[object] = None
    formats: list[str] = field(default_factory=list)  # les tailles de table réunies (MERGED)


def read(hand: Hand, hero: str) -> dict[str, bool]:
    """Ce que la main dit de toi : {stat: fait ?} pour chaque occasion rencontrée."""
    out: dict[str, bool] = {}
    pos = hand.position(hero)
    pre = [a for a in hand.actions if a.street == "preflop" and a.kind in VOLUNTARY]
    raises = limpers = callers = 0
    opener = None
    first_done = hero_open = facing_3bet_done = False
    folded = set()
    acted = False
    for a in pre:
        if a.player == hero:
            acted = True
            if not first_done:
                first_done = True
                if raises == 0 and limpers == 0:
                    out["open"], out["limp"] = a.kind == RAISE, a.kind == CALL
                    hero_open = a.kind == RAISE
                elif raises == 1:
                    out["threebet"], out["flat"] = a.kind == RAISE, a.kind == CALL
                    if pos in ("SB", "BB") and hand.position(opener) in STEAL_FROM and limpers == 0 and callers == 0:
                        out["fold_steal"], out["threebet_steal"] = a.kind == FOLD, a.kind == RAISE
            elif hero_open and raises == 2 and not facing_3bet_done:
                facing_3bet_done = True
                out["fold_3bet"] = a.kind == FOLD
        if a.kind == RAISE:
            raises += 1
            if raises == 1:
                opener, callers = a.player, 0
        elif a.kind == CALL:
            if raises == 0:
                limpers += 1
            else:
                callers += 1
        elif a.kind == FOLD:
            folded.add(a.player)
    if acted:
        out["vpip"] = any(a.player == hero and a.kind in (CALL, RAISE) for a in pre)
        out["pfr"] = any(a.player == hero and a.kind == RAISE for a in pre)
    if hero in folded or len(hand.board) < 3:
        return out
    aggressor = next((a.player for a in reversed(pre) if a.kind == RAISE), None)
    on_flop = [p for p in hand.seats if p not in folded]
    flop = [a for a in hand.actions if a.street == "flop" and a.kind in VOLUNTARY]
    if aggressor == hero:
        mine = next((k for k, a in enumerate(flop) if a.player == hero), None)
        if mine is not None and not any(a.kind in (BET, RAISE) for a in flop[:mine]):
            out["cbet_hu" if len(on_flop) == 2 else "cbet_multi"] = flop[mine].kind == BET
    elif aggressor in on_flop:
        bet = next((k for k, a in enumerate(flop) if a.kind in (BET, RAISE)), None)
        if bet is not None and flop[bet].player == aggressor and flop[bet].kind == BET:
            answer = next((a for a in flop[bet + 1:] if a.player == hero or a.kind == RAISE), None)
            if answer is not None and answer.player == hero:
                out["fold_cbet"] = answer.kind == FOLD
    folded_later = any(a.player == hero and a.kind == FOLD for a in hand.actions)
    out["wtsd"] = hand.showdown and not folded_later
    if out["wtsd"]:
        out["wsd"] = hand.net(hero) > 0  # une mise non payée rendue en side pot n'est pas un gain
    return out


def analyze(hands: list[Hand], hero: str, merge: bool = False) -> list[FormatStats]:
    """Tes stats par format de table (6-max, 3-max…), puis par position ; merge=True : toutes les tables de 3 joueurs
    et plus ensemble (MERGED), une position comptant ses mains de chaque taille de table."""
    out = []
    formats = sorted({h.table_format for h in hands if hero in h.seats and h.size > 2},
                     key=lambda f: (FORMATS.index(f) if f in FORMATS else len(FORMATS), f))
    if merge:
        formats = [MERGED] if formats else []
    for fmt in formats:
        mine = [h for h in hands if hero in h.seats and h.bb and h.size > 2 and (fmt == MERGED or h.table_format == fmt)]
        total = PositionStats("Toutes")
        by_pos: dict[str, PositionStats] = {}
        for h in mine:
            pos = h.position(hero) or "?"
            for ps in (total, by_pos.setdefault(pos, PositionStats(pos))):
                ps.hands += 1
                ps.net_bb += h.net(hero) / h.bb
            for key, made in read(h, hero).items():
                total.ratios[key].add(made)
                by_pos[pos].ratios[key].add(made)
        positions = sorted(by_pos.values(), key=lambda p: ORDER.index(p.position) if p.position in ORDER else len(ORDER))
        out.append(FormatStats(fmt, total, positions, sorted({h.site for h in mine}),
                               mine[0].date if mine else None, mine[-1].date if mine else None,
                               sorted({h.table_format for h in mine}, key=lambda f: (h_size(f), f))))
    return out


def h_size(table_format: str) -> int:
    """La taille d'une table d'après son format (« 6-max », « 9 joueurs »), pour les ranger."""
    digits = "".join(c for c in table_format if c.isdigit())
    return int(digits) if digits else 0


def ref(table_format: str, key: str, position: Optional[str] = None) -> Optional[tuple[float, float]]:
    if table_format not in ("6-max", MERGED):
        return None
    if key == "open" and position:
        return OPEN_6MAX.get(position)
    return REF_6MAX.get(key) if position is None else None


def stat_def(table_format: str, key: str, position: Optional[str] = None) -> StatDef:
    return StatDef(key, LABEL[key], ref(table_format, key, position))


# --- Les adversaires ------------------------------------------------------------------------------------------------

def size_class(players: int) -> str:
    """« 3 », « 6 » (4 à 6 joueurs) ou « 9 » (7 et plus)."""
    return "3" if players <= 3 else "6" if players <= 6 else "9"


def profiles(hands: list[Hand], names: set[str]) -> dict[str, dict]:
    """Les fréquences de ces joueurs (read) à chaque taille de table : {joueur: {taille: {"hands": n, stat: Ratio}}}."""
    out: dict[str, dict] = {}
    for h in hands:
        if h.size <= 2 or not h.bb:
            continue
        size = size_class(h.size)
        for name in names & set(h.seats):
            st = out.setdefault(name, {}).setdefault(size, {"hands": 0})
            st["hands"] += 1
            for key, made in read(h, name).items():
                st.setdefault(key, Ratio()).add(made)
    return out


def in_pot(hand: Hand, player: str) -> bool:
    """Le joueur a mis de l'argent dans le pot de lui-même (call, relance, mise ; pas une blinde)."""
    return any(a.player == player and a.kind in (CALL, RAISE, BET) for a in hand.actions)


def opponents(hands: list[Hand], hero: str) -> list[dict]:
    """Tes adversaires aux tables à plusieurs, du plus joué au moins joué : mains à la même table, pots disputés
    ensemble (chacun y a mis de l'argent de lui-même), ton résultat dans ces pots."""
    rows: dict[str, dict] = {}
    for h in hands:
        if h.size <= 2 or hero not in h.seats or not h.bb:
            continue
        mine = in_pot(h, hero)
        for name in h.seats:
            if name == hero:
                continue
            row = rows.setdefault(name, {"name": name, "hands": 0, "pots": 0, "net_bb": 0.0, "last": None})
            row["hands"] += 1
            row["last"] = max(row["last"] or h.date, h.date)
            if mine and in_pot(h, name):
                row["pots"] += 1
                row["net_bb"] += h.net(hero) / h.bb
    out = list(rows.values())
    for row in out:
        row["bb100"] = 100 * row["net_bb"] / row["pots"] if row["pots"] else 0.0
    return sorted(out, key=lambda r: (-r["hands"], r["name"]))
