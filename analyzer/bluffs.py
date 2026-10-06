"""Les bluffs des adversaires : dans quelles lignes, sur quelles cartes, avec quelles tailles.

Deux sources, croisées :

1. Ses fréquences de mise, sur toutes les mains (pas seulement celles qui vont à l'abattage), selon la carte qui
   vient de tomber (overcard, couleur ou quinte possible, board qui se paire, brique) ou la texture du flop, face
   à celles du solveur dans les mêmes situations (plans de jeu des flops résolus). Une carte ne lui donne pas plus
   de bonnes mains qu'à la théorie : s'il mise nettement plus que le solveur quand elle tombe, le surplus est
   fait de bluffs (ou de value fine) ; s'il mise nettement moins, ses mises y sont surtout de la value.
2. Ses mains montrées : chaque mise ou relance vue à l'abattage, avec son intention (value, value fine,
   semi-bluff, bluff : lines.classify), rangée par ligne, taille, carte et texture. À la river, la part de bluffs
   est comparée à celle de la théorie pour la mise, l'équité qu'il te faut pour payer (un tiers pour une mise
   de la taille du pot) : à la river, ta décision de payer ne dépend pas de ses cartes, les mains vues sont un
   échantillon honnête de sa ligne. Au flop et à la turn, on compare à sa propre moyenne.

Un pattern est « solide » quand le hasard l'explique mal (intervalle de confiance à 90 % qui exclut la
référence), « à confirmer » quand l'écart est net mais l'échantillon petit.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Optional

from .insights import wilson
from .lines import BOARD_SIZE, INTENT_LABELS, INTENTS, classify, line_label, required_equity
from .models import BET, POSTFLOP, RAISE, Hand
from .stats import HandReader, Ratio, think_times
from .theory import coach, exploit, studyspots

MARGIN = 0.05      # écart minimal à la référence, au-delà de l'intervalle de confiance
LOOSE_GAP = 0.20   # écart net, sur peu de mains : « à confirmer »
MIN_FREQ = 5       # occasions minimum pour une fréquence par carte
MIN_SHOWN = 3      # mises montrées minimum pour un groupe
SOLID_MIN = 10     # en dessous, même un écart net reste « à confirmer »
FAMILY_NAMES = {f: studyspots.FAMILIES[f]["name"] for f in studyspots.FAMILIES}
CARD_TEXT = {"over": "une overcard", "brick": "une brique", "paired": "une carte qui paire le board",
             "flush": "une carte qui complète une couleur possible", "straight": "une carte qui ouvre une quinte"}


@dataclass(frozen=True)
class Spot:
    key: str               # événement de stats.HandReader (other_river : mise river hors 3e barrel)
    street: str
    label: str
    plan: Optional[str]    # nœud du plan de jeu du solveur (coach.LINES_*)
    initiative: bool       # mise de l'agresseur préflop


SPOTS = (
    Spot("cbet_flop", "flop", "C-bet au flop", "cbet", True),
    Spot("float_flop", "flop", "Mise quand l'agresseur checke le flop", "stab", False),
    Spot("cbet_turn", "turn", "2e barrel", "barrel", True),
    Spot("delayed_cbet_turn", "turn", "C-bet retardée à la turn", "delayed", True),
    Spot("probe_turn", "turn", "Probe à la turn", "probe", False),
    Spot("cbet_river", "river", "3e barrel", "barrel3", True),
    Spot("other_river", "river", "Mise à la river (hors 3e barrel)", None, False),
)
SPOT = {s.key: s for s in SPOTS}


def feature(board: list[str], street: str) -> Optional[str]:
    """Texture du flop, ou type de la carte qui vient de tomber (turn, river)."""
    n = BOARD_SIZE[street]
    if len(board) < n:
        return None
    if street == "flop":
        return studyspots.flop_texture(board[:3])
    return coach.card_class(board[:n - 1], board[n - 1])


def feature_label(street: str, value: str) -> str:
    return f"flop {value}" if street == "flop" else coach.CARD_LABEL.get(value, value)


# --- 1. Ses fréquences selon la carte ----------------------------------------------------------------

@dataclass
class Freq:
    family: Optional[str]
    spot: Spot
    feature: str
    ratio: Ratio = field(default_factory=Ratio)
    solver: Optional[float] = None
    average: Optional[float] = None  # sans repère du solveur : sa fréquence sur les autres cartes
    verdict: Optional[tuple[str, str]] = None  # (« plus » | « moins », « solide » | « à confirmer »)

    @property
    def reference(self) -> Optional[float]:
        return self.solver if self.solver is not None else self.average


def _spot_events(reader: HandReader, player: str) -> dict[str, bool]:
    """Situations de mise de ce joueur dans la main (une décision par situation) : {clé: a misé}."""
    events = {k: made for k, made in reader.events.get(player, []) if "." not in k}
    out = {k: v for k, v in events.items() if k in SPOT}
    river = events.get("lead_river", events.get("stab_river"))
    if river is not None and "cbet_river" not in events:
        out["other_river"] = river
    return out


def frequencies(hands: Iterable[Hand], names: Iterable[str]) -> list[Freq]:
    """Ses mises par situation et par carte (ou texture), toutes mains confondues, avec le repère du solveur."""
    names = set(names)
    rows: dict[tuple, Freq] = {}
    for hand in hands:
        if not hand.button or not hand.big_blind or not hand.bb:
            continue
        players = names & set(hand.seats)
        if not players:
            continue
        reader = HandReader(hand)
        family = exploit.hand_family(reader)
        for player in players:
            for key, made in _spot_events(reader, player).items():
                spot = SPOT[key]
                value = feature(hand.board, spot.street)
                if value is None:
                    continue
                row = rows.setdefault((family, key, value), Freq(family, spot, value))
                row.ratio.add(made)
    baselines = {f: solver_rates(f) for f in {r.family for r in rows.values()} if f}
    own: dict[tuple, Ratio] = defaultdict(Ratio)
    for row in rows.values():
        total = own[(row.family, row.spot.key)]
        total.hits, total.opps = total.hits + row.ratio.hits, total.opps + row.ratio.opps
    for row in rows.values():
        if row.family:
            row.solver = baselines[row.family].get((row.spot.key, row.feature))
        if row.solver is not None:
            row.verdict = compare(row.ratio, row.solver, MIN_FREQ)
        else:  # pas de repère du solveur : on compare à sa moyenne dans la situation, toutes cartes confondues
            total = own[(row.family, row.spot.key)]
            others = Ratio(total.hits - row.ratio.hits, total.opps - row.ratio.opps)
            if others.opps >= MIN_FREQ:
                row.average = others.hits / others.opps
                row.verdict = compare(row.ratio, row.average, MIN_FREQ)
    order = {s.key: i for i, s in enumerate(SPOTS)}
    families = list(studyspots.FAMILIES) + [None]
    return sorted(rows.values(), key=lambda r: (families.index(r.family), order[r.spot.key], -r.ratio.opps))


def solver_rates(family: str) -> dict[tuple[str, str], float]:
    """Fréquence de mise du solveur par (situation, carte ou texture), sur les flops résolus de la famille."""
    sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for plan in coach.plans(family):
        for spot in SPOTS:
            node = plan["nodes"].get(spot.plan) if spot.plan else None
            if not node:
                continue
            if spot.street == "flop":
                group = node["groups"].get("flop")
                if group:
                    acc = sums[(spot.key, plan["texture"])]
                    acc[0] += coach.aggression(group["f"])
                    acc[1] += 1
                continue
            for card, group in node["groups"].items():
                acc = sums[(spot.key, card)]
                acc[0] += coach.aggression(group["f"]) * group["n"]
                acc[1] += group["n"]
    return {k: v[0] / v[1] for k, v in sums.items() if v[1]}


def compare(ratio: Ratio, ref: float, minimum: int) -> Optional[tuple[str, str]]:
    """Écart à une référence : solide si l'intervalle de confiance l'exclut (de MARGIN au moins), à confirmer si
    l'écart dépasse LOOSE_GAP sur peu de mains."""
    if ratio.opps < minimum:
        return None
    lo, hi = (x / 100 for x in wilson(ratio))
    p = ratio.hits / ratio.opps
    solid = "solide" if ratio.opps >= SOLID_MIN else "à confirmer"
    if lo > ref + MARGIN:
        return "plus", solid
    if hi < ref - MARGIN:
        return "moins", solid
    if abs(p - ref) >= LOOSE_GAP:
        return ("plus" if p > ref else "moins"), "à confirmer"
    return None


# --- 2. Ses mains montrées ---------------------------------------------------------------------------

@dataclass
class Shown:
    player: str
    hand: Hand
    index: int          # l'action dans hand.actions
    street: str
    line: str           # c-bet, barrel, mise après check, check-raise...
    size: str           # tranche de taille (vide pour une relance)
    feature: str        # texture du flop ou type de la carte
    intent: str         # value, thin, semi, bluff (lines.INTENTS)
    description: str
    theory: float       # part de bluffs de la théorie : l'équité qu'il te faut pour payer
    think: Optional[float]

    @property
    def bluff(self) -> bool:
        return self.intent in ("bluff", "semi") if self.street != "river" else self.intent == "bluff"


def shown_bets(hands: Iterable[Hand], names: Iterable[str], hero: str) -> list[Shown]:
    """Ses mises et relances postflop dont on a vu les cartes à l'abattage."""
    names = set(names)
    out = []
    for hand in hands:
        if not hand.showdown or len(hand.hole_cards.get(hero, [])) != 2:
            continue
        players = [p for p in names & set(hand.seats) if len(hand.hole_cards.get(p, [])) == 2]
        if not players:
            continue
        pfa = HandReader(hand).pfa
        times = think_times(hand)
        for i, a in enumerate(hand.actions):
            if a.player not in players or a.street == "preflop" or a.kind not in (BET, RAISE):
                continue
            board = hand.board[: BOARD_SIZE[a.street]]
            value = feature(hand.board, a.street)
            if value is None:
                continue
            label, size = line_label(hand, i, pfa)
            intent, _, desc = classify(hand.hole_cards[a.player], hand.hole_cards[hero], board)
            out.append(Shown(a.player, hand, i, a.street, label, size, value, intent, desc,
                             required_equity(hand, i, hero), times[i]))
    return out


@dataclass
class Group:
    street: str
    dimension: str      # ligne, taille, carte
    value: str
    items: list = field(default_factory=list)
    reference: Optional[float] = None   # théorie (river) ou sa moyenne à cette street
    verdict: Optional[tuple[str, str]] = None

    @property
    def intents(self) -> Counter:
        return Counter(s.intent for s in self.items)

    @property
    def ratio(self) -> Ratio:
        return Ratio(sum(s.bluff for s in self.items), len(self.items))


DIMENSIONS = (("line", "Ligne"), ("size", "Taille"), ("feature", "Carte"))


def shown_groups(shown: list[Shown]) -> list[Group]:
    """Ses mises montrées par street et par ligne, taille, carte ; part de bluffs face à la référence."""
    groups: dict[tuple, Group] = {}
    for s in shown:
        for dim, _ in DIMENSIONS:
            value = getattr(s, dim)
            if value:
                groups.setdefault((s.street, dim, value), Group(s.street, dim, value)).items.append(s)
    overall = {street: Ratio(sum(s.bluff for s in shown if s.street == street),
                             sum(1 for s in shown if s.street == street)) for street in POSTFLOP}
    for g in groups.values():
        if g.street == "river":
            g.reference = sum(s.theory for s in g.items) / len(g.items)
        else:
            r = overall[g.street]
            g.reference = r.hits / r.opps if r.opps else None
        if g.reference is not None:
            g.verdict = compare(g.ratio, g.reference, MIN_SHOWN)
    order = {s: i for i, s in enumerate(POSTFLOP)}
    dims = {d: i for i, (d, _) in enumerate(DIMENSIONS)}
    return sorted(groups.values(), key=lambda g: (order[g.street], dims[g.dimension], -len(g.items)))


def timing(shown: list[Shown]) -> list[dict]:
    """Temps de réflexion médian de ses bluffs et de sa value, par street (au moins 3 de chaque)."""
    out = []
    for street in POSTFLOP:
        bluffs = [s.think for s in shown if s.street == street and s.bluff and s.think is not None]
        values = [s.think for s in shown if s.street == street and s.intent == "value" and s.think is not None]
        if len(bluffs) >= 3 and len(values) >= 3:
            out.append({"street": street, "bluff": median(bluffs), "value": median(values),
                        "n": (len(bluffs), len(values))})
    return out


# --- 3. Ce qui ressort ------------------------------------------------------------------------------------

@dataclass
class Pattern:
    source: str         # fréquences, abattage, timing
    street: str
    direction: str      # plus : il bluffe plus ; moins : ses mises sont de la value
    confidence: str
    title: str
    evidence: str
    advice: str
    score: float


def _pct(x: float) -> str:
    return f"{round(100 * x)} %"


STREET_AT = {"flop": "Au flop", "turn": "À la turn", "river": "À la river"}


def _where(street: str, value: str) -> str:
    return f"sur un flop {value}" if street == "flop" else f"quand {CARD_TEXT.get(value, value)} tombe"


def _freq_pattern(f: Freq, shown: list[Shown]) -> Pattern:
    direction, conf = f.verdict
    p, ref = f.ratio.hits / f.ratio.opps, f.reference
    pot = f" ({FAMILY_NAMES[f.family]})" if f.family else " (autres pots)"
    title = f"{f.spot.label}{pot} : il mise {'bien plus' if direction == 'plus' else 'bien moins'} " + (
        "que la théorie " if f.solver is not None else "que d'habitude ") + _where(f.spot.street, f.feature)
    against = f"{_pct(ref)} pour le solveur" if f.solver is not None else f"{_pct(ref)} sur les autres cartes"
    evidence = f"{_pct(p)} sur {f.ratio.opps} occasions, contre {against}"
    seen = [s for s in shown if s.street == f.spot.street and s.feature == f.feature]
    if seen:  # ce que montrent ses mises vues à l'abattage sur ces cartes (toutes lignes)
        what = "bluff(s)" if f.spot.street == "river" else "sans main faite"
        evidence += (f" ; à l'abattage, {sum(s.bluff for s in seen)} {what} sur {len(seen)} de ses mises montrées "
                     "sur ces cartes")
    if direction == "plus":
        advice = ("Ses mises y sont plus légères : défends plus large (paie tes bluff-catchers) et relance-le plus "
                  "souvent, en bluff comme en value.")
    else:
        advice = ("Quand il mise malgré tout, c'est surtout de la value : folde tes bluff-catchers faibles. Ses checks y "
                  "cachent les bluffs qu'il abandonne : attaque le pot quand il checke.")
    return Pattern("fréquences", f.spot.street, direction, conf, title, evidence, advice,
                   _score(conf, abs(p - ref), f.ratio.opps))


def _shown_pattern(g: Group) -> Pattern:
    direction, conf = g.verdict
    r = g.ratio
    if g.dimension == "line":
        what = f"sa ligne « {g.value} »"
    elif g.dimension == "size":
        what = f"ses mises {g.value}"
    else:
        what = f"ses mises {_where(g.street, g.value)}"
    verdict = "souvent des bluffs" if direction == "plus" else "presque toujours de la value"
    title = f"{STREET_AT[g.street]}, {what} : {verdict}"
    if g.street == "river":
        evidence = (f"{r.hits} bluff(s) sur {r.opps} montrées ({_pct(r.hits / r.opps)}), contre {_pct(g.reference)} "
                    "pour la théorie à ces tailles")
        advice = ("Paie tes bluff-catchers dans cette ligne : il bluffe plus que l'équité qu'il te faut pour payer."
                  if direction == "plus" else
                  "Folde tes bluff-catchers dans cette ligne : il ne bluffe pas assez pour que payer rapporte.")
    else:
        evidence = (f"{r.hits} sans main faite (bluffs et semi-bluffs) sur {r.opps} montrées "
                    f"({_pct(r.hits / r.opps)}), contre {_pct(g.reference)} pour l'ensemble de ses mises à cette street")
        advice = ("Ses mises y sont souvent sans main faite : défends plus large et relance-le."
                  if direction == "plus" else
                  "Ses mises y sont presque toujours de la value : folde tes bluff-catchers faibles.")
    return Pattern("abattage", g.street, direction, conf, title, evidence, advice,
                   _score(conf, abs(r.hits / r.opps - g.reference), r.opps))


def patterns(freqs: list[Freq], groups: list[Group], times: list[dict]) -> list[Pattern]:
    """Ce qui ressort, du plus net au moins net. Deux groupes de mains montrées identiques (une ligne qui n'a
    qu'une taille, par exemple) ne comptent qu'une fois."""
    shown = [s for g in groups for s in g.items]
    shown = list({(s.hand.hand_id, s.index): s for s in shown}.values())
    out = [_freq_pattern(f, shown) for f in freqs if f.verdict]
    seen: set = set()
    for g in groups:
        if not g.verdict:
            continue
        key = (g.street, frozenset((s.hand.hand_id, s.index) for s in g.items))
        if key in seen:
            continue
        seen.add(key)
        out.append(_shown_pattern(g))
    for t in times:
        if abs(t["bluff"] - t["value"]) >= 2 and max(t["bluff"], t["value"]) >= 1.5 * min(t["bluff"], t["value"]):
            slower = t["bluff"] > t["value"]
            title = (f"Timing {STREET_AT[t['street']].lower().replace('au ', 'au ').replace('à la ', 'à la ')} : il "
                     f"{'réfléchit plus longtemps' if slower else 'va plus vite'} quand il bluffe")
            evidence = (f"médiane {t['bluff']:.0f} s pour ses bluffs ({t['n'][0]}), {t['value']:.0f} s pour sa value "
                        f"({t['n'][1]})")
            advice = ("Une mise lente est plus souvent un bluff : paie plus volontiers." if slower else
                      "Une mise rapide est plus souvent un bluff ; une mise lente, de la value.")
            conf = "solide" if min(t["n"]) >= 8 else "à confirmer"
            out.append(Pattern("timing", t["street"], "plus", conf, title, evidence, advice,
                               _score(conf, 0.2, sum(t["n"]))))
    return sorted(out, key=lambda p: -p.score)


def _score(confidence: str, gap: float, n: int) -> float:
    return (2.0 if confidence == "solide" else 1.0) + gap * min(n, 40) ** 0.5


@dataclass
class Report:
    names: list
    hands: int
    bets: int           # ses mises et relances postflop
    freqs: list
    shown: list
    groups: list
    times: list
    patterns: list


def analyze(hands: list[Hand], names: Iterable[str], hero: str) -> Report:
    """Tout ce qu'on sait de ses bluffs : fréquences par carte, mains montrées, patterns."""
    names = list(names)
    mine = [h for h in hands if set(names) & set(h.seats) and hero in h.seats]
    bets = sum(1 for h in mine for a in h.actions
               if a.player in names and a.street != "preflop" and a.kind in (BET, RAISE))
    freqs = frequencies(mine, names)
    shown = shown_bets(mine, names, hero)
    groups = shown_groups(shown)
    times = timing(shown)
    return Report(names, len(mine), bets, freqs, shown, groups, times, patterns(freqs, groups, times))


def intents_text(counter: Counter) -> str:
    return " · ".join(f"{INTENT_LABELS[i]} {counter[i]}" for i in INTENTS if counter[i])
