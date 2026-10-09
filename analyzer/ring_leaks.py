"""Leakfinding aux tables à plusieurs : toutes les tables de 3 joueurs et plus ensemble (3-max, 6-max, 7 à 9
joueurs), avec les briques et la présentation du heads-up (analyzer/leaks.py) — sur toutes ses mains, contre les
réguliers et contre les récréatifs.

Une main compte « contre les récréatifs » quand un récréatif a mis de l'argent dans le pot de lui-même pendant que le
joueur y était encore (versus) ; sinon « contre les réguliers ». Seules les mains contre les réguliers font des leaks
et passent au solveur : contre un récréatif, l'exploitation prime.

1. Préflop, position par position : ses décisions face aux charts (ring_ranges), avec les mêmes cartes — l'open
   quand il parle le premier, le fold, le call et le 3bet face à une ouverture, le fold et le 4bet face au 3bet après
   son open. Chaque décision est jugée par les charts de sa table : les siens, sinon ceux du 6-max à même nombre de
   joueurs derrière (ring_ranges.chart_mapping). La fréquence des charts est celle d'un joueur qui les suivrait avec
   les cartes reçues. Sans charts : les repères indicatifs de l'open en 6-max (ring.py).
2. Après le flop, dans les pots à deux joueurs : ses fréquences (c-bet, barrels, folds face aux mises, relances,
   probes), par structure de pot (SRP, pot 3bet, pot 4bet ; l'agresseur en position ou non), face aux plans de jeu
   des flops 6-max résolus de même structure (Études du solveur › 6-max).
3. Ses plus gros pots à deux au flop contre les réguliers, deux par ligne (pot, positions, dernière street) : passés
   au solveur, l'EV perdue par situation et ses décisions les plus chères ; contre les récréatifs, à revoir à la main.
4. Les mains de départ qui perdent nettement plus que le fold, contre les réguliers.
5. Chaque écart net est vérifié en jeu (analyzer/leakcheck.py) : ce qu'il rapporte ou coûte sur ses mains, la
   réponse de ses adversaires (dans ses pots à deux, et leurs fréquences préflop face aux repères d'un régulier
   solide), le plan de jeu suggéré pour la c-bet.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional
from urllib.parse import urlencode

from . import handplay, leakcheck, ring
from .leaks import HAND_LEAKS, POT_STAKE, SCOPES, Pick, Report, Stat, _hand_leak, chances, kept, rank, street_stake
from .models import BET, CALL, FOLD, RAISE, VOLUNTARY, Hand
from .stats import HandReader, Ratio
from .theory import coach, exploit, postflop, review, studyspots

FORMAT = "ring"   # le format des tables à plusieurs dans l'application (« ?format=ring »)
LABEL = ring.MERGED
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


# --- Contre qui ---------------------------------------------------------------------------------------------------

def versus(hand: Hand, hero: str, kinds: dict) -> str:
    """« rec » si un récréatif a mis de l'argent dans le pot de lui-même (call, relance, mise) pendant que le joueur y
    était encore, sinon « reg »."""
    for a in hand.actions:
        if a.player == hero:
            if a.kind == FOLD:
                break
        elif a.kind in (CALL, RAISE, BET) and kinds.get(a.player, {}).get("kind") == "rec":
            return "rec"
    return "reg"


def split(hands: list[Hand], hero: str, kinds: dict) -> dict[str, list[Hand]]:
    """Ses mains, toutes puis contre les réguliers et contre les récréatifs (versus)."""
    out = {"all": list(hands), "reg": [], "rec": []}
    for h in hands:
        out[versus(h, hero, kinds)].append(h)
    return out


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
    le flop, troisième joueur qui a mis de l'argent avant de se coucher… (gardé sur la main : les pages le relisent)"""
    if "_pot" not in hand.__dict__:
        hand.__dict__["_pot"] = _pot_of(hand)
    return hand.__dict__["_pot"]


def _pot_of(hand: Hand) -> Optional[Pot]:
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

def preflop_stats(plays: list[handplay.Played], scope_of: dict[str, str], totals: dict[str, int]) -> list[Stat]:
    """Ses décisions préflop, position par position, face aux charts avec les mêmes cartes (ou, sans charts, aux
    repères indicatifs de l'open en 6-max), dans chaque portée ; scope_of : main -> « reg » | « rec »."""
    cells: dict[tuple[str, str], dict] = {}
    for p in plays:
        scopes = ("all", scope_of.get(p.hand_id, "reg"))
        for d in p.decisions:
            if d.action not in handplay.GROUPS:
                continue
            group = handplay.GROUPS[d.action]
            cell = cells.setdefault((d.situation, p.position), {})
            for scope in scopes:
                c = cell.setdefault(scope, {"n": [0, 0, 0], "t": [0, 0, 0], "exp": [0.0, 0.0, 0.0]})
                c["n"][group] += 1
                if d.theory is not None:  # la décision d'un joueur qui suivrait les charts, avec cette main
                    c["t"][group] += 1
                    for action, freq in d.theory.items():
                        if action in handplay.GROUPS:
                            c["exp"][handplay.GROUPS[action]] += freq
    empty = {"n": [0, 0, 0], "t": [0, 0, 0], "exp": [0.0, 0.0, 0.0]}
    charts = any(sum(c["t"]) for cell in cells.values() for c in cell.values())
    out = []
    for situation, section, rows in PREFLOP:
        for position in sorted({pos for sit, pos in cells if sit == situation}, key=handplay.position_order):
            cell = cells[(situation, position)]
            for group, word, kind, stake in rows:
                ratios, refs = {}, {}
                for scope, _ in SCOPES:
                    c = cell.get(scope, empty)
                    if charts:  # comparé sur les décisions que les charts couvrent
                        n = sum(c["t"])
                        ratios[scope] = Ratio(c["t"][group], n)
                        refs[scope] = c["exp"][group] / n if n else None
                    else:
                        ratios[scope] = Ratio(c["n"][group], sum(c["n"]))
                if not ratios["all"].opps:
                    continue
                reference = refs.get("reg") if refs.get("reg") is not None else refs.get("all")
                band = None
                if not charts and situation == "open" and group == 0:
                    found = ring.OPEN_6MAX.get(position)
                    band = (found[0] / 100, found[1] / 100) if found else None
                if not ratios["all"].hits and (reference is None and band is None or
                                               reference is not None and reference < 0.005):
                    continue  # jamais fait, et rien (ou les charts non plus) ne le fait : rien à dire
                n_all, n_charts = sum(cell["all"]["n"]), sum(cell["all"]["t"])
                note = ""
                if charts and n_charts < n_all:
                    note = (f"comparé sur ses {n_charts} décisions que les charts couvrent, sur {n_all} : les mains "
                            "hors des charts d'open, et les places sans chart (UTG à 7-9 joueurs…), n'y comptent pas")
                elif band is not None:
                    note = "repère indicatif d'un régulier 6-max : charge tes charts pour comparer main par main"
                more, less = ADVICE[kind]
                out.append(Stat(f"pre:{situation}:{position}:{word}", section, f"{position} : {word}", kind, ratios,
                                reference, note, per100=100 * ratios["all"].opps / max(totals["all"], 1), band=band,
                                versus="les charts avec les mêmes cartes", advice={"plus": more, "moins": less},
                                stake=stake, exact=charts, refs={k: v for k, v in refs.items() if v is not None},
                                link=("mains#" + urlencode({"fmt": FORMAT, "kind": "reg", "pos": position,
                                                            "sit": situation}), "Voir ses mains de départ")))
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


def postflop_stats(hands: list[Hand], hero: str, pots: dict[str, Pot], scope_of: dict[str, str]) -> list[Stat]:
    """Ses fréquences dans les pots à deux au flop, par structure de pot et par rôle, dans chaque portée, face aux plans
    de jeu des flops 6-max résolus de même structure."""
    seen: dict[tuple[str, str, str], exploit.Ratios] = {}
    for h in hands:
        pot = pots.get(h.hand_id)
        if pot is None:
            continue
        role = "agresseur" if hero == pot.aggressor else "defenseur"
        events = HandReader(_view(h, pot)).events.get(hero, [])
        for scope in ("all", scope_of.get(h.hand_id, "reg")):
            st = seen.setdefault((pot.structure, role, scope), exploit.Ratios())
            for key, made in events:
                st.ratios[key].add(made)
    out = []
    for structure, title in STRUCTURES:
        families = _families(structure)
        pairs = ", ".join(studyspots.RING_FAMILIES[f]["pair"] for f in families)
        for role in ROLES:
            if (structure, role, "all") not in seen:
                continue
            base = _baselines(families, role)
            for s in exploit.situations(families[0], role):
                ratios = {scope: Ratio(*exploit.tally(seen.get((structure, role, scope), exploit.Ratios()), s))
                          for scope, _ in SCOPES}
                if not ratios["all"].opps:
                    continue
                ref, flops = base.get(s.key, (None, 0))
                if ref is None:
                    note = f"pas encore de repère du solveur : résous des flops 6-max ({pairs})"
                else:
                    note = f"repère tiré de {flops} flop(s) résolu(s) : {pairs}" if flops < exploit.MIN_FLOPS else ""
                out.append(Stat(f"{structure}:{role}:{s.key}", title, f"{ROLES[role]} : {s.label}",
                                "bet" if s.measure == "bet" else s.measure, ratios, ref, note,
                                per100=100 * ratios["all"].opps / max(len(hands), 1),
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


def interesting(parts: dict[str, list[Hand]], hero: str, pots: dict[str, Pot], digests: list[dict],
                per_line: int = 2, limit: int = 24) -> dict[str, list[Pick]]:
    """Par portée (réguliers, récréatifs) : les plus gros pots à deux de chaque ligne (per_line par ligne), jusqu'à
    limit mains ; contre les réguliers, elles passent au solveur."""
    solved = {d["hand"]: d for d in digests}
    out = {}
    for scope in ("reg", "rec"):
        lines: dict[str, list[Pick]] = {}
        for h in parts[scope]:
            pot = pots.get(h.hand_id)
            if pot is None:
                continue
            line = line_of(h, hero, pot)
            lines.setdefault(line, []).append(Pick(h, scope, line, round(h.total_pot / h.bb, 1),
                                                   round(h.net(hero) / h.bb, 1), solved.get(h.hand_id)))
        chosen = []
        for picks in sorted(lines.values(), key=lambda ps: -max(p.pot_bb for p in ps)):
            chosen.extend(sorted(picks, key=lambda p: -p.pot_bb)[:per_line])
        chosen = sorted(chosen, key=lambda p: -p.pot_bb)[:limit]
        if scope == "reg":
            for p in chosen:
                if p.digest is None:
                    try:
                        p.spot = postflop.build_spot(p.hand, hero)
                    except postflop.Unsupported as exc:
                        p.why = str(exc)
        out[scope] = chosen
    return out


# --- Le rapport -----------------------------------------------------------------------------------------------------

def mine(hands: list[Hand], hero: str) -> list[Hand]:
    """Ses mains aux tables de 3 joueurs et plus."""
    return [h for h in hands if h.size > 2 and hero in h.seats and h.bb and h.button]


def _pots(hands: list[Hand], hero: str) -> dict[str, Pot]:
    """Ses pots à deux joueurs au flop (ligne préflop simple), par main."""
    out = {}
    for h in hands:
        pot = pot_of(h)
        if pot is not None and hero in (pot.oop, pot.ip):
            out[h.hand_id] = pot
    return out


def stats(hands: list[Hand], hero: str, kinds: Optional[dict] = None, plays: Optional[list] = None,
          pots: Optional[dict[str, Pot]] = None) -> list[Stat]:
    """Ses stats face à la théorie, sans le solveur : préflop face aux charts, après le flop face aux plans de jeu, sur
    toutes ses mains, contre les réguliers et contre les récréatifs (kinds : le type de ses adversaires ; plays : ses
    mains de départ déjà lues avec ses charts, handplay.collect, sur ces mains ou plus)."""
    hands = mine(hands, hero)
    parts = split(hands, hero, kinds or {})
    scope_of = {h.hand_id: scope for scope in ("reg", "rec") for h in parts[scope]}
    if pots is None:
        pots = _pots(hands, hero)
    if plays is None:
        plays = handplay.collect(hands, hero, handplay.Theory(None, handplay.ring_lines()))
    else:  # celles de ces mains, sans l'EV perdue (le préflop face aux charts n'en a pas besoin)
        ids = {h.hand_id for h in hands}
        plays = [p for p in plays if p.hand_id in ids]
    totals = {scope: len(parts[scope]) for scope, _ in SCOPES}
    return preflop_stats(plays, scope_of, totals) + postflop_stats(hands, hero, pots, scope_of)


def build(hands: list[Hand], hero: str, kinds: Optional[dict] = None, plays: Optional[list] = None,
          found: Optional[dict] = None) -> Report:
    """Le rapport d'un joueur aux tables à plusieurs (toutes ses mains de 3 joueurs et plus ; kinds : le type de ses
    adversaires, players.classify). plays : ses mains de départ déjà lues avec ses charts (handplay.collect, sur ces
    mains ou plus) ; found : ring.analyze de chaque portée (parts) — de quoi ne pas les relire."""
    kinds = kinds or {}
    hands = mine(hands, hero)
    parts = split(hands, hero, kinds)
    pots = _pots(hands, hero)
    solver = solver_review(parts["reg"], hero, pots)
    lines = handplay.ring_lines()
    losses = review.hero_losses(solver["digests"])
    plays = (handplay.with_losses(plays, hands, losses) if plays is not None else
             handplay.collect(hands, hero, handplay.Theory(None, lines), losses))
    rows = stats(hands, hero, kinds, plays, pots)
    leakcheck.attach(rows, leakcheck.ring_checks(rows, parts["reg"], hero, pots, plays, kinds))
    regular = {h.hand_id for h in parts["reg"]}
    hand_leaks = [_hand_leak(x, len(parts["reg"]), "reg", FORMAT)
                  for x in handplay.losers([p for p in plays if p.hand_id in regular])[:HAND_LEAKS]]
    if found is None:
        found = {scope: ring.analyze(parts[scope], hero, merge=True) for scope, _ in SCOPES}
    info = {scope: {k: found[scope][0].total.ratios[k] if found[scope] else Ratio() for k in ("vpip", "pfr", "wtsd", "wsd")}
            for scope, _ in SCOPES}
    winrate = {scope: found[scope][0].total.bb100 if found[scope] else None for scope, _ in SCOPES}
    plans = sum(len(coach.plans(f)) for f in studyspots.RING_FAMILIES)
    opponents: dict = {"reg": [], "rec": []}
    for name, info_k in kinds.items():
        opponents["rec" if info_k.get("kind") == "rec" else "reg"].append(name)
    return Report(hero, len(hands), {s: len(parts[s]) for s, _ in SCOPES}, winrate, info, rows, solver,
                  interesting(parts, hero, pots, solver["digests"]),
                  rank(rows, solver["groups"], solver["analyzed"], digests=solver["digests"], extra=hand_leaks),
                  [], opponents, table_format=FORMAT,
                  context={"charts": bool(lines), "plans": plans, "pots": len(pots),
                           "formats": sorted({h.table_format for h in hands}, key=lambda f: (len(f), f))},
                  exploits=kept(rows), opportunities=chances(rows))
