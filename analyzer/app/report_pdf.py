"""Le rapport de Leakfinding en PDF, synthétique (deux pages environ) : les chiffres clés, la courbe des résultats,
les leaks à travailler, les écarts les plus importants à la théorie, ce que dit le solveur et les mains à revoir.

Fait avec analyzer/pdfwriter.py (sans dépendance) à partir de la synthèse (synthesis.py)."""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from .. import NAME, leaks
from .. import pdfwriter as pw
from ..report import num
from .synthesis import Synthesis, pick_status, plural

W, H = pw.A4
M = 42.0                   # marges gauche et droite
CW = W - 2 * M             # largeur du contenu
TOP, BOTTOM = 48.0, H - 54.0

INK, INK2, MUTED = pw.rgb("#0b0b0b"), pw.rgb("#52514e"), pw.rgb("#898781")
GRID, AXIS = pw.rgb("#e1e0d9"), pw.rgb("#c3c2b7")
TILE = pw.rgb("#f4f3ef")
ACCENT = pw.rgb("#2a78d6")
REAL_LINE, EV_LINE = pw.rgb("#008300"), pw.rgb("#eda100")  # comme le graphique de l'application (trackers)
WIN, LOSS = pw.rgb("#006300"), pw.rgb("#d03b3b")
HI_BG, LO_BG = pw.rgb("#fbe1d6"), pw.rgb("#dde9f8")  # trop souvent, pas assez (et « solide », « indicatif »)
WHITE = (1.0, 1.0, 1.0)
SUIT_COLORS = {"s": INK, "h": LOSS, "d": ACCENT, "c": pw.rgb("#008300")}


def tone(x: Optional[float]):
    return INK if x is None or abs(x) < 0.05 else WIN if x > 0 else LOSS


def draw_cards(page: pw.Page, x: float, y: float, cards: Sequence[str], size: float = 9.0) -> float:
    """Des cartes (« As », « Kh ») : la hauteur en gras, la couleur en symbole de sa teinte ; renvoie la largeur."""
    start = x
    for card in cards:
        rank, suit = card[0].upper(), card[1].lower()
        x += page.text(x, y, rank, "bold", size, INK)
        x += page.suit(x + 0.5, y, suit, size * 0.95, SUIT_COLORS.get(suit, INK)) + 3.2
    return x - start


def cards_width(cards: Sequence[str], size: float = 9.0) -> float:
    return sum(pw.text_width(c[0].upper(), "bold", size) + pw.SUITS[c[1].lower()][1] * size * 0.95 / 1000 + 3.7
               for c in cards)


def pill(page: pw.Page, x: float, y: float, text: str, solid: bool, align: str = "left") -> float:
    """Une étiquette « solide » / « indicatif » (y : ligne de base du texte voisin) ; renvoie sa largeur."""
    w = pw.text_width(text, "regular", 7.5) + 8
    if align == "right":
        x -= w
    page.rect(x, y - 8.2, w, 11, fill=HI_BG if solid else LO_BG, radius=3)
    page.text(x + 4, y, text, "regular", 7.5, INK2)
    return w


class Flow:
    """Le fil du document : la page en cours et la hauteur où l'on écrit ; une nouvelle page quand il le faut."""

    def __init__(self, s: Synthesis):
        self.s = s
        self.doc = pw.Document(title=f"Leakfinding — {s.who}", subject=s.format_label)
        self.page: pw.Page = self.doc.add_page()
        self.y = TOP

    def new_page(self) -> None:
        self.page = self.doc.add_page()
        self.page.text(M, TOP, f"Leakfinding de {self.s.who} · {self.s.format_label}", "bold", 8.5, MUTED)
        self.page.line(M, TOP + 7, W - M, TOP + 7, GRID, 0.6)
        self.y = TOP + 24

    def need(self, height: float) -> None:
        if self.y + height > BOTTOM:
            self.new_page()

    def heading(self, text: str, right: str = "", keep: float = 60) -> None:
        """Un titre de partie, gardé avec le début de ce qui suit (keep points)."""
        self.need(30 + keep)
        self.y += 10
        self.page.text(M, self.y + 12, text, "bold", 12.5, INK)
        if right:
            self.page.text(W - M, self.y + 12, right, "regular", 8.5, MUTED, align="right")
        self.y += 24

    def paragraph(self, text: str, x: float = M, width: float = CW, font: str = "regular", size: float = 9.0,
                  color=INK2, leading: Optional[float] = None) -> None:
        leading = leading or size * 1.35
        lines = pw.wrap(text, width, font, size)
        self.need(len(lines) * leading)
        for line in lines:
            self.page.text(x, self.y + size, line, font, size, color)
            self.y += leading

    def footers(self) -> None:
        total = len(self.doc.pages)
        for i, page in enumerate(self.doc.pages, start=1):
            page.line(M, H - 40, W - M, H - 40, GRID, 0.6)
            page.text(M, H - 28, f"{NAME} · Leakfinding de {self.s.who} · {self.s.generated:%d/%m/%Y}", "regular",
                      7.5, MUTED)
            page.text(W - M, H - 28, f"page {i} / {total}", "regular", 7.5, MUTED, align="right")


# --- tableau -------------------------------------------------------------------------------------------------------

Cell = object  # du texte, ou (texte, police, couleur, fond) ; ou une fonction (page, x, ligne de base) pour un dessin


def table(flow: Flow, columns: Sequence[tuple[str, float, str]], rows: Sequence[Sequence[Cell]],
          size: float = 8.5, leading: float = 11.0) -> None:
    """Un tableau : columns [(titre, largeur, « left » | « right »)] ; une ligne trop haute passe à la page suivante
    avec l'en-tête du tableau."""
    def header() -> None:
        x = M
        for title, width, align in columns:
            tx = x + width - 4 if align == "right" else x + 4
            flow.page.text(tx, flow.y + 8, title, "bold", 7.5, MUTED, align=align)
            x += width
        flow.y += 12
        flow.page.line(M, flow.y, M + sum(c[1] for c in columns), flow.y, AXIS, 0.6)
        flow.y += 2

    flow.need(16 + 2 * leading)
    header()
    for row in rows:
        layouts = []
        for cell, (_, width, _) in zip(row, columns):
            if callable(cell):
                layouts.append(([], None, None, None, cell))
                continue
            text, font, color, fill = cell if isinstance(cell, tuple) else (cell, "regular", INK, None)
            layouts.append((pw.wrap(str(text), width - 8, font, size), font, color, fill, None))
        height = max([len(lines) for lines, *_ in layouts] + [1]) * leading + 6
        if flow.y + height > BOTTOM:
            flow.new_page()
            header()
        x = M
        for (lines, font, color, fill, draw), (_, width, align) in zip(layouts, columns):
            if fill is not None:
                flow.page.rect(x + 1, flow.y + 1, width - 2, height - 2, fill=fill)
            if draw is not None:
                draw(flow.page, x + 4, flow.y + 3 + size)
            for k, line in enumerate(lines):
                tx = x + width - 4 if align == "right" else x + 4
                flow.page.text(tx, flow.y + 3 + size + k * leading, line, font, size, color, align=align)
            x += width
        flow.y += height
        flow.page.line(M, flow.y, M + sum(c[1] for c in columns), flow.y, GRID, 0.5)


# --- les parties du rapport -------------------------------------------------------------------------------------------

def _head(flow: Flow) -> None:
    s, page = flow.s, flow.page
    page.text(M, TOP, f"{NAME.upper()} · LEAKFINDING", "bold", 8.5, ACCENT)
    page.text(W - M, TOP, f"Rapport du {s.generated:%d/%m/%Y}", "regular", 8.5, MUTED, align="right")
    page.text(M, TOP + 28, s.who, "bold", 22, INK)
    line = " · ".join(x for x in (s.format_label, f"{num(s.report.hands, 0)} mains", s.dates) if x)
    page.text(M, TOP + 46, line, "regular", 10, INK2)
    flow.y = TOP + 52
    if s.period:
        page.text(M, TOP + 60, f"Période : {s.period}", "regular", 9, MUTED)
        flow.y = TOP + 66
    flow.y += 8
    page.line(M, flow.y, W - M, flow.y, GRID, 0.8)
    flow.y += 12


def _tiles(flow: Flow) -> None:
    page, gap = flow.page, 8.0
    width = (CW - 3 * gap) / 4
    tiles = flow.s.tiles()
    subs = [pw.wrap(sub, width - 20, "regular", 7.5) for _, _, sub, _ in tiles]
    height = 46 + 9 * max(len(x) for x in subs)
    for k, (label, value, _, sign) in enumerate(tiles):
        x = M + k * (width + gap)
        page.rect(x, flow.y, width, height, fill=TILE, radius=6)
        page.text(x + 10, flow.y + 15, label, "regular", 8, INK2)
        page.text(x + 10, flow.y + 34, value, "bold", 15, tone(sign) if sign is not None else INK)
        for j, line in enumerate(subs[k]):
            page.text(x + 10, flow.y + 46 + j * 9, line, "regular", 7.5, MUTED)
    flow.y += height + 4


def _nice_step(span: float, ticks: int = 4) -> float:
    raw = span / max(ticks, 1)
    power = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    return next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)


def _curve(flow: Flow) -> None:
    s = flow.s
    points = s.curve()
    if len(points) < 3:
        return
    res = s.result
    right = (f"{num(res['net_bb'], 1, sign=True)} bb · {num(res['bb100'], 1, sign=True)} bb/100 · EV all-in "
             f"{num(res['ev_bb'], 1, sign=True)} bb")
    flow.heading("Résultat cumulé (bb)", right, keep=150)
    page, top, height = flow.page, flow.y, 150.0
    left, bottom_pad, top_pad = M + 36, 18.0, 16.0
    plot_w, plot_h = W - M - 8 - left, height - bottom_pad - top_pad
    values = [v for _, net, ev in points for v in (net, ev)] + [0.0]
    lo, hi = min(values), max(values)
    if hi - lo < 1:
        lo, hi = lo - 1, hi + 1
    step = _nice_step(hi - lo)
    lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step
    n = points[-1][0] or 1
    px = lambda i: left + plot_w * i / n  # noqa: E731
    py = lambda v: top + top_pad + plot_h * (hi - v) / (hi - lo)  # noqa: E731
    tick = lo
    while tick <= hi + step / 2:
        color, width = (AXIS, 0.8) if abs(tick) < step / 1000 else (GRID, 0.5)
        page.line(left, py(tick), left + plot_w, py(tick), color, width)
        page.text(left - 5, py(tick) + 2.6, num(tick, 0), "regular", 7, MUTED, align="right")
        tick += step
    page.polyline([(px(i), py(ev)) for i, _, ev in points], EV_LINE, 1.0)
    page.polyline([(px(i), py(net)) for i, net, _ in points], REAL_LINE, 1.5)
    page.text(left + plot_w, top + height - 4, f"{num(n, 0)} mains", "regular", 7, MUTED, align="right")
    page.text(left, top + height - 4, "0", "regular", 7, MUTED)
    x = left + 4
    for label, color in (("Résultat réel", REAL_LINE), ("EV all-in", EV_LINE)):
        page.line(x, top + 6, x + 14, top + 6, color, 2)
        x += 18 + page.text(x + 18, top + 8.5, label, "regular", 7.5, INK2) + 14
    flow.y = top + height + 6


def _leaks(flow: Flow) -> None:
    items = flow.s.report.leaks
    flow.heading("Les leaks à travailler", "classés par confiance, puis par poids", keep=70)
    if not items:
        flow.paragraph("Pas de leak net pour l'instant : pas assez de mains contre les réguliers, ou un jeu proche de "
                       "la théorie dans les situations mesurées. Les mains analysées par le solveur affinent le rapport.")
        return
    text_x, width = M + 22, CW - 22
    for k, leak in enumerate(items, start=1):
        conf_w = pw.text_width(leak.confidence, "regular", 7.5) + 14
        title = pw.wrap(leak.title, width - conf_w, "bold", 10)
        evidence = pw.wrap(leak.evidence, width, "regular", 8.6)
        advice = pw.wrap("› " + leak.advice, width, "regular", 9)
        height = len(title) * 13 + len(evidence) * 11.2 + len(advice) * 11.8 + 10
        flow.need(height)
        page, y = flow.page, flow.y
        page.rect(M, y + 1, 15, 15, fill=ACCENT, radius=7.5)
        page.text(M + 7.5, y + 11.6, str(k), "bold", 8.5, WHITE, align="center")
        for j, line in enumerate(title):
            page.text(text_x, y + 12 + j * 13, line, "bold", 10, INK)
        pill(page, W - M, y + 11.5, leak.confidence, leak.confidence == "solide", align="right")
        y += len(title) * 13 + 2
        for line in evidence:
            page.text(text_x, y + 9, line, "regular", 8.6, INK2)
            y += 11.2
        for line in advice:
            page.text(text_x, y + 9.5, line, "regular", 9, INK)
            y += 11.8
        flow.y = y + 8


def _gaps(flow: Flow) -> None:
    s = flow.s
    found = s.gaps()
    flow.heading("Les écarts les plus importants", "fréquence (occasions) face à la théorie", keep=50)
    if not found:
        flow.paragraph("Aucun écart net à la théorie pour l'instant : pas assez d'occasions, ou des fréquences proches "
                       "de la théorie.")
        return
    scope = s.report.solver_scope
    rows = []
    for stat, direction, confidence in found:
        r = stat.ratios[scope]
        advice = (stat.advice or {}).get(direction) or leaks.ADVICE.get((stat.kind, direction), "écart à corriger")
        reference = (f"{round(100 * stat.reference)} %" if stat.reference is not None else
                     f"{round(100 * stat.band[0])}–{round(100 * stat.band[1])} %" if stat.band else "–")
        gap = round(100 * stat.gap(scope))
        rows.append([
            (f"{stat.label} — {stat.section}", "regular", INK, None),
            (f"{round(100 * r.hits / r.opps)} % ({r.opps})", "bold", INK, HI_BG if direction == "plus" else LO_BG),
            (reference, "regular", INK2, None),
            (f"{'+' if direction == 'plus' else '−'}{gap} pts", "regular", INK, None),
            (lambda page, x, y, c=confidence: pill(page, x, y, c, c == "solide")),
            (leaks._cap(advice), "regular", INK2, None),
        ])
    table(flow, [("Situation", 158, "left"), (s.solver_who, 72, "right"), ("Théorie", 50, "right"),
                 ("Écart", 44, "right"), ("Confiance", 58, "left"), ("À faire", CW - 382, "left")], rows, size=8.2)


def _solver(flow: Flow) -> None:
    s = flow.s
    rv = s.report.review
    flow.heading("Face au solveur", f"{plural(rv['analyzed'], 'main')} analysée{'s' if rv['analyzed'] > 1 else ''}",
                 keep=40)
    if not rv["analyzed"]:
        flow.paragraph("Aucune main passée au solveur pour l'instant : dans l'application, le Leakfinding analyse les "
                       "mains choisies (Face au solveur) ; chaque décision y est comparée à la meilleure action pour la "
                       "main exacte.")
        return
    groups = s.situations()
    flow.paragraph(f"{num(rv['lost'], 1)} bb perdus en {plural(rv['decisions'], 'décision')} face au solveur. "
                   + ("Les situations qui coûtent le plus :" if groups else "Aucune situation n'y coûte d'EV notable."),
                   color=INK2)
    flow.y += 2
    rows = []
    for g in groups:
        rows.append([(f"{g['label']} — {leaks.family_name(g['family'])}", "regular", INK, None),
                     (str(g["n"]), "regular", INK2, None), (str(g["errors"]), "regular", INK2, None),
                     (f"{num(g['lost'], 1)} bb", "bold", LOSS if g["lost"] >= 0.5 else INK, None),
                     (f"{num(g['lost'] / g['known'], 2)} bb", "regular", INK2, None)])
    if rows:
        table(flow, [("Situation", CW - 236, "left"), ("Fois", 50, "right"), ("Erreurs", 56, "right"),
                     ("EV perdue", 66, "right"), ("Par fois", 64, "right")], rows)


def _picks(flow: Flow) -> None:
    s = flow.s
    picks = s.picks()
    flow.heading("Mains à revoir", "les plus gros pots de chaque ligne", keep=50)
    if not picks:
        flow.paragraph("Pas encore de main à revoir.")
        return
    rows = []
    for p in picks:
        hero_cards = p.hand.hole_cards.get(s.report.hero, [])
        against = "contre un récréatif" if p.kind == "rec" else "contre un régulier"
        rows.append([
            (f"{p.line}\n{p.hand.date:%d/%m/%Y %H:%M} · {against}", "regular", INK, None),
            (lambda page, x, y, c=hero_cards: draw_cards(page, x, y, c)),
            (lambda page, x, y, c=p.hand.board: draw_cards(page, x, y, c)),
            (f"{num(p.pot_bb, 0)} bb", "regular", INK2, None),
            (f"{num(p.net_bb, 1, sign=True)} bb", "bold", tone(p.net_bb), None),
            (pick_status(p), "regular", INK2, None),
        ])
    table(flow, [("Ligne", 160, "left"), ("Main", 50, "left"), ("Board", 92, "left"), ("Pot", 46, "right"),
                 ("Résultat", 58, "right"), ("Solveur", CW - 406, "left")], rows)


def _notes(flow: Flow) -> None:
    notes = flow.s.report.rec_notes
    if notes:
        flow.heading("Contre les récréatifs", keep=30)
        for note in notes:
            flow.paragraph("• " + note, color=INK)
    flow.y += 8
    flow.paragraph("« Solide » : le hasard explique mal l'écart (intervalle de confiance à 90 %, 20 occasions au "
                   "moins) ; « indicatif » : écart net sur moins de mains. Le rapport complet (toutes les stats, "
                   "chaque décision face au solveur) est dans l'application, onglet Leakfinding.", size=7.8,
                   color=MUTED)


SECTIONS: tuple[Callable[[Flow], None], ...] = (_head, _tiles, _curve, _leaks, _gaps, _solver, _picks, _notes)


def build_pdf(s: Synthesis) -> bytes:
    """Le rapport PDF d'une synthèse."""
    flow = Flow(s)
    for section in SECTIONS:
        section(flow)
    flow.footers()
    return flow.doc.output(s.generated)
