"""Leakfinding aux tables à plusieurs (6-max, 3-max) : ce qu'un joueur doit travailler en priorité dans ce format,
avec les briques du heads-up (analyzer/leaks.py).

1. Préflop, position par position : ses décisions face à ses charts du format (ring_ranges), avec les mêmes cartes —
   l'open quand il parle le premier, le fold, le call et le 3bet face à une ouverture, le fold et le 4bet face au 3bet
   après son open. La fréquence des charts est celle d'un joueur qui les suivrait avec les cartes reçues. Sans
   charts : les repères indicatifs d'un régulier 6-max pour l'open (ring.py).
2. Après le flop, dans les pots à deux joueurs : ses fréquences (c-bet, barrels, folds face aux mises, relances,
   probes), par structure de pot (SRP, pot 3bet, pot 4bet ; l'agresseur en position ou non), face aux plans de jeu
   des flops 6-max résolus de même structure (Études du solveur › 6-max).
3. Ses plus gros pots à deux au flop, deux par ligne (pot, positions, dernière street) : passés au solveur, l'EV
   perdue par situation et ses décisions les plus chères.
4. Les mains de départ qui perdent nettement plus que le fold.

Toutes ses mains comptent : à une table à plusieurs, pas de tri entre réguliers et récréatifs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional
from urllib.parse import urlencode

from . import handplay, ring
from .leaks import HAND_LEAKS, POT_STAKE, Pick, Report, Stat, _hand_leak, rank, street_stake
from .models import CALL, FOLD, RAISE, VOLUNTARY, Hand
from .stats import HandReader, Ratio
from .theory import coach, exploit, postflop, review, studyspots

SCOPES = (("all", "Toutes ses mains"),)
POTS = {1: "srp", 2: "3bet", 3: "4bet"}  # relances préflop -> type de pot
STRUCTURES = (  # qui a l'initiative après le flop, et où
    ("srp_ip", "SRP, l'ouvreur en position"),
    ("srp_oop", "SRP, l'ouvreur hors de position"),
    ("3bet_oop", "Pot 3bet, le 3bettor hors de position"),
    ("3bet_ip", "Pot 3bet, le 3bettor en position"),
    ("4bet_ip", "Pot 4bet, le 4bettor en position"),
    ("4bet_oop", "Pot 4bet, le 4bettor hors de position"),
)
ROLES = {"agresseur": "À l'initiative", "defenseur": "En défense"}

# Préflop : situation -> titre, puis (groupe d'actions de handplay.GROUPS, libellé, sens, poids de la décision)
PREFLOP = (
    ("open", "Préflop · premier à parler", ((0, "open", "open", 1.0), (1, "limp", "limp", 1.0))),
    ("vs_open", "Préflop · face à une ouverture", ((2, "fold", "fold", 1.0), (1, "call", "call", 1.0),
                                                   (0, "3bet", "aggr", 1.5))),
    ("vs_3bet", "Préflop · face au 3bet, après l'open", ((2, "fold", "fold", 2.0), (0, "4bet", "aggr", 2.0))),
)
ADVICE = {  # sens -> (plus que les charts, moins que les charts)
    "open": ("tu ouvres trop large à cette position : resserre", "tu ouvres trop serré à cette position : élargis"),
    "limp": ("tu limpes : ouvre ou folde ces mains", "pas assez de limps"),
    "fold": ("tu foldes trop : défends plus large", "tu ne foldes pas assez : lâche tes mains les plus faibles"),
    "call": ("tu paies trop : relance ou folde une partie de ces mains", "tu ne paies pas assez : garde plus de mains "
                                                                          "en call"),
    "aggr": ("tu relances trop : garde la relance pour les mains qui la jouent", "tu ne relances pas assez : ajoute "
                                                                                 "des relances (valeur et bluffs)"),
}


# --- Les pots à deux joueurs au flop ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Pot:
    """Un pot à deux joueurs au flop, après une ligne préflop simple : open, 3bet, 4bet, puis call."""
    oop: str
    ip: str
    kind: str            # srp, 3bet, 4bet
    aggressor: str       # le dernier relanceur préflop
    positions: tuple[str, str]  # (hors de position, en position)

    @property
    def structure(self) -> str:
        """« srp_ip » : le type de pot, et l'agresseur en position (ip) ou non (oop), comme sizing.STRUCTURES."""
        return f"{self.kind}_{'oop' if self.aggressor == self.oop else 'ip'}"

    @property
    def family(self) -> str:
        """« 6max_bb_co_srp » : la famille d'étude de ces positions, qu'elle soit étudiée ou non (studyspots)."""
        return f"6max_{self.positions[0].lower()}_{self.positions[1].lower()}_{self.kind}"


def pot_of(hand: Hand) -> Optional[Pot]:
    """Le pot à deux joueurs au flop d'une main à une table à plusieurs, ou None : pot à plusieurs, limpé, tapis avant
    le flop, troisième joueur qui a mis de l'argent avant de se coucher…"""
    if len(hand.seats) <= 2:
        return None
    pair = postflop.flop_pair(hand)
    if pair is None:
        return None
    pre = [a for a in hand.actions if a.street == "preflop" and a.kind in VOLUNTARY and a.kind != FOLD]
    raises = sum(a.kind == RAISE for a in pre)
    if (raises not in POTS or [a.kind for a in pre] != [RAISE] * raises + [CALL] or any(a.all_in for a in pre)
            or any(a.player not in pair for a in pre) or any(x.player == y.player for x, y in zip(pre, pre[1:]))):
        return None
    oop, ip = pair
    return Pot(oop, ip, POTS[raises], pre[-2].player, (hand.position(oop), hand.position(ip)))


def _view(hand: Hand, pot: Pot) -> Hand:
    """La main réduite aux deux joueurs du flop, lue comme un heads-up (celui en position au bouton) : HandReader y
    trouve leurs décisions après le flop (c-bet, barrels, réponses aux mises…)."""
    seats = {pot.oop: replace(hand.seats[pot.oop], is_button=False),
             pot.ip: replace(hand.seats[pot.ip], is_button=True)}
    actions = [a for a in hand.actions if a.player in seats and (a.street != "preflop" or a.kind in VOLUNTARY)]
    return Hand(hand.site, hand.hand_id, hand.table_id, hand.game_name, hand.date, hand.sb, hand.bb, hand.total_pot,
                hand.rake, 2, seats, {}, list(hand.board), actions)


def _families(structure: str) -> list[str]:
    return [f for f, info in studyspots.RING_FAMILIES.items() if info["structure"] == structure]


# --- 1. Préflop face aux charts ------------------------------------------------------------------------------------

def preflop_stats(plays: list[handplay.Played], table_format: str, total: int) -> list[Stat]:
    """Ses décisions préflop, position par position, face à ses charts avec les mêmes cartes (ou, sans charts, aux
    repères indicatifs de l'open en 6-max)."""
    cells: dict[tuple[str, str], dict] = {}
    for p in plays:
        for d in p.decisions:
            if d.action not in handplay.GROUPS:
                continue
            c = cells.setdefault((d.situation, p.position), {"n": [0, 0, 0], "t": [0, 0, 0], "exp": [0.0, 0.0, 0.0]})
            group = handplay.GROUPS[d.action]
            c["n"][group] += 1
            if d.theory is not None:  # la décision d'un joueur qui suivrait ses charts, avec cette main
                c["t"][group] += 1
                for action, freq in d.theory.items():
                    if action in handplay.GROUPS:
                        c["exp"][handplay.GROUPS[action]] += freq
    out = []
    for situation, section, rows in PREFLOP:
        positions = sorted({pos for sit, pos in cells if sit == situation}, key=handplay.position_order)
        for position in positions:
            c = cells[(situation, position)]
            n_all, n_charts = sum(c["n"]), sum(c["t"])
            for group, word, kind, stake in rows:
                if n_charts:
                    ratio, reference = Ratio(c["t"][group], n_charts), c["exp"][group] / n_charts
                else:
                    ratio, reference = Ratio(c["n"][group], n_all), None
                band = None
                if reference is None and situation == "open" and group == 0 and table_format == "6-max":
                    found = ring.OPEN_6MAX.get(position)
                    band = (found[0] / 100, found[1] / 100) if found else None
                if not ratio.hits and reference is None and band is None:
                    continue  # ni fait, ni repère : rien à dire
                if not ratio.hits and reference is not None and reference < 0.005:
                    continue  # jamais fait, et les charts non plus (les limps d'un joueur qui n'en fait pas)
                note = ""
                if n_charts and n_charts < n_all:
                    note = (f"comparé sur ses {n_charts} décisions que ses charts couvrent, sur {n_all} : les mains "
                            "hors de ses charts d'open n'y comptent pas")
                elif band is not None:
                    note = "repère indicatif d'un régulier 6-max : charge tes charts pour comparer main par main"
                more, less = ADVICE[kind]
                out.append(Stat(f"pre:{situation}:{position}:{word}", section, f"{position} : {word}", kind,
                                {"all": ratio}, reference, note, per100=100 * ratio.opps / max(total, 1), band=band,
                                versus="ses charts avec les mêmes cartes", advice={"plus": more, "moins": less},
                                stake=stake, exact=reference is not None,
                                link=("mains#" + urlencode({"fmt": "ring", "pos": position, "sit": situation}),
                                      "Voir ses mains de départ")))
    return out


# --- 2. Après le flop, face aux plans de jeu des flops 6-max -----------------------------------------------------

def _baselines(families: list[str], role: str) -> dict[str, tuple[float, int]]:
    """Le repère du solveur de chaque situation : la moyenne des plans de jeu des flops résolus de ces familles."""
    sums: dict[str, list[float]] = {}
    for family in families:
        for key, (value, flops) in exploit.baselines(family, role).items():
            acc = sums.setdefault(key, [0.0, 0])
            acc[0] += value * flops
            acc[1] += flops
    return {key: (value / n, int(n)) for key, (value, n) in sums.items() if n}


def postflop_stats(hands: list[Hand], hero: str, table_format: str, pots: dict[str, Pot]) -> list[Stat]:
    """Ses fréquences dans les pots à deux au flop, par structure de pot et par rôle, face aux plans de jeu des flops
    6-max résolus de même structure."""
    seen: dict[tuple[str, str], exploit.Ratios] = {}
    for h in hands:
        pot = pots.get(h.hand_id)
        if pot is None:
            continue
        role = "agresseur" if hero == pot.aggressor else "defenseur"
        st = seen.setdefault((pot.structure, role), exploit.Ratios())
        for key, made in HandReader(_view(h, pot)).events.get(hero, []):
            st.ratios[key].add(made)
    out = []
    for structure, title in STRUCTURES:
        families = _families(structure)
        pairs = ", ".join(studyspots.RING_FAMILIES[f]["pair"] for f in families)
        for role in ROLES:
            st = seen.get((structure, role))
            if st is None:
                continue
            base = _baselines(families, role) if table_format == studyspots.RING_FORMAT else {}
            for s in exploit.situations(families[0], role):
                hits, opps = exploit.tally(st, s)
                if not opps:
                    continue
                ref, flops = base.get(s.key, (None, 0))
                if ref is None:
                    note = (f"pas encore de repère du solveur : résous des flops 6-max ({pairs})"
                            if table_format == studyspots.RING_FORMAT else f"pas de repère du solveur en {table_format}")
                else:
                    note = f"repère tiré de {flops} flop(s) résolu(s) : {pairs}" if flops < exploit.MIN_FLOPS else ""
                out.append(Stat(f"{structure}:{role}:{s.key}", title, f"{ROLES[role]} : {s.label}",
                                "bet" if s.measure == "bet" else s.measure, {"all": Ratio(hits, opps)}, ref, note,
                                per100=100 * opps / max(len(hands), 1),
                                fragile=ref is not None and flops < exploit.MIN_FLOPS,
                                stake=street_stake(s.stats[0]) * POT_STAKE[structure.split("_")[0]],
                                link=(f"plan#famille={families[0]}", "Voir le plan de jeu 6-max")))
    return out


# --- 3. Face au solveur ---------------------------------------------------------------------------------------------

def _relabel(digest: dict, hand: Hand, pot: Pot, hero: str) -> dict:
    """Le résumé d'une main passée au solveur, ses situations nommées avec les vraies positions (le résumé les nomme
    comme en heads-up : la BB hors de position, le bouton en position) et rangées par pot et positions."""
    profile = pot.family if pot.family in studyspots.RING_FAMILIES else _families(pot.structure)[0]
    decisions = [dict(d, label=review.situation_label(d["key"], profile, pot.positions)) for d in digest["decisions"]]
    return dict(digest, family=pot.family, hero_position=hand.position(hero), decisions=decisions)


def solver_review(hands: list[Hand], hero: str, pots: dict[str, Pot]) -> dict:
    eligible = [h for h in hands if h.hand_id in pots]
    digests, todo = review.collect(eligible, hero)
    by_id = {h.hand_id: h for h in eligible}
    digests = [_relabel(g, by_id[g["hand"]], pots[g["hand"]], hero) for g in digests if g.get("hand") in by_id]
    known = [review.loss(d) for _, d in review.decisions_of(digests, "H") if d["ev_loss"] is not None]
    return {"digests": digests, "todo": todo, "groups": review.by_situation(digests, "H"),
            "costly": review.costly(digests, "H", 10), "lost": sum(known), "decisions": len(known),
            "analyzed": len(digests)}


def line_of(hand: Hand, hero: str, pot: Pot) -> str:
    """« SRP · CO c. BB · river » : type de pot, sa position contre celle de l'adversaire, dernière street jouée."""
    villain = pot.ip if hero == pot.oop else pot.oop
    streets = [s for s in ("flop", "turn", "river") if any(a.street == s for a in hand.actions)]
    return (f"{studyspots.POT_NAMES[pot.kind]} · {hand.position(hero)} c. {hand.position(villain)} · "
            f"{streets[-1] if streets else 'flop'}" + (" · abattage" if hand.showdown else ""))


def interesting(hands: list[Hand], hero: str, pots: dict[str, Pot], digests: list[dict], per_line: int = 2,
                limit: int = 24) -> list[Pick]:
    """Les plus gros pots à deux de chaque ligne (per_line par ligne), jusqu'à limit mains : à passer au solveur."""
    solved = {d["hand"]: d for d in digests}
    lines: dict[str, list[Pick]] = {}
    for h in hands:
        pot = pots.get(h.hand_id)
        if pot is None:
            continue
        line = line_of(h, hero, pot)
        lines.setdefault(line, []).append(Pick(h, "all", line, round(h.total_pot / h.bb, 1),
                                               round(h.net(hero) / h.bb, 1), solved.get(h.hand_id)))
    chosen = []
    for picks in sorted(lines.values(), key=lambda ps: -max(p.pot_bb for p in ps)):
        chosen.extend(sorted(picks, key=lambda p: -p.pot_bb)[:per_line])
    chosen = sorted(chosen, key=lambda p: -p.pot_bb)[:limit]
    for p in chosen:
        if p.digest is None:
            try:
                p.spot = postflop.build_spot(p.hand, hero)
            except postflop.Unsupported as exc:
                p.why = str(exc)
    return chosen


# --- Le rapport -----------------------------------------------------------------------------------------------------

def _mine(hands: list[Hand], hero: str, table_format: str) -> list[Hand]:
    return [h for h in hands if h.table_format == table_format and hero in h.seats and h.bb and h.button]


def _pots(hands: list[Hand], hero: str) -> dict[str, Pot]:
    """Ses pots à deux joueurs au flop (ligne préflop simple), par main."""
    out = {}
    for h in hands:
        pot = pot_of(h)
        if pot is not None and hero in (pot.oop, pot.ip):
            out[h.hand_id] = pot
    return out


def stats(hands: list[Hand], hero: str, table_format: str, plays: Optional[list] = None,
          pots: Optional[dict[str, Pot]] = None) -> list[Stat]:
    """Ses stats face à la théorie, sans le solveur : préflop face aux charts, après le flop face aux plans de jeu
    (hands : toutes ses mains ; seules celles du format comptent)."""
    hands = _mine(hands, hero, table_format)
    if pots is None:
        pots = _pots(hands, hero)
    if plays is None:
        plays = handplay.collect(hands, hero, handplay.Theory(None, handplay.ring_lines((table_format,))))
    return preflop_stats(plays, table_format, len(hands)) + postflop_stats(hands, hero, table_format, pots)


def build(hands: list[Hand], hero: str, table_format: str) -> Report:
    """Le rapport d'un joueur à ce format de table (« 6-max », « 3-max »), sur ses mains de ce format."""
    hands = _mine(hands, hero, table_format)
    pots = _pots(hands, hero)
    solver = solver_review(hands, hero, pots)
    lines = handplay.ring_lines((table_format,))
    plays = handplay.collect(hands, hero, handplay.Theory(None, lines), review.hero_losses(solver["digests"]))
    rows = stats(hands, hero, table_format, plays, pots)
    hand_leaks = [_hand_leak(x, len(hands), "all", "ring") for x in handplay.losers(plays)[:HAND_LEAKS]]
    found = next((fs for fs in ring.analyze(hands, hero) if fs.table_format == table_format), None)
    info = {"all": {k: found.total.ratios[k] if found else Ratio() for k in ("vpip", "pfr", "wtsd", "wsd")}}
    plans = sum(len(coach.plans(f)) for f in studyspots.RING_FAMILIES) if table_format == studyspots.RING_FORMAT else 0
    return Report(hero, len(hands), {"all": len(hands)}, {"all": found.total.bb100 if found else None}, info, rows,
                  solver, {"all": interesting(hands, hero, pots, solver["digests"])},
                  rank(rows, solver["groups"], solver["analyzed"], digests=solver["digests"], extra=hand_leaks,
                       scope="all"),
                  table_format=table_format, scopes=SCOPES, solver_scope="all",
                  context={"charts": bool(lines.get(table_format)), "plans": plans, "pots": len(pots)})
