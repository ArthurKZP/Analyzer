"""Le catalogue des statistiques de l'onglet Statistiques (à la façon de PokerTracker) : plus d'une centaine de
stats, d'avant le flop à l'abattage, que chacun choisit, ajoute, retire et range à sa guise (settings :
stat_selection).

Chaque stat se lit main par main, pour un joueur, puis s'additionne (tally) sur toutes ses mains et position par
position. Celles qui existaient déjà se lisent comme ailleurs dans l'application, pour donner les mêmes chiffres
que le bilan et le Leakfinding : en heads-up stats.HandReader, aux tables à plusieurs ring.read et
field.postflop_read. Les autres viennent de read (ici), en heads-up comme aux tables à plusieurs.

Une stat est une fréquence (fois où il l'a fait / fois où il pouvait le faire), une moyenne (taille d'une mise), un
facteur d'agression (mises et relances / calls) ou un résultat (bb/100). Ses repères sont ceux du reste de
l'application : la solution préflop et ceux d'un régulier HU solide en heads-up (insights), ceux d'un régulier 6-max à
100 bb aux tables à plusieurs (ring, field) ; pas de repère inventé.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import field as field_stats
from . import ring
from .insights import STAT_DEFS, StatDef
from .models import BET, CALL, CHECK, FOLD, RAISE, VOLUNTARY, Hand
from .rake import paid as rake_paid
from .stats import HandReader, Ratio, allin_ev

FORMATS = ("HU", "ring")
STREETS = (("f", "flop", "flop"), ("t", "turn", "turn"), ("r", "river", "river"))
STEAL_FROM = ring.STEAL_FROM
POSITIONS = {"HU": ("BTN", "BB"), "ring": ring.ORDER}


# --- La lecture d'une main ------------------------------------------------------------------------------------------

@dataclass
class Events:
    """Ce qu'une main dit d'un joueur : ses occasions (clé, fait ?), des valeurs (taille d'une mise…) et ses actions
    après le flop, street par street (pour l'agressivité)."""
    ratios: list = field(default_factory=list)
    values: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)

    def flag(self, key: str, made: bool) -> None:
        self.ratios.append((key, made))

    def choice(self, prefix: str, kind: str, suffix: str = "") -> None:
        """Face à une mise : fold, call ou relance (trois occasions, une seule faite)."""
        for name, option in (("fold", FOLD), ("call", CALL), ("raise", RAISE)):
            self.ratios.append((f"{name}_{prefix}{suffix}", kind == option))


def read(hand: Hand) -> dict[str, Events]:
    """Les stats que le reste de l'application ne lit pas, pour chaque joueur servi (gardées sur la main)."""
    found = hand.__dict__.get("_statlib")
    if found is None:
        found = hand.__dict__["_statlib"] = _read(hand)
    return found


def _read(hand: Hand) -> dict[str, Events]:
    out = {p: Events() for p in hand.seats}
    raises = _read_preflop(hand, out)
    _read_postflop(hand, out, raises)
    return out


def _read_preflop(hand: Hand, out: dict[str, Events]) -> int:
    """Avant le flop ; renvoie le nombre de relances."""
    pre = [a for a in hand.actions if a.street == "preflop" and a.kind in VOLUNTARY]
    raises = limpers = callers = 0
    last_to = hand.bb
    opener = None
    acted: set[str] = set()
    opened: set[str] = set()       # a ouvert (relance, personne n'étant entré)
    limped: set[str] = set()       # est entré en payant, personne n'ayant relancé
    threebet: set[str] = set()     # a fait la première relance d'une ouverture
    faced_3bet: set[str] = set()
    faced_any_3bet: set[str] = set()
    faced_4bet: set[str] = set()
    faced_iso: set[str] = set()
    folded: set[str] = set()
    for a in pre:
        p, kind = a.player, a.kind
        ev = out.get(p)
        if ev is None:
            continue
        pos = hand.position(p)
        if p not in acted:
            if raises == 0 and limpers == 0:
                ev.flag("fold_rfi", kind == FOLD)
            elif raises == 0:
                ev.flag("iso", kind == RAISE)
                ev.flag("overlimp", kind == CALL)
            elif raises == 1:
                ev.flag("fold_open", kind == FOLD)
                if callers:
                    ev.flag("squeeze", kind == RAISE)
                if (pos in ("SB", "BB") and hand.position(opener) in STEAL_FROM and limpers == 0 and callers == 0):
                    ev.flag("call_steal", kind == CALL)
        if raises == 1 and p in limped and p not in faced_iso:
            faced_iso.add(p)
            ev.choice("limp", kind)  # fold_limp, call_limp, raise_limp
        if raises == 2 and p not in faced_any_3bet:
            faced_any_3bet.add(p)
            ev.flag("fourbet_any", kind == RAISE)
            if p in opened:
                faced_3bet.add(p)
                ev.choice("vs3", kind)  # fold_vs3, call_vs3, raise_vs3 (4bet)
        if raises == 3 and p in threebet and p not in faced_4bet:
            faced_4bet.add(p)
            ev.choice("vs4", kind)  # fold_vs4, call_vs4, raise_vs4 (5bet)
        if kind == RAISE and not a.all_in:
            if raises == 0:
                ev.values.append(("open_bb", a.to / hand.bb))
            elif raises in (1, 2):
                ev.values.append((("threebet_x", "fourbet_x")[raises - 1], a.to / last_to))
        if kind == RAISE:
            if raises == 0 and limpers == 0 and p not in acted:
                opened.add(p)
            if raises == 1:
                threebet.add(p)
            raises += 1
            last_to, callers = a.to, 0
            if raises == 1:
                opener = p
        elif kind == CALL:
            if raises == 0:
                limpers += 1
                if p not in acted:
                    limped.add(p)
            else:
                callers += 1
        elif kind == FOLD:
            folded.add(p)
        acted.add(p)
    saw = len(hand.board) >= 3
    for p, ev in out.items():
        ev.flag("saw_f", saw and p not in folded)
    return raises


def _read_postflop(hand: Hand, out: dict[str, Events], raises: int) -> None:
    if len(hand.board) < 3:
        return
    pfa = next((a.player for a in reversed(hand.actions) if a.street == "preflop" and a.kind == RAISE), None)
    pot_type = {1: "srp", 2: "3bp"}.get(raises, "4bp") if raises else "limp"
    cbet_chain = True        # l'agresseur a misé en premier chaque street jusqu'ici (c-bet, puis barrels)
    called_cbet: set[str] = set()  # ceux qui ont payé sa c-bet de la street d'avant
    checked_through = False  # la street d'avant s'est jouée sans mise
    for short, street, _ in STREETS:
        acts = [a for a in hand.actions if a.street == street and a.kind in VOLUNTARY]
        if not acts:
            break
        order = list(dict.fromkeys(a.player for a in acts))  # l'ordre de parole : le dernier est en position
        alive = set(order)
        level = 0
        bettor = None
        cbet_now = False
        pfa_acted = pfa_checked = False
        last_to = 0.0
        checked: set[str] = set()
        answered: set[str] = set()
        aggressors: set[str] = set()  # ont misé ou relancé cette street
        called_now: set[str] = set()
        for a in acts:
            p, kind = a.player, a.kind
            ev = out.get(p)
            if ev is None:
                continue
            if kind in (BET, RAISE):
                ev.counts[f"aggr_{short}"] += 1
            elif kind in (CALL, FOLD):
                ev.counts[f"{kind}_{short}"] += 1
            if level == 0:
                ev.flag(f"bet_{short}", kind == BET)
                if p == pfa:
                    if cbet_chain:
                        ev.flag(f"cbet_{short}", kind == BET)
                        if street == "flop":
                            ip = "ip" if p == order[-1] else "oop"
                            ways = "hu" if len(alive) == 2 else "multi"
                            for tag in (ip, ways, pot_type):
                                ev.flag(f"cbet_f_{tag}", kind == BET)
                        cbet_now = kind == BET
                    elif street == "turn" and checked_through:
                        ev.flag("delayed_cbet_t", kind == BET)
                elif pfa in alive:
                    if not pfa_acted:  # il parle avant l'agresseur
                        if street == "flop":
                            ev.flag("donk_f", kind == BET)
                        elif p in called_cbet:
                            ev.flag(f"donk_{short}", kind == BET)
                        elif checked_through:
                            ev.flag(f"probe_{short}", kind == BET)
                    elif pfa_checked and (street == "flop" or p in called_cbet):
                        ev.flag(f"stab_{short}", kind == BET)
                if kind == CHECK:
                    checked.add(p)
                elif kind == BET:
                    level, bettor = 1, p
                    pct = 100.0 * a.amount / a.pot_before if a.pot_before else 0.0
                    if not a.all_in or pct <= 150:
                        ev.values.append((f"size_bet_{short}", pct))
                        if cbet_now and p == pfa:
                            ev.values.append((f"size_cbet_{short}", pct))
            elif level == 1:
                if p not in answered:
                    answered.add(p)
                    ev.choice("bet_", kind, short)  # fold_bet_f, call_bet_f, raise_bet_f
                    if cbet_now and bettor == pfa:
                        ev.choice("cbet_", kind, short)
                        if kind == CALL:
                            called_now.add(p)
                    if p in checked:
                        for name, option in (("xf", FOLD), ("xc", CALL), ("xr", RAISE)):
                            ev.flag(f"{name}_{short}", kind == option)
            elif p in aggressors:  # il avait misé ou relancé : on le relance
                ev.flag(f"fold_raise_{short}", kind == FOLD)
            if kind == RAISE:
                level += 1
                if last_to and not a.all_in:
                    ev.values.append((f"size_raise_{short}", a.to / last_to))
            if kind in (BET, RAISE):
                aggressors.add(p)
                last_to = a.to
            if p == pfa:
                pfa_acted = True
                pfa_checked = kind == CHECK and level == 0
            if kind == FOLD:
                alive.discard(p)
        cbet_chain = cbet_chain and cbet_now
        called_cbet = called_now
        checked_through = level == 0


# --- Le catalogue ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Stat:
    """Une stat du catalogue : son identifiant (gardé dans les paramètres), son nom, son groupe, ce qu'elle mesure,
    et où la lire dans chaque format (src : {"HU": clé, "ring": clé} ; « a+b » additionne deux occasions).

    kind : « pct » (fréquence), « avg » (moyenne, unit), « af » (facteur d'agression : counts), « result » (bb/100 :
    un champ de Tally), « hands ». positions : seulement les mains jouées à ces positions. refs : les repères par
    format ((min, max) en %) ; ref_keys : la stat d'où vient chacun (insights, ring, field), pour ceux d'un niveau de
    limites (field_ref)."""
    id: str
    label: str
    group: str
    help: str
    src: dict
    kind: str = "pct"
    unit: str = ""
    positions: tuple = ()
    refs: dict = field(default_factory=dict)
    ref_keys: dict = field(default_factory=dict)

    def formats(self) -> tuple[str, ...]:
        return tuple(f for f in FORMATS if f in self.src and (not self.positions
                                                              or set(self.positions) & set(POSITIONS[f])))


def _hu_ref(key: str) -> Optional[tuple]:
    d = STAT_DEFS.get(key)
    return d.ref if d else None


def _ring_ref(key: str) -> Optional[tuple]:
    known = ring.REF_6MAX.get(key)
    if known:
        return known
    d = field_stats.RING_DEF.get(key)
    return d.ref if d else None


def _refs(hu: Optional[str] = None, ring_key: Optional[str] = None) -> dict:
    """Les repères d'une stat (refs) et les stats d'où ils viennent (ref_keys)."""
    refs, keys = {}, {}
    for fmt, key, find in (("HU", hu, _hu_ref), ("ring", ring_key, _ring_ref)):
        ref = find(key) if key else None
        if ref:
            refs[fmt], keys[fmt] = ref, key
    return {"refs": refs, "ref_keys": keys}


G_RESULTS = "Résultats"
G_PRE = "Préflop — général"
G_OPEN = "Préflop — ouverture et vol"
G_VS_OPEN = "Préflop — face à une ouverture"
G_3BET = "Préflop — 3bet, 4bet, 5bet"
G_LIMP = "Préflop — limp"
G_PRE_SIZE = "Préflop — tailles"
G_PFA = "Après le flop — à l'initiative (dernier relanceur préflop)"
G_DEF = "Après le flop — face à l'initiative"
G_ANY = "Après le flop — toutes situations"
G_SIZE = "Après le flop — tailles"
G_AGGR = "Agressivité"
G_SD = "Abattage"
GROUPS = (G_RESULTS, G_PRE, G_OPEN, G_VS_OPEN, G_3BET, G_LIMP, G_PRE_SIZE, G_PFA, G_DEF, G_ANY, G_SIZE, G_AGGR, G_SD)

POSITION_NAMES = {"UTG": "UTG", "HJ": "HJ", "CO": "CO", "BTN": "bouton", "SB": "SB", "BB": "BB"}


def _catalog() -> list[Stat]:
    out: list[Stat] = []

    def add(*args, **kwargs) -> None:
        out.append(Stat(*args, **kwargs))

    both = lambda hu, ring_key: {"HU": hu, "ring": ring_key}  # noqa: E731

    # Résultats
    add("hands", "Mains", G_RESULTS, "Mains jouées.", both("hands", "hands"), kind="hands")
    add("bb100", "bb/100", G_RESULTS, "Ton résultat en big blinds pour 100 mains (rake déduit).",
        both("net_bb", "net_bb"), kind="result")
    add("bb100_ev", "bb/100 EV all-in", G_RESULTS, "Ton résultat en remplaçant celui des tapis payés avant la river "
        "par ton espérance (le hasard des tapis retiré).", both("ev_bb", "ev_bb"), kind="result")
    add("bb100_sd", "bb/100 à l'abattage", G_RESULTS, "Ce que te rapportent les mains allées à l'abattage, pour 100 "
        "mains jouées.", both("sd_bb", "sd_bb"), kind="result")
    add("bb100_nsd", "bb/100 sans abattage", G_RESULTS, "Ce que te rapportent les mains finies sans abattage, pour 100 "
        "mains jouées.", both("nsd_bb", "nsd_bb"), kind="result")
    add("rake100", "Rake payé (bb/100)", G_RESULTS, "Ta part du rake de chaque pot, au prorata de ce que tu y as mis.",
        both("rake_bb", "rake_bb"), kind="result")

    # Préflop — général
    add("vpip", "VPIP", G_PRE, "Mains où tu mets de l'argent de toi-même avant le flop (call ou relance ; poster une "
        "blinde ne compte pas).", both("vpip", "vpip"), **_refs(ring_key="vpip"))
    add("pfr", "PFR", G_PRE, "Mains où tu relances avant le flop.", both("pfr", "pfr"), **_refs(ring_key="pfr"))
    add("saw_flop", "Voit le flop", G_PRE, "Mains où tu vois le flop.", both("saw_flop", "saw_f"))
    for pos in ring.SIX_MAX:
        name = POSITION_NAMES[pos]
        hu = {"BTN": "vpip_sb", "BB": "vpip_bb"}.get(pos)
        add(f"vpip_{pos.lower()}", f"VPIP {name}", G_PRE, f"VPIP quand tu es {name}.", both("vpip", "vpip"),
            positions=(pos,), **_refs(hu=hu))
    for pos in ring.SIX_MAX:
        name = POSITION_NAMES[pos]
        add(f"pfr_{pos.lower()}", f"PFR {name}", G_PRE, f"PFR quand tu es {name}.", both("pfr", "pfr"),
            positions=(pos,))

    # Préflop — ouverture et vol
    add("rfi", "Open (RFI)", G_OPEN, "Tu relances quand personne n'est entré avant toi (Raise First In).",
        both("sb_first.raise", "open"), **_refs(hu="sb_first.raise"))
    for pos in ("UTG", "HJ", "CO", "BTN", "SB"):
        name = POSITION_NAMES[pos]
        refs = {"ring": ring.OPEN_6MAX[pos]} if pos in ring.OPEN_6MAX else {}
        if pos == "BTN" and _hu_ref("sb_first.raise"):
            refs["HU"] = _hu_ref("sb_first.raise")
        add(f"rfi_{pos.lower()}", f"Open {name}", G_OPEN, f"Open (RFI) quand tu es {name}.",
            both("sb_first.raise", "open"), positions=(pos,), refs=refs)
    add("steal", "Tentative de vol", G_OPEN, "Open du CO, du bouton ou de la SB quand personne n'est entré (attempt "
        "to steal).", both("sb_first.raise", "open"), positions=STEAL_FROM)
    add("limp", "Limp d'entrée", G_OPEN, "Tu paies la blinde quand personne n'est entré avant toi.",
        both("sb_first.call", "limp"), **_refs(hu="sb_first.call"))
    add("fold_rfi", "Fold d'entrée", G_OPEN, "Tu te couches quand personne n'est entré avant toi.",
        both("sb_first.fold", "fold_rfi"), **_refs(hu="sb_first.fold"))

    # Préflop — face à une ouverture
    add("fold_open", "Fold face à une ouverture", G_VS_OPEN, "Tu te couches face à une ouverture (une relance, pas "
        "encore de 3bet), à ta première parole.", both("bb_vs_open.fold", "fold_open"),
        **_refs(hu="bb_vs_open.fold"))
    add("call_open", "Call d'une ouverture (flat)", G_VS_OPEN, "Tu paies une ouverture à ta première parole (cold "
        "call).", both("bb_vs_open.call", "flat"), **_refs(hu="bb_vs_open.call"))
    add("threebet", "3bet", G_VS_OPEN, "Tu relances une ouverture à ta première parole.",
        both("bb_vs_open.raise", "threebet"), **_refs(hu="bb_vs_open.raise", ring_key="threebet"))
    add("squeeze", "Squeeze", G_VS_OPEN, "Tu relances une ouverture déjà payée par un ou plusieurs joueurs.",
        {"ring": "squeeze"})
    add("fold_steal", "Fold face au vol", G_VS_OPEN, "En SB ou en BB, tu te couches face à une ouverture du CO, du "
        "bouton ou de la SB (sans caller).", both("bb_vs_open.fold", "fold_steal"),
        **_refs(hu="bb_vs_open.fold", ring_key="fold_steal"))
    add("call_steal", "Call face au vol", G_VS_OPEN, "En SB ou en BB, tu paies une ouverture du CO, du bouton ou de la "
        "SB.", both("bb_vs_open.call", "call_steal"))
    add("threebet_steal", "3bet face au vol", G_VS_OPEN, "En SB ou en BB, tu relances une ouverture du CO, du bouton "
        "ou de la SB.", both("bb_vs_open.raise", "threebet_steal"), **_refs(ring_key="threebet_steal"))
    for pos in ("SB", "BB"):
        hu = both("bb_vs_open.fold", "fold_steal") if pos == "BB" else {"ring": "fold_steal"}
        add(f"fold_steal_{pos.lower()}", f"Fold {pos} face au vol", G_VS_OPEN, f"Fold face au vol quand tu es {pos}.",
            hu, positions=(pos,))
        hu = both("bb_vs_open.raise", "threebet_steal") if pos == "BB" else {"ring": "threebet_steal"}
        add(f"threebet_steal_{pos.lower()}", f"3bet {pos} face au vol", G_VS_OPEN,
            f"3bet face au vol quand tu es {pos}.", hu, positions=(pos,))
    add("iso", "Iso-raise face à un limp", G_VS_OPEN, "Tu relances un ou plusieurs limps (personne n'a relancé).",
        both("bb_vs_limp.raise", "iso"), **_refs(hu="bb_vs_limp.raise"))
    add("overlimp", "Limp derrière un limp", G_VS_OPEN, "Tu paies derrière un ou plusieurs limps.",
        {"ring": "overlimp"})

    # Préflop — 3bet, 4bet, 5bet
    add("fold_3bet", "Fold face au 3bet", G_3BET, "Tu avais ouvert : tu te couches face au 3bet.",
        both("sb_vs_3bet.fold", "fold_3bet"), **_refs(hu="sb_vs_3bet.fold", ring_key="fold_3bet"))
    add("call_3bet", "Call du 3bet", G_3BET, "Tu avais ouvert : tu paies le 3bet.", both("sb_vs_3bet.call", "call_vs3"),
        **_refs(hu="sb_vs_3bet.call"))
    add("fourbet", "4bet", G_3BET, "Tu avais ouvert : tu relances le 3bet.", both("sb_vs_3bet.raise", "raise_vs3"),
        **_refs(hu="sb_vs_3bet.raise"))
    add("fourbet_any", "4bet (toutes situations)", G_3BET, "Tu relances un 3bet, que tu aies ouvert ou non (cold 4bet "
        "compris).", both("vs_3bet.raise", "fourbet_any"))
    add("fold_4bet", "Fold face au 4bet", G_3BET, "Tu avais 3betté : tu te couches face au 4bet.",
        both("bb_vs_4bet.fold", "fold_vs4"), **_refs(hu="bb_vs_4bet.fold"))
    add("call_4bet", "Call du 4bet", G_3BET, "Tu avais 3betté : tu paies le 4bet.", both("bb_vs_4bet.call", "call_vs4"))
    add("fivebet", "5bet", G_3BET, "Tu avais 3betté : tu relances le 4bet.", both("bb_vs_4bet.raise", "raise_vs4"),
        **_refs(hu="bb_vs_4bet.raise"))

    # Préflop — limp
    for option, label, what in (("fold", "Limp puis fold", "tu te couches"), ("call", "Limp puis call", "tu paies"),
                                ("raise", "Limp-relance", "tu relances")):
        add(f"limp_{option}", label, G_LIMP, f"Tu as limpé et quelqu'un relance : {what}.",
            both(f"sb_vs_iso.{option}", f"{option}_limp"), **_refs(hu=f"sb_vs_iso.{option}"))

    # Préflop — tailles
    add("open_size", "Taille de l'open", G_PRE_SIZE, "Ta relance d'ouverture, en big blinds (tapis exclus).",
        both("pf_open_bb", "open_bb"), kind="avg", unit="bb")
    add("threebet_size", "Taille du 3bet", G_PRE_SIZE, "Ton 3bet, en multiple de la relance qu'il suit (tapis exclus).",
        both("pf_3bet_x", "threebet_x"), kind="avg", unit="×")
    add("fourbet_size", "Taille du 4bet", G_PRE_SIZE, "Ton 4bet, en multiple du 3bet (tapis exclus).",
        both("pf_4bet_x", "fourbet_x"), kind="avg", unit="×")

    # Après le flop — à l'initiative
    add("cbet_f", "C-bet flop", G_PFA, "Dernier relanceur avant le flop, tu mises au flop quand personne n'a misé "
        "avant toi.", both("cbet_flop", "cbet_hu+cbet_multi"))
    add("cbet_f_ip", "C-bet flop en position", G_PFA, "C-bet flop quand tu parles le dernier.",
        both("cbet_flop_ip", "cbet_f_ip"), **_refs(hu="cbet_flop_ip"))
    add("cbet_f_oop", "C-bet flop hors de position", G_PFA, "C-bet flop quand un adversaire parle après toi.",
        both("cbet_flop_oop", "cbet_f_oop"), **_refs(hu="cbet_flop_oop"))
    add("cbet_f_hu", "C-bet flop, pot à deux", G_PFA, "C-bet flop quand vous êtes deux à voir le flop.",
        both("cbet_flop", "cbet_hu"), **_refs(ring_key="cbet_hu"))
    add("cbet_f_multi", "C-bet flop, pot à plusieurs", G_PFA,
        "C-bet flop quand vous êtes trois ou plus à voir le flop.",
        {"ring": "cbet_multi"})
    add("cbet_f_srp", "C-bet flop, pot simple", G_PFA, "C-bet flop après une seule relance avant le flop.",
        both("cbet_f_srp", "cbet_f_srp"))
    add("cbet_f_3bp", "C-bet flop, pot 3bet", G_PFA, "C-bet flop dans un pot 3betté.", both("cbet_f_3bp", "cbet_f_3bp"))
    add("cbet_t", "2e barrel (c-bet turn)", G_PFA, "Tu as fait la c-bet flop : tu mises encore la turn quand personne "
        "n'a misé avant toi.", both("cbet_turn", "cbet_turn"), **_refs(hu="cbet_turn", ring_key="cbet_turn"))
    add("cbet_r", "3e barrel (c-bet river)", G_PFA, "Tu as misé le flop et la turn : tu mises encore la river.",
        both("cbet_river", "cbet_river"), **_refs(hu="cbet_river"))
    add("delayed_cbet_t", "C-bet retardé turn", G_PFA, "Le flop s'est joué sans mise : dernier relanceur avant le "
        "flop, tu mises la turn.", both("delayed_cbet_turn", "delayed_cbet_t"), **_refs(hu="delayed_cbet_turn"))
    for short, street, _ in STREETS:
        add(f"fold_raise_{short}", f"Fold face à une relance après ta mise, {street}", G_ANY,
            f"Tu as misé ou relancé au {street} et on te relance : tu te couches.",
            both(f"vs_raise_{street}.fold", f"fold_raise_{short}"), **_refs(hu=f"vs_raise_{street}.fold"))

    # Après le flop — face à l'initiative
    for short, street, _ in STREETS:
        name = {"f": "c-bet flop", "t": "2e barrel", "r": "3e barrel"}[short]
        ring_fold = "fold_cbet" if short == "f" else f"fold_cbet_{short}"
        add(f"fold_cbet_{short}", f"Fold face à la {name}" if short == "f" else f"Fold face au {name}", G_DEF,
            f"Le dernier relanceur avant le flop mise ({name}) : tu te couches.",
            both(f"vs_cbet_{street}.fold", ring_fold),
            **_refs(hu=f"vs_cbet_{street}.fold", ring_key=ring_fold))
        add(f"call_cbet_{short}", f"Call de la {name}" if short == "f" else f"Call du {name}", G_DEF,
            f"Le dernier relanceur avant le flop mise ({name}) : tu paies.",
            both(f"vs_cbet_{street}.call", f"call_cbet_{short}"))
        add(f"raise_cbet_{short}", f"Relance de la {name}" if short == "f" else f"Relance du {name}", G_DEF,
            f"Le dernier relanceur avant le flop mise ({name}) : tu relances.",
            both(f"vs_cbet_{street}.raise", f"raise_cbet_{short}"), **_refs(hu=f"vs_cbet_{street}.raise"))
    add("donk_f", "Donk flop", G_DEF, "Tu mises au flop avant le dernier relanceur préflop, qui parle après toi.",
        both("donk_flop", "donk_f"), **_refs(hu="donk_flop"))
    add("donk_t", "Donk turn", G_DEF, "Tu as payé sa c-bet flop : tu mises la turn avant lui.",
        both("donk_turn", "donk_t"))
    add("stab_f", "Mise quand l'initiative checke, flop", G_DEF, "Le dernier relanceur préflop checke le flop : tu "
        "mises (« bet vs missed c-bet »).", both("float_flop", "stab_flop"),
        **_refs(hu="float_flop", ring_key="stab_flop"))
    add("stab_t", "Mise quand l'initiative renonce, turn", G_DEF, "Tu as payé sa c-bet flop et il checke la turn : tu "
        "mises (float).", both("float_turn", "stab_t"))
    add("probe_t", "Probe turn", G_DEF, "Le flop s'est joué sans mise : tu mises la turn avant le dernier relanceur "
        "préflop.", both("probe_turn", "probe_t"), **_refs(hu="probe_turn"))
    add("probe_r", "Probe river", G_DEF, "La turn s'est jouée sans mise : tu mises la river avant le dernier relanceur "
        "préflop.", both("probe_r", "probe_r"))

    # Après le flop — toutes situations
    for short, street, _ in STREETS:
        add(f"bet_{short}", f"Mise au {street}", G_ANY, f"Personne n'a misé au {street} quand tu parles : tu mises.",
            both(f"bet_{short}", f"bet_{short}"))
    for short, street, _ in STREETS:
        ring_fold = {"f": "fold_bet_f", "t": "fold_turn", "r": "fold_river"}[short]
        add(f"fold_bet_{short}", f"Fold face à une mise, {street}", G_ANY,
            f"On mise au {street} (première mise) : tu te couches.", both(f"vs_bet_{street}.fold", ring_fold),
            **_refs(hu=f"vs_bet_{street}.fold", ring_key=ring_fold))
        add(f"call_bet_{short}", f"Call d'une mise, {street}", G_ANY,
            f"On mise au {street} (première mise) : tu paies.",
            both(f"vs_bet_{street}.call", f"call_bet_{short}"))
        add(f"raise_bet_{short}", f"Relance d'une mise, {street}", G_ANY,
            f"On mise au {street} (première mise) : tu relances.", both(f"vs_bet_{street}.raise", f"raise_bet_{short}"))
    add("fold_bet", "Fold face à une mise, toutes streets", G_ANY,
        "Face à une première mise, au flop, à la turn ou à la river : tu te couches.",
        both("vs_bet_flop.fold+vs_bet_turn.fold+vs_bet_river.fold", "fold_bet_f+fold_turn+fold_river"))
    for short, street, _ in STREETS:
        ring_xr = "xr_flop" if short == "f" else f"xr_{short}"
        add(f"xr_{short}", f"Check-raise {street}", G_ANY, f"Tu as checké le {street}, on mise : tu relances.",
            both(f"xr_{street}", ring_xr), **_refs(hu=f"xr_{street}", ring_key=ring_xr))
        add(f"xc_{short}", f"Check-call {street}", G_ANY, f"Tu as checké le {street}, on mise : tu paies.",
            both(f"xc_{short}", f"xc_{short}"))
        add(f"xf_{short}", f"Check-fold {street}", G_ANY, f"Tu as checké le {street}, on mise : tu te couches.",
            both(f"xf_{short}", f"xf_{short}"))

    # Après le flop — tailles
    for short, street, _ in STREETS:
        name = {"f": "c-bet flop", "t": "2e barrel", "r": "3e barrel"}[short]
        add(f"size_cbet_{short}", f"Taille {'de la' if short == 'f' else 'du'} {name}", G_SIZE,
            f"Ta mise ({name}), en % du pot.", both(f"cbet_{street}", f"size_cbet_{short}"), kind="avg",
            unit="% du pot")
    for short, street, _ in STREETS:
        add(f"size_bet_{short}", f"Taille des mises, {street}", G_SIZE, f"Tes mises au {street} (toutes), en % du pot.",
            both(f"bet_{street}", f"size_bet_{short}"), kind="avg", unit="% du pot")
    for short, street, _ in STREETS:
        add(f"size_raise_{short}", f"Taille des relances, {street}", G_SIZE,
            f"Tes relances au {street}, en multiple de la mise relancée.",
            both(f"raise_{street}_x", f"size_raise_{short}"), kind="avg", unit="×")

    # Agressivité
    add("af", "Facteur d'agression (AF)", G_AGGR, "Après le flop : (mises + relances) / calls.", both("*", "*"),
        kind="af")
    for short, street, _ in STREETS:
        add(f"af_{short}", f"AF {street}", G_AGGR, f"Au {street} : (mises + relances) / calls.", both(short, short),
            kind="af")
    add("afq", "Fréquence d'agression (AFq)", G_AGGR, "Après le flop : part des mises et relances parmi tes mises, "
        "relances, calls et folds.", both("*", "*"), kind="afq", **_refs(ring_key="afq"))
    for short, street, _ in STREETS:
        add(f"afq_{short}", f"AFq {street}", G_AGGR, f"Au {street} : part des mises et relances parmi tes actions.",
            both(short, short), kind="afq")

    # Abattage
    add("wtsd", "WTSD (va à l'abattage)", G_SD, "Quand tu vois le flop, tu vas à l'abattage.", both("wtsd", "wtsd"),
        **_refs(hu="wtsd", ring_key="wtsd"))
    add("wsd", "W$SD (gagne à l'abattage)", G_SD, "Quand tu vas à l'abattage, tu gagnes.", both("wsd", "wsd"),
        **_refs(hu="wsd", ring_key="wsd"))
    add("wwsf", "WWSF (gagne après le flop)", G_SD, "Quand tu vois le flop, tu gagnes le pot (abattage ou non).",
        both("wwsf", "wwsf"), **_refs(ring_key="wwsf"))
    return out


CATALOG = _catalog()
BY_ID = {s.id: s for s in CATALOG}

# Ta sélection par défaut : les stats que l'onglet montrait jusqu'ici.
DEFAULTS = {
    "HU": ["hands", "bb100", "vpip", "pfr", "vpip_btn", "vpip_bb", "saw_flop", "rfi", "limp", "fold_rfi", "fold_open",
           "call_open", "threebet", "iso", "fold_3bet", "call_3bet", "fourbet", "fold_4bet", "fivebet", "limp_fold",
           "limp_raise", "cbet_f_ip", "cbet_f_oop", "cbet_t", "cbet_r", "delayed_cbet_t", "fold_cbet_f",
           "raise_cbet_f", "fold_cbet_t", "fold_cbet_r", "donk_f", "stab_f", "probe_t", "xr_f", "xr_t", "fold_bet_f",
           "fold_bet_t", "fold_bet_r", "fold_raise_f", "wtsd", "wsd", "wwsf"],
    "ring": ["hands", "bb100", "vpip", "pfr", "rfi", "limp", "threebet", "call_open", "fold_steal", "threebet_steal",
             "fold_3bet", "cbet_f_hu", "cbet_f_multi", "cbet_t", "fold_cbet_f", "stab_f", "fold_bet_t", "fold_bet_r",
             "xr_f", "afq", "wtsd", "wsd", "wwsf"],
}
# Dans l'Étude du field, par défaut : les stats que sa page montrait jusqu'ici.
FIELD_DEFAULTS = {
    "HU": DEFAULTS["HU"],
    "ring": ["hands", "bb100", "vpip", "pfr", "rfi", "limp", "threebet", "call_open", "fold_steal", "threebet_steal",
             "fold_3bet", "cbet_f_hu", "cbet_f_multi", "cbet_t", "fold_cbet_f", "stab_f", "fold_bet_t", "fold_bet_r",
             "xr_f", "fold_bet", "afq", "wtsd", "wsd", "wwsf"],
}
MAX_SELECTED = len(CATALOG)


def available(table_format: str) -> list[Stat]:
    """Les stats du catalogue qui ont un sens dans ce format (« HU » ou « ring »)."""
    return [s for s in CATALOG if table_format in s.formats()]


def selection(table_format: str, chosen: Optional[dict] = None, defaults: Optional[dict] = None) -> list[Stat]:
    """Tes stats dans ce format, dans ton ordre (chosen : settings stat_selection ou field_stat_selection ; sans choix
    pour ce format : defaults, DEFAULTS par défaut)."""
    ids = (chosen or {}).get(table_format)
    if ids is None:
        ids = (defaults or DEFAULTS)[table_format]
    return [BY_ID[i] for i in ids if i in BY_ID and table_format in BY_ID[i].formats()]


# --- Le total sur des mains -----------------------------------------------------------------------------------------

@dataclass
class Tally:
    """Ce que des mains disent d'un joueur (ou d'un groupe) : ses occasions (opps) et ce qu'il en a fait (hits), ses
    valeurs (somme et nombre), ses actions après le flop et ses résultats (bb)."""
    hands: int = 0
    net_bb: float = 0.0
    ev_bb: float = 0.0
    sd_bb: float = 0.0
    nsd_bb: float = 0.0
    rake_bb: float = 0.0
    opps: Counter = field(default_factory=Counter)
    hits: Counter = field(default_factory=Counter)
    sums: dict = field(default_factory=lambda: defaultdict(float))
    samples: Counter = field(default_factory=Counter)
    counts: Counter = field(default_factory=Counter)

    def ratio(self, key: str) -> Ratio:
        return Ratio(self.hits[key], self.opps[key])

    def merge(self, other: "Tally") -> None:
        for name in ("hands", "net_bb", "ev_bb", "sd_bb", "nsd_bb", "rake_bb"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for mine, theirs in ((self.opps, other.opps), (self.hits, other.hits), (self.samples, other.samples),
                             (self.counts, other.counts)):
            mine.update(theirs)
        for key, total in other.sums.items():
            self.sums[key] += total

    def add(self, hand: Hand, player: str, table_format: str) -> None:
        """Une main de ce joueur."""
        old, sizes = _old_events(hand, player, table_format)
        new = read(hand).get(player) or _NONE
        net = hand.net(player) / hand.bb
        ev = allin_ev(hand)
        self.hands += 1
        self.net_bb += net
        self.ev_bb += ev[player] / hand.bb if ev is not None else net
        if hand.showdown:
            self.sd_bb += net
        else:
            self.nsd_bb += net
        self.rake_bb += rake_paid(hand, player) / hand.bb
        for events in (old, new.ratios):
            self.opps.update(key for key, _ in events)
            self.hits.update(key for key, made in events if made)
        for values in (sizes, new.values):
            for key, x in values:
                self.sums[key] += x
                self.samples[key] += 1
        self.counts.update(new.counts)


_NONE = Events()


@dataclass
class Table:
    """Les stats d'un joueur (ou d'un groupe) sur des mains : toutes positions (total), puis position par
    position."""
    total: Tally
    positions: dict[str, Tally]

    def scope(self, positions: tuple = ()) -> Tally:
        """Le total, ou seulement les mains à ces positions."""
        if not positions:
            return self.total
        out = Tally()
        for pos in positions:
            if pos in self.positions:
                out.merge(self.positions[pos])
        return out


def _old_events(hand: Hand, player: str, table_format: str) -> tuple[list, list]:
    """Les stats lues ailleurs dans l'application (mêmes chiffres que le bilan et le Leakfinding)."""
    if table_format == "HU":
        reader = HandReader.of(hand)
        saw = reader.saw_flop(player)
        extra = [("saw_flop", saw)]
        if saw:
            extra += [("wtsd", hand.showdown), ("wwsf", hand.winnings.get(player, 0.0) > 0)]
            if hand.showdown:
                extra.append(("wsd", hand.winnings.get(player, 0.0) > 0))
        return reader.events.get(player, []) + extra, reader.sizes.get(player, [])
    found = list(ring.read(hand, player).items()) + list(field_stats.postflop_read(hand, player).items())
    return found, []


def _usable(hand: Hand, table_format: str) -> bool:
    if not hand.bb or (hand.size <= 2) != (table_format == "HU"):
        return False
    return table_format != "HU" or bool(hand.button and hand.big_blind)


def tally_group(hands: Iterable[Hand], names: Iterable[str], table_format: str) -> Table:
    """Les stats de ces joueurs ensemble sur ces mains (celles de ce format : « HU » ou « ring ») : chaque main compte
    une fois pour chacun d'eux qui y est servi ; toutes positions, puis position par position."""
    names = set(names)
    by_pos: dict[str, Tally] = {}
    for h in hands:
        here = names & h.seats.keys()
        if not here or not _usable(h, table_format):
            continue
        for player in here:
            pos = h.position(player) or "?"
            at = by_pos.get(pos)
            if at is None:
                at = by_pos[pos] = Tally()
            at.add(h, player, table_format)
    total = Tally()
    for at in by_pos.values():
        total.merge(at)
    return Table(total, by_pos)


def tally(hands: Iterable[Hand], player: str, table_format: str) -> Table:
    """Les stats de ce joueur sur ces mains (celles de ce format : « HU » ou « ring »), toutes positions puis
    position par position."""
    return tally_group(hands, (player,), table_format)


# --- La valeur d'une stat -------------------------------------------------------------------------------------------

@dataclass
class Value:
    """La valeur d'une stat : value (None : pas d'occasion), sample (occasions, mains ou actions), ratio (les
    fréquences, pour leur intervalle de confiance)."""
    value: Optional[float]
    sample: int
    ratio: Optional[Ratio] = None


def value(stat: Stat, table_format: str, table: Table, positions: Optional[tuple] = None) -> Value:
    """La valeur de cette stat sur ces mains (positions : seulement celles-là, en plus de celles de la stat)."""
    wanted = stat.positions
    if positions:
        wanted = tuple(p for p in positions if not stat.positions or p in stat.positions)
        if not wanted:
            return Value(None, 0)
    t = table.scope(wanted)
    src = stat.src.get(table_format)
    if src is None:
        return Value(None, 0)
    if stat.kind == "hands":
        return Value(float(t.hands), t.hands)
    if stat.kind == "result":
        return Value(100.0 * getattr(t, src) / t.hands if t.hands else None, t.hands)
    if stat.kind in ("af", "afq"):
        shorts = [s for s, _, _ in STREETS] if src == "*" else [src]
        aggr = sum(t.counts[f"aggr_{s}"] for s in shorts)
        calls = sum(t.counts[f"{CALL}_{s}"] for s in shorts)
        folds = sum(t.counts[f"{FOLD}_{s}"] for s in shorts)
        if stat.kind == "af":
            return Value(aggr / calls if calls else None, aggr + calls)
        r = Ratio(aggr, aggr + calls + folds)
        return Value(r.pct, r.opps, r)
    if stat.kind == "avg":
        n = t.samples[src]
        return Value(t.sums[src] / n if n else None, n)
    keys = src.split("+")
    r = Ratio(sum(t.hits[k] for k in keys), sum(t.opps[k] for k in keys))
    return Value(r.pct, r.opps, r)


def stat_def(stat: Stat, table_format: str, ref: Optional[tuple] = None) -> StatDef:
    """Pour colorer une fréquence face à son repère (report.pct_cell) : ref, sinon celui de la stat."""
    return StatDef(stat.id, stat.label, ref if ref is not None else stat.refs.get(table_format))


def field_ref(stat: Stat, table_format: str, band: Optional[str] = None) -> Optional[tuple]:
    """Le repère d'une stat dans l'Étude du field : celui d'un régulier solide de ce niveau de limites (stakes.BANDS ;
    ceux de field.RING_DEFS d'abord aux tables à plusieurs, comme ses autres pages), sinon celui de la stat."""
    key = stat.ref_keys.get(table_format)
    base = stat.refs.get(table_format)
    known = field_stats.RING_DEF.get(key) if table_format == "ring" and key else None
    if known is not None and known.ref:
        base = known.ref
    if base is None or not key or not band:
        return base
    return (field_stats.HU_BAND_REFS if table_format == "HU" else field_stats.BAND_REFS).get(band, {}).get(key, base)
