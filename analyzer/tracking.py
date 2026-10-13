"""Suivi des leaks dans le temps : chaque écart à la théorie du Leakfinding, période par période, et avant / après le
jour où le joueur a commencé à le travailler.

Les périodes suivent le calendrier quand les mains s'étalent assez (des mois, sinon des semaines), sinon des blocs de
mains de même taille ; au plus MAX_PERIODS, les plus anciennes réunies. Chaque période est lue comme le Leakfinding
(contre les réguliers, face à la théorie : la solution préflop ou les charts, les plans de jeu des flops résolus, le
repère ramené à sa taille) ; un écart suivi compare ses mains d'avant la date choisie à celles d'après :

- corrigé : depuis, sa fréquence est dans la tolérance de la théorie (celle du Leakfinding) ;
- en progrès : elle s'en est nettement rapprochée (le hasard l'explique mal : test à 90 %), sans y être encore ;
- s'éloigne de la théorie : elle s'en est nettement éloignée ;
- pas encore de changement net, trop tôt pour le dire (moins de LOOSE_N occasions depuis), ou rien à comparer
  (moins de LOOSE_N occasions avant).

Les leaks suivis se gardent dans la base (documents « suivi », un par joueur et par format de table).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

from . import db
from .db import documents
from .leaks import LOOSE_N, Stat
from .models import Hand
from .stats import Ratio

MAX_PERIODS = 8
MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
Z_MOVE = 1.28   # un changement net : test unilatéral à 90 %
BLOCK_HANDS = 250  # sans assez de semaines : des blocs d'environ autant de mains

STATUSES = {  # clé -> (libellé, explication)
    "corrige": ("Corrigé", "depuis, ta fréquence est dans la tolérance de la théorie"),
    "progres": ("En progrès", "ta fréquence s'est nettement rapprochée de la théorie, sans y être encore"),
    "pas_encore": ("Pas encore de changement net", "ta fréquence n'a pas nettement bougé"),
    "aggrave": ("S'éloigne de la théorie", "ta fréquence s'en est nettement éloignée"),
    "trop_tot": ("Trop tôt pour le dire", f"il faut au moins {LOOSE_N} occasions depuis"),
    "sans_avant": ("Rien à comparer", f"moins de {LOOSE_N} occasions avant cette date"),
}


@dataclass
class Period:
    label: str
    start: date
    end: date
    hands: list = field(default_factory=list)


def _day(hand: Hand) -> Optional[date]:
    when = hand.date
    return when.date() if isinstance(when, datetime) else when


def _span(first: Hand, last: Hand) -> str:
    """Le libellé d'un bloc de mains : ses jours, ou ses heures quand il tient dans une journée."""
    if _day(first) == _day(last) and isinstance(first.date, datetime):
        return f"{first.date:%d/%m} {first.date:%H}h{first.date:%M} → {last.date:%H}h{last.date:%M}"
    return f"{_day(first):%d/%m} → {_day(last):%d/%m}"


def periods(hands: list[Hand]) -> tuple[str, list[Period]]:
    """Les périodes de ces mains, des plus anciennes aux plus récentes, et leur unité (« mois », « semaine »,
    « bloc ») : le calendrier quand les mains s'étalent sur trois mois ou trois semaines au moins, sinon des blocs de
    mains. Au-delà de MAX_PERIODS, les plus anciennes sont réunies dans la première."""
    dated = sorted((h for h in hands if _day(h)), key=lambda h: h.date)
    if not dated:
        return "bloc", []
    months: dict[tuple[int, int], list[Hand]] = {}
    weeks: dict[tuple[int, int], list[Hand]] = {}
    for h in dated:
        day = _day(h)
        months.setdefault((day.year, day.month), []).append(h)
        weeks.setdefault(tuple(day.isocalendar()[:2]), []).append(h)
    if len(months) >= 3:
        unit, groups = "mois", [(f"{MONTHS[m - 1]} {y}", g) for (y, m), g in months.items()]
    elif len(weeks) >= 3:
        unit, groups = "semaine", [(f"sem. du {_day(g[0]):%d/%m}", g) for g in weeks.values()]
    else:
        k = max(1, min(MAX_PERIODS, len(dated) // BLOCK_HANDS))
        size = math.ceil(len(dated) / k)
        chunks = [dated[i:i + size] for i in range(0, len(dated), size)]
        unit, groups = "bloc", [(_span(g[0], g[-1]), g) for g in chunks]
    if len(groups) > MAX_PERIODS:  # les plus anciennes ensemble
        cut = len(groups) - MAX_PERIODS + 1
        merged = [h for _, g in groups[:cut] for h in g]
        groups = [(f"jusqu'à {groups[cut - 1][0]}", merged)] + groups[cut:]
    return unit, [Period(label, _day(g[0]), _day(g[-1]), list(g)) for label, g in groups]


# --- Avant / après ---------------------------------------------------------------------------------------------

def _rate(stat: Optional[Stat], scope: str) -> Ratio:
    return stat.ratios.get(scope, Ratio()) if stat is not None else Ratio()


def status(before: Optional[Stat], after: Optional[Stat], scope: str = "reg") -> str:
    """Où en est un écart depuis qu'il est travaillé (STATUSES) : ses mains d'avant (before) et d'après (after)."""
    ra, rb = _rate(after, scope), _rate(before, scope)
    if ra.opps < LOOSE_N:
        return "trop_tot"
    if after.verdict(scope) is None:
        return "corrige"
    if rb.opps < LOOSE_N:
        return "sans_avant"
    pa, pb = ra.hits / ra.opps, rb.hits / rb.opps
    pooled = (ra.hits + rb.hits) / (ra.opps + rb.opps)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / ra.opps + 1 / rb.opps)) or 1e-9
    if abs(pa - pb) / spread < Z_MOVE:
        return "pas_encore"
    return "progres" if after.gap(scope) < before.gap(scope) else "aggrave"


# --- Les leaks suivis (dans la base) ---------------------------------------------------------------------------

def _key(space: str, table_format: str) -> str:
    return f"{space}|{table_format}"


def followed(space: str, table_format: str) -> dict[str, dict]:
    """Les leaks que ce joueur travaille (space : son espace, « /moi », « /eleve/paul »), dans ce format : clé de la
    stat -> {"since": "AAAA-MM-JJ", "title": …}."""
    data = documents.get(db.current(), "suivi", _key(space, table_format))
    return dict(data.get("leaks", {})) if isinstance(data, dict) else {}


def follow(space: str, table_format: str, key: str, since: Optional[date], title: str = "") -> dict[str, dict]:
    """Suit ce leak à partir de cette date (None : ne plus le suivre) ; renvoie les leaks suivis."""
    found = followed(space, table_format)
    if since is None:
        found.pop(key, None)
    else:
        found[key] = {"since": since.isoformat(), "title": title[:200]}
    documents.put(db.current(), "suivi", _key(space, table_format), {"leaks": found})
    return found


# --- Le suivi -------------------------------------------------------------------------------------------------

@dataclass
class Track:
    """Un écart, période par période ; et, s'il est suivi, avant et après la date choisie."""
    key: str
    title: str
    stats: list            # par période : Stat ou None (aucune occasion)
    since: Optional[date] = None
    before: Optional[Stat] = None
    after: Optional[Stat] = None
    status: str = ""
    current: Optional[str] = None  # le sens de l'écart dans le Leakfinding (« plus », « moins »), s'il en est un

    def since_index(self, found: list[Period]) -> Optional[int]:
        """La première période qui commence après la date de suivi (où placer le repère), ou None."""
        if self.since is None:
            return None
        return next((k for k, p in enumerate(found) if p.start >= self.since), None)


@dataclass
class Tracking:
    unit: str
    periods: list
    tracks: list            # les leaks suivis, puis ceux du Leakfinding à suivre
    rows: dict              # clé -> [Stat ou None par période] : toutes les stats
    labels: dict            # clé -> (section, libellé)
    hands: int


def build(hands: list[Hand], stats_of: Callable[[list[Hand]], list[Stat]], follow_list: dict[str, dict],
          current: list[tuple[str, str, str]], scope: str = "reg") -> Tracking:
    """Le suivi : hands, toutes les mains du joueur dans ce format ; stats_of : ses stats face à la théorie sur une
    partie de ces mains (comme le Leakfinding, portée scope) ; follow_list : ses leaks suivis (followed) ; current :
    les leaks du Leakfinding à proposer, [(clé, titre, sens)]."""
    unit, found = periods(hands)
    per_period = [{s.key: s for s in stats_of(p.hands)} for p in found]
    rows: dict[str, list] = {}
    labels: dict[str, tuple[str, str]] = {}
    for k, by_key in enumerate(per_period):
        for key, stat in by_key.items():
            rows.setdefault(key, [None] * len(found))[k] = stat
            labels[key] = (stat.section, stat.label)
    tracks = []
    splits: dict[date, tuple[dict, dict]] = {}
    directions = {key: direction for key, _, direction in current}
    for key, info in follow_list.items():
        try:
            since = date.fromisoformat(info.get("since", ""))
        except ValueError:
            continue
        if since not in splits:
            before = [h for h in hands if _day(h) and _day(h) < since]
            after = [h for h in hands if _day(h) and _day(h) >= since]
            splits[since] = ({s.key: s for s in stats_of(before)} if before else {},
                             {s.key: s for s in stats_of(after)} if after else {})
        b, a = splits[since][0].get(key), splits[since][1].get(key)
        title = info.get("title") or " · ".join(labels.get(key, ("", key)))
        tracks.append(Track(key, title, rows.get(key, [None] * len(found)), since, b, a, status(b, a, scope),
                            directions.get(key)))
    for key, title, direction in current:
        if key not in follow_list:
            tracks.append(Track(key, title, rows.get(key, [None] * len(found)), current=direction))
    return Tracking(unit, found, tracks, rows, labels, len(hands))
