"""La synthèse d'un Leakfinding, pour le rapport PDF et la présentation du coach : les chiffres clés, la courbe des
résultats, les leaks à travailler, les écarts les plus importants à la théorie, ce que dit le solveur et les mains à
revoir — l'essentiel du rapport complet (leaks.Report), sur la période choisie."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .. import leaks
from ..models import Hand
from ..report import num
from ..theory import review
from .bilan_page import results

FORMAT_LABELS = {"HU": "Heads-up", "ring": "Tables à plusieurs"}
MAX_PICKS = 8
MAX_SITUATIONS = 4
CURVE_POINTS = 400  # points de la courbe au plus (une longue période est échantillonnée)


@dataclass
class Synthesis:
    who: str                      # le joueur : ton pseudo, ou le nom de l'élève
    report: leaks.Report
    result: dict                  # bilan_page.results : net, EV all-in, courbe…
    period: str = ""              # « 1 000 dernières mains », rien pour toutes les mains
    student: bool = False         # le rapport d'un élève (la présentation s'adresse à lui)
    generated: datetime = field(default_factory=datetime.now)

    @property
    def table_format(self) -> str:
        return self.report.table_format

    @property
    def format_label(self) -> str:
        """« Heads-up », « Tables à plusieurs (6-max, 9-max) »."""
        label = FORMAT_LABELS.get(self.table_format, self.table_format)
        tables = self.report.context.get("formats") or []
        return f"{label} ({', '.join(tables)})" if tables and self.table_format != "HU" else label

    @property
    def dates(self) -> str:
        """« du 01/09/2026 au 28/09/2026 » (ou « le 28/09/2026 »)."""
        first, last = self.result.get("first"), self.result.get("last")
        if not first:
            return ""
        a, b = f"{first:%d/%m/%Y}", f"{last:%d/%m/%Y}"
        return f"le {a}" if a == b else f"du {a} au {b}"

    @property
    def solver_who(self) -> str:
        """Les mains comparées à la théorie : contre les réguliers (heads-up), ou toutes (tables à plusieurs)."""
        return "Contre réguliers" if self.report.solver_scope == "reg" else "Ses mains"

    def tiles(self) -> list[tuple[str, str, str, Optional[float]]]:
        """Les chiffres clés : (libellé, valeur, précision, signe pour la couleur)."""
        r = self.report
        rv = r.review
        hands_label = "Mains aux tables à plusieurs" if self.table_format != "HU" else "Mains"
        return [
            (hands_label, num(r.hands, 0), f"{num(r.scope_hands['reg'], 0)} contre réguliers · "
             f"{num(r.scope_hands['rec'], 0)} contre récréatifs", None),
            ("Contre réguliers", bb100(r.winrate.get("reg")), "bb/100", r.winrate.get("reg")),
            ("Contre récréatifs", bb100(r.winrate.get("rec")), "bb/100", r.winrate.get("rec")),
            ("Face au solveur", f"{rv['analyzed']} mains", f"{num(rv['lost'], 1)} bb perdus en {rv['decisions']} "
             "décisions", None),
        ]

    def gaps(self, n: int = 8) -> list[tuple[leaks.Stat, str, str]]:
        """Les écarts les plus importants à la théorie (leaks.top_gaps)."""
        return leaks.top_gaps(self.report, n)

    def situations(self, n: int = MAX_SITUATIONS) -> list[dict]:
        """Les situations où le solveur voit perdre le plus d'EV (au moins une décision avec main connue)."""
        groups = [g for g in self.report.review.get("groups", []) if g["known"] and g["lost"] >= 0.05]
        return groups[:n]

    def picks(self, n: int = MAX_PICKS) -> list[leaks.Pick]:
        """Les mains à revoir : celles comparées au solveur d'abord, puis celles contre les récréatifs."""
        first = self.report.picks.get(self.report.solver_scope, [])
        rest = [p for scope, items in self.report.picks.items() if scope != self.report.solver_scope for p in items]
        seen, out = set(), []
        for p in first[:max(n - 2, 1)] + rest + first[max(n - 2, 1):]:
            if p.hand.hand_id not in seen:
                seen.add(p.hand.hand_id)
                out.append(p)
        return out[:n]

    def curve(self, points: int = CURVE_POINTS) -> list[tuple[int, float, float]]:
        """(main, résultat réel, EV all-in) cumulés, en bb ; au plus points points (le dernier toujours)."""
        rows = self.result.get("curve") or []
        if not rows:
            return []
        step = max(1, len(rows) // points)
        picked = list(range(0, len(rows), step))
        if picked[-1] != len(rows) - 1:
            picked.append(len(rows) - 1)
        return [(0, 0.0, 0.0)] + [(i + 1, rows[i][0], rows[i][1]) for i in picked]


def bb100(x: Optional[float]) -> str:
    return "–" if x is None else num(x, 1, sign=True)


def pick_status(p: leaks.Pick) -> str:
    """Ce qu'en dit le solveur : EV perdue, à analyser, ou à revoir à la main."""
    if p.digest is not None:
        lost = sum(review.loss(d) or 0 for d in p.digest["decisions"] if d["who"] == "H")
        return f"{num(lost, 2)} bb perdus" if lost >= 0.005 else "rien perdu"
    if p.spot is not None:
        return "à analyser"
    return "à revoir à la main" if p.kind == "rec" else "hors solveur"


def collect(report: leaks.Report, hands: list[Hand], who: str, period: str = "", student: bool = False,
            now: Optional[datetime] = None) -> Synthesis:
    """La synthèse d'un rapport : hands, les mains du rapport (pour la courbe des résultats) ; period, la période
    (rien pour toutes les mains)."""
    return Synthesis(who, report, results(hands, report.hero), period, student, now or datetime.now())
