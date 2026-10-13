"""Bluffcatch à la river : quand payer, quand coucher une mise river (tu défends).

Chaque mise (ou relance) river que tu as affrontée, rangée par ligne (3e barrel, mise après check à la turn…), taille,
carte de la river, et ligne × taille. Pour chaque situation :

- la part de bluffs dans ses mises vues à l'abattage (lines.classify : rien de fait à la river), face à l'équité qu'il
  te faut pour payer (taille / (pot + 2 × taille)) : au-dessus, un bluffcatcher (une main qui ne bat que ses bluffs)
  gagne à payer ; en dessous, il gagne à coucher. Ses mises vues sont celles que tu as payées : ta décision ne dépend
  pas de ses cartes, l'échantillon est honnête tant que ses mains payées sont montrées (la part montrée est donnée) ;
- ce qu'un bluffcatcher y gagne ou perd par call, à cette part de bluffs ;
- ta fréquence de fold face à celle qui rend ses bluffs rentables (taille / (pot + taille)), et tes calls (gagnés, bb).

Le verdict (decision) : payer quand il bluffe nettement plus qu'il ne faut, coucher quand il bluffe nettement moins ;
sinon, défendre plus quand tu couches nettement plus que ce qui rend tous ses bluffs rentables (sa part de bluffs
n'y suffit pas à te donner raison).

Plusieurs joueurs (les réguliers) : la part de bluffs est la moyenne des joueurs, chacun pesant selon ses mises vues
(bluffs.pooled), comme dans la page Bluffs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import bluffs
from .lines import classify, line_label, required_equity
from .models import BET, CALL, FOLD, RAISE, Hand
from .stats import HandReader, Ratio

SIZES = ((0.40, "petite (≤ 40 %)"), (0.75, "moyenne (41–75 %)"), (1.10, "grosse (76–110 %)"),
         (math.inf, "overbet (> 110 %)"))
MIN_SHOWN = 4      # mises vues minimum pour trancher
MIN_FACED = 10     # mises affrontées minimum pour juger tes folds
TOP = 8            # critères en tête de page
ALL, LINE, SIZE, CARD, LINE_SIZE = "toutes", "ligne", "taille", "carte", "ligne × taille"
DIMENSIONS = ((ALL, "Toutes ses mises river"), (LINE, "Ligne"), (SIZE, "Taille"), (CARD, "Carte de la river"),
              (LINE_SIZE, "Ligne et taille"))


def size_class(fraction: float) -> str:
    return next(label for top, label in SIZES if fraction <= top + 1e-9)


@dataclass
class Faced:
    """Une mise river que tu as affrontée."""
    player: str
    hand: Hand
    index: int
    line: str
    size: str               # tranche de taille (vide : une relance)
    card: str               # le type de la river (coach.card_class)
    required: float         # l'équité qu'il te faut pour payer
    alpha: float            # la part de folds qui rend son bluff rentable : sa mise / (pot + sa mise)
    pot: float              # le pot avant sa mise, en bb
    bet: float              # sa mise, en bb
    to_call: float          # ce qu'il te faut payer, en bb
    reply: Optional[str]    # call, fold ou raise
    intent: Optional[str] = None    # ses cartes vues (tu as payé) : bluff, thin, value
    won: Optional[bool] = None      # ton call : gagné ou perdu

    @property
    def shown(self) -> bool:
        return self.intent is not None

    @property
    def profit(self) -> Optional[float]:
        """Ton call, en bb : ce qu'il y avait à gagner (le pot et sa mise), ou le call perdu."""
        if self.reply != CALL or self.won is None:
            return None
        return self.pot + self.bet if self.won else -self.to_call


def _reply(hand: Hand, index: int, hero: str) -> Optional[str]:
    for b in hand.actions[index + 1:]:
        if b.street != "river":
            return None
        if b.player == hero and b.kind in (FOLD, CALL, RAISE):
            return b.kind
    return None


def faced(hands: Iterable[Hand], names: Iterable[str], hero: str) -> list[Faced]:
    """Les mises et relances river de ces joueurs que tu as affrontées (et ta réponse)."""
    names = set(names)
    out = []
    for hand in hands:
        mine = hand.hole_cards.get(hero, [])
        if (hero not in hand.seats or len(mine) != 2 or len(hand.board) < 5 or not hand.bb
                or not names & set(hand.seats)):
            continue
        pfa = None
        for i, a in enumerate(hand.actions):
            if a.street != "river" or a.kind not in (BET, RAISE) or a.player not in names or not a.pot_before:
                continue
            reply = _reply(hand, i, hero)
            if reply is None:
                continue
            if pfa is None:
                pfa = HandReader.of(hand).pfa or ""
            label, size = line_label(hand, i, pfa or None)
            committed = sum(b.amount for b in hand.actions[:i] if b.street == "river" and b.player == hero)
            to_call = max(a.to - committed, 0.0)
            item = Faced(a.player, hand, i, label, size_class(a.amount / a.pot_before) if a.kind == BET else "",
                         bluffs.feature(hand.board, "river") or "", required_equity(hand, i, hero),
                         a.amount / (a.pot_before + a.amount), a.pot_before / hand.bb, a.amount / hand.bb,
                         to_call / hand.bb, reply)
            cards = hand.hole_cards.get(a.player, [])
            if reply == CALL:
                if len(cards) == 2:
                    item.intent = classify(cards, mine, hand.board[:5])[0]
                net = hand.net(hero)
                item.won = net > 0 if abs(net) > 1e-9 else None
            out.append(item)
    return out


@dataclass
class Spot:
    dimension: str
    value: str
    items: list = field(default_factory=list)

    @property
    def calls(self) -> list:
        return [x for x in self.items if x.reply == CALL]

    @property
    def folds(self) -> int:
        return sum(x.reply == FOLD for x in self.items)

    @property
    def fold_rate(self) -> float:
        return self.folds / len(self.items) if self.items else 0.0

    @property
    def alpha(self) -> float:
        """La part de folds qui rend ses bluffs rentables, en moyenne."""
        return sum(x.alpha for x in self.items) / len(self.items) if self.items else 0.0

    @property
    def shown(self) -> list:
        return [x for x in self.items if x.shown]

    @property
    def sample(self) -> Ratio:
        """Sa part de bluffs dans ses mises vues (moyenne des joueurs, chacun pesant selon ses mises vues)."""
        per: dict[str, Ratio] = {}
        for x in self.shown:
            per.setdefault(x.player, Ratio()).add(x.intent == "bluff")
        return bluffs.pooled(per, bluffs.SHOWN_K)

    @property
    def share(self) -> Optional[float]:
        r = self.sample
        return r.hits / r.opps if r.opps else None

    @property
    def required(self) -> float:
        """L'équité qu'il te faut pour payer, en moyenne sur ses mises (vues, sinon toutes)."""
        base = self.shown or self.items
        return sum(x.required for x in base) / len(base) if base else 0.0

    @property
    def seen(self) -> Optional[float]:
        """La part de tes calls où ses cartes sont montrées."""
        return len(self.shown) / len(self.calls) if self.calls else None

    @property
    def verdict(self) -> Optional[tuple[str, str]]:
        """(« plus » : il bluffe plus qu'il ne faut, paie ; « moins » : couche, « solide » | « à confirmer »)."""
        return bluffs.compare(self.sample, self.required, MIN_SHOWN) if self.shown else None

    @property
    def fold_verdict(self) -> Optional[tuple[str, str]]:
        """Tes folds face à la part qui rend ses bluffs rentables (« plus » : tu couches trop)."""
        return bluffs.compare(Ratio(self.folds, len(self.items)), self.alpha, MIN_FACED) if self.items else None

    @property
    def decision(self) -> Optional[tuple[str, str]]:
        """(« payer » | « coucher » | « défendre plus », « solide » | « à confirmer »), ou None."""
        bluff = self.verdict
        if bluff:
            return ("payer" if bluff[0] == "plus" else "coucher"), bluff[1]
        fold = self.fold_verdict
        if fold and fold[0] == "plus":
            return "défendre plus", fold[1]
        return None

    @property
    def catcher(self) -> Optional[float]:
        """Ce qu'une main qui ne bat que ses bluffs gagne (ou perd) par call, en bb, à sa part de bluffs."""
        p = self.share
        if p is None:
            return None
        return sum(p * (x.pot + x.bet) - (1 - p) * x.to_call for x in self.items) / len(self.items)

    @property
    def won(self) -> tuple[int, int, float]:
        """Tes calls jugés : (gagnés, joués, bb)."""
        judged = [x for x in self.calls if x.won is not None]
        return sum(x.won for x in judged), len(judged), sum(x.profit for x in judged)

    @property
    def counts(self) -> dict[str, int]:
        out = {"bluff": 0, "thin": 0, "value": 0}
        for x in self.shown:
            out[x.intent if x.intent in out else "value"] += 1
        return out

    @property
    def title(self) -> str:
        if self.dimension == ALL:
            return "Toutes ses mises river"
        if self.dimension == SIZE:
            return f"Mise {self.value}"
        if self.dimension == CARD:
            return f"River : {bluffs.feature_label('river', self.value)}"
        return self.value


def _value(x: Faced, dimension: str) -> str:
    if dimension == ALL:
        return "toutes"
    if dimension == LINE:
        return x.line
    if dimension == SIZE:
        return x.size
    if dimension == CARD:
        return x.card
    return f"{x.line}, {x.size}" if x.size else ""


def spots(items: list[Faced]) -> list[Spot]:
    """Les situations : toutes, puis par ligne, taille, carte, ligne et taille (les plus fréquentes d'abord)."""
    found: dict[tuple[str, str], Spot] = {}
    for x in items:
        for dimension, _ in DIMENSIONS:
            value = _value(x, dimension)
            if value:
                found.setdefault((dimension, value), Spot(dimension, value)).items.append(x)
    order = {d: k for k, (d, _) in enumerate(DIMENSIONS)}
    return sorted(found.values(), key=lambda s: (order[s.dimension], -len(s.items)))


def criteria(found: list[Spot], top: int = TOP) -> list[Spot]:
    """Les situations qui tranchent : toutes ses mises (si elles tranchent) ; puis payer ou coucher (sa part de
    bluffs), les solides et les plus vues d'abord. Une ligne et taille qui dit la même chose que sa ligne seule ne
    s'ajoute pas. Les situations où tu couches trop sont à part (overfolds)."""
    judged = [s for s in found if s.decision and (s.dimension == ALL or s.decision[0] != "défendre plus")]
    judged.sort(key=lambda s: (s.dimension != ALL, s.decision[1] != "solide", -len(s.shown), s.dimension == LINE_SIZE))
    out: list[Spot] = []
    for s in judged:
        if s.dimension == LINE_SIZE:
            line = s.value.rsplit(", ", 1)[0]
            if any(o.dimension == LINE and o.value == line and o.decision[0] == s.decision[0] for o in out):
                continue
        out.append(s)
    return out[:top]


def overfolds(found: list[Spot], top: int = 5) -> list[Spot]:
    """Les situations (hors « toutes ») où tu couches nettement trop sans que sa part de bluffs te donne raison, les
    plus coûteuses d'abord (excès de folds × mises)."""
    over = [s for s in found if s.dimension not in (ALL, LINE_SIZE) and s.decision and s.decision[0] == "défendre plus"]
    over.sort(key=lambda s: -(s.fold_rate - s.alpha) * len(s.items))
    return over[:top]


def _pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{round(100 * x)} %"


VOICES = {False: {"il": "il", "sa": "sa", "ses": "ses", "bluffe": "bluffe"},
          True: {"il": "ils", "sa": "leur", "ses": "leurs", "bluffe": "bluffent"}}


def evidence(s: Spot, plural: bool = False) -> str:
    """La preuve en une ligne : sa part de bluffs, ce qu'il te faut, tes folds."""
    v = VOICES[plural]
    n, k = len(s.shown), len(s.items)
    seen = (f"{_pct(s.share)} de bluffs dans {n} mise{'s' if n > 1 else ''} vue{'s' if n > 1 else ''} pour "
            f"{_pct(s.required)} qu'il te faut" if n else "aucune mise vue à l'abattage")
    return (f"{seen} ; tu couches {_pct(s.fold_rate)} de {k} mise{'s' if k > 1 else ''} ({v['ses']} bluffs sont "
            f"rentables dès {_pct(s.alpha)} de folds).")


def advice(s: Spot, plural: bool = False) -> str:
    """Quoi faire dans cette situation."""
    v = VOICES[plural]
    floor = (f"défends à la fréquence minimale (ne couche pas plus de {_pct(s.alpha)} de ces mises), avec tes "
             "meilleurs bluffcatchers.")
    found = s.decision
    if found is None:
        if len(s.shown) < MIN_SHOWN:
            return f"Pas assez de mises vues pour trancher ({len(s.shown)}) : {floor}"
        return f"Proche de la théorie : {floor}"
    if found[0] == "payer":
        extra = " Tu couches trop." if s.fold_rate > s.alpha + 0.05 else ""
        return f"Paie : tes bluffcatchers gagnent à payer, même les plus faibles.{extra}"
    if found[0] == "coucher":
        extra = " Tes folds sont justifiés." if s.fold_rate > s.alpha else " Tu paies trop."
        return (f"Couche tes bluffcatchers : ne paie qu'avec ce qui bat une partie de {v['sa']} value ({v['sa']} "
                f"value fine : une paire moyenne ou faible).{extra}")
    if len(s.shown) >= MIN_SHOWN and s.share is not None and s.share >= s.required - 0.03:
        why = f"{v['il']} {v['bluffe']} autant qu'il faut ({_pct(s.share)} pour {_pct(s.required)})"
    else:
        why = f"rien ne dit encore qu'{v['il']} {v['bluffe']} moins qu'il ne faut"
    return (f"Défends plus : à ce taux de folds, n'importe quel bluff de {v['sa']} part est rentable, et {why}. Paie "
            f"avec tes meilleurs bluffcatchers (ceux qui battent {v['sa']} value fine) jusqu'à couvrir la défense "
            "minimale.")
