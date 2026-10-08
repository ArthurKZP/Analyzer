"""La présentation PowerPoint du coach : le Leakfinding d'un élève (ou le tien), une idée par diapositive, à
présenter en séance — où il en est, sa courbe, ses leaks un par un, ses écarts à la théorie, ce que dit le solveur,
les mains à revoir ensemble et le plan de travail. Chaque diapositive a ses notes pour le coach.

Fait avec analyzer/pptxwriter.py (sans dépendance) à partir de la synthèse (synthesis.py). Le tapis vert ouvre et
ferme la présentation ; le contenu est sur fond blanc ; les cartes sont en quatre couleurs."""
from __future__ import annotations

import math
from typing import Optional, Sequence

from .. import NAME, leaks
from .. import pptxwriter as px
from ..models import Hand
from ..pptxwriter import Cell, Para, Run, para
from ..report import num
from .synthesis import Synthesis, pick_status, plural

FELT, FELT_2 = "0F3D2E", "1A5240"          # le tapis, et ses cartes
INK, INK2, MUTED = "1B1B1B", "52514E", "898781"
ON_FELT, ON_FELT_2 = "FFFFFF", "C9DDD2"
AMBER, RED, GREEN, BLUE = "E0A526", "D03B3B", "1B7A3E", "2A78D6"
CARD, RULE = "EEF3EF", "D9DED9"
HI_BG, LO_BG = "FBE1D6", "DDE9F8"          # trop souvent, pas assez (et « solide », « indicatif »)
SUITS = {"s": ("♠", INK), "h": ("♥", RED), "d": ("♦", BLUE), "c": ("♣", GREEN)}
LEFT, RIGHT = 0.6, px.SLIDE_W - 0.6
WIDTH = RIGHT - LEFT
MAX_LEAK_SLIDES = 3
SLACK = 0.95  # les lignes se coupent un peu avant le bord : les logiciels n'ont pas tous exactement les mêmes largeurs


def tone(x: Optional[float]) -> str:
    return INK if x is None or abs(x) < 0.05 else GREEN if x > 0 else RED


def fit(text: str, box_w: float, size: float, bold: bool = False, max_lines: int = 1,
        min_size: Optional[float] = None) -> tuple[str, float, int]:
    """Le texte à la plus grande taille (de size à min_size) qui tient en max_lines lignes de box_w pouces ;
    coupé d'un « … » s'il ne tient toujours pas. Renvoie (texte, taille, lignes)."""
    s, room = size, box_w * SLACK
    lines = px.wrap(text, room, s, bold)
    while len(lines) > max_lines and min_size is not None and s > min_size:
        s -= 1
        lines = px.wrap(text, room, s, bold)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and px.width(last + "…", s, bold) > room:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
        text = " ".join(lines)
    return text, s, len(lines)


def common_size(texts: Sequence[str], box_w: float, size: float, bold: bool = False, max_lines: int = 1,
                min_size: Optional[float] = None) -> float:
    """La taille qui convient à tous ces textes (des cartes côte à côte gardent la même taille de texte)."""
    return min((fit(t, box_w, size, bold, max_lines, min_size)[1] for t in texts), default=size)


def first_sentence(text: str) -> str:
    """La première phrase (les nombres s'écrivent avec une virgule : un point finit une phrase)."""
    head, sep, _ = text.partition(". ")
    return head + "." if sep else text


def text_block(slide: px.Slide, x: float, y: float, w: float, text: str, size: float, color: str = INK,
               bold: bool = False, max_lines: int = 1, min_size: Optional[float] = None, align: str = "l") -> float:
    """Un texte qui tient dans sa boîte ; renvoie sa hauteur (pouces)."""
    text, s, n = fit(text, w, size, bold, max_lines, min_size)
    h = n * px.line_height(s) + 0.04
    slide.text(x, y, w, h, [para(text, s, color, bold, align)])
    return h


def card_runs(cards: Sequence[str], size: float, gap: str = " ") -> list[Run]:
    """Des cartes en quatre couleurs : la hauteur en gras, le symbole de la couleur dans sa teinte."""
    runs: list[Run] = []
    for k, card in enumerate(cards):
        symbol, color = SUITS.get(card[1].lower(), (card[1], INK))
        runs += [Run(card[0].upper(), size, INK, bold=True), Run(symbol, size, color, bold=True)]
        if k < len(cards) - 1:
            runs.append(Run(gap, size, INK))
    return runs


def header(slide: px.Slide, kicker: str, title: str, number: int) -> None:
    """Le haut d'une diapositive de contenu : sa rubrique, son titre ; en bas à droite, son numéro."""
    slide.text(LEFT, 0.38, WIDTH, 0.3, [para(kicker.upper(), 12, AMBER, bold=True)], name="Rubrique")
    text, size, _ = fit(title, WIDTH, 30, True, 1, 22)
    slide.text(LEFT, 0.66, WIDTH, 0.6, [para(text, size, FELT, bold=True)], name="Titre")
    slide.text(RIGHT - 1.0, 7.0, 1.0, 0.25, [para(str(number), 10, MUTED, align="r")], name="Numéro")


def pill(slide: px.Slide, x: float, y: float, confidence: str, w: float = 1.25) -> None:
    solid = confidence == "solide"
    slide.shape(x, y, w, 0.36, fill=HI_BG if solid else LO_BG, geom="roundRect", radius=0.18,
                paras=[para(confidence, 12, INK2, align="ctr")], inset=0.02, name="Confiance")


def badge(slide: px.Slide, x: float, y: float, d: float, label: str, fill: str = AMBER, color: str = FELT,
          size: float = 18) -> None:
    slide.shape(x, y, d, d, fill=fill, geom="ellipse", paras=[para(label, size, color, bold=True, align="ctr")],
                inset=0.0, name="Pastille")


# --- les diapositives ------------------------------------------------------------------------------------------------

def title_slide(deck: px.Presentation, s: Synthesis) -> None:
    slide = deck.add_slide(FELT)
    for k, suit in enumerate("shdc"):  # le motif : les quatre couleurs
        symbol, color = SUITS[suit]
        slide.shape(0.8 + k * 0.95, 0.85, 0.72, 0.72, fill=ON_FELT, geom="ellipse",
                    paras=[para(symbol, 26, color, bold=True, align="ctr")], inset=0.0, name="Motif")
    kicker = "Séance de coaching · Leakfinding" if s.student else "Leakfinding"
    slide.text(0.8, 2.5, 11.7, 0.35, [para(kicker.upper(), 14, AMBER, bold=True)], name="Rubrique")
    text, size, _ = fit(s.who, 11.7, 54, True, 1, 32)
    slide.text(0.8, 2.95, 11.7, 1.0, [para(text, size, ON_FELT, bold=True)], name="Titre")
    line = " · ".join(x for x in (s.format_label, f"{num(s.report.hands, 0)} mains", s.dates) if x)
    y = 4.15
    y += text_block(slide, 0.8, y, 11.7, line, 20, ON_FELT_2, max_lines=2, min_size=16) + 0.1
    if s.period:
        text_block(slide, 0.8, y, 11.7, f"Période : {s.period}", 16, ON_FELT_2)
    slide.text(0.8, 6.55, 11.7, 0.3, [para(f"Préparé avec {NAME} le {s.generated:%d/%m/%Y}", 12, "9DBFAF")])
    slide.notes = (f"Objectif de la séance : repartir avec deux ou trois points précis à travailler.\n"
                   f"Ce bilan porte sur {num(s.report.hands, 0)} mains ({s.format_label}) "
                   + (f"{s.dates}" if s.dates else "") + (f", période : {s.period}" if s.period else "") + ".\n"
                   "Commence par demander à l'élève son ressenti sur cette période, avant de montrer les chiffres.")


def numbers_slide(deck: px.Presentation, s: Synthesis, number: int) -> None:
    slide = deck.add_slide()
    header(slide, "En chiffres", "Où tu en es", number)
    tiles = s.tiles()
    gap = 0.25
    w = (WIDTH - 3 * gap) / 4
    label_size = common_size([t[0] for t in tiles], w - 0.5, 14, min_size=12)
    value_size = common_size([t[1] for t in tiles], w - 0.5, 40, True, min_size=28)
    for k, (label, value, sub, sign) in enumerate(tiles):
        x = LEFT + k * (w + gap)
        slide.shape(x, 1.65, w, 2.35, fill=CARD, geom="roundRect", radius=0.14, name="Chiffre")
        text_block(slide, x + 0.25, 1.9, w - 0.5, label, label_size, INK2)
        text_block(slide, x + 0.25, 2.35, w - 0.5, value, value_size, tone(sign) if sign is not None else FELT,
                   bold=True)
        text_block(slide, x + 0.25, 3.2, w - 0.5, sub, 12, MUTED, max_lines=2)
    r = s.report
    analyzed = r.review["analyzed"]
    rows = [
        ("s", f"{plural(r.scope_hands['reg'], 'main')} contre les réguliers : c'est sur elles que tes leaks se mesurent "
              "face à la théorie."),
        ("h", f"{plural(r.scope_hands['rec'], 'main')} contre les récréatifs : là, l'exploitation prime sur la "
              "théorie."),
        ("d", f"{plural(analyzed, 'main')} déjà passée{'s' if analyzed > 1 else ''} au solveur : "
              f"{num(r.review['lost'], 1)} bb perdus en {plural(r.review['decisions'], 'décision')}." if analyzed else
              "Aucune main passée au solveur pour l'instant : le Leakfinding peut analyser les mains choisies."),
    ]
    y = 4.45
    for suit, text in rows:
        symbol, color = SUITS[suit]
        badge(slide, LEFT, y, 0.42, symbol, fill=CARD, color=color, size=16)
        text_block(slide, LEFT + 0.65, y + 0.06, WIDTH - 0.65, text, 16, INK, max_lines=1, min_size=14)
        y += 0.68
    winrate = r.winrate.get("reg")
    slide.notes = ("Les chiffres de la période. "
                   + (f"Contre les réguliers : {num(winrate, 1, sign=True)} bb/100. " if winrate is not None else "")
                   + "Rappelle que le winrate bouge beaucoup sur peu de mains : les leaks qui suivent comptent plus "
                   "que le résultat.")


def _nice_step(span: float, ticks: int = 4) -> float:
    raw = span / max(ticks, 1)
    power = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    return next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)


def curve_slide(deck: px.Presentation, s: Synthesis, number: int) -> bool:
    points = s.curve(300)
    if len(points) < 3:
        return False
    res = s.result
    slide = deck.add_slide()
    header(slide, "Résultat", f"{num(res['net_bb'], 1, sign=True)} bb sur {num(res['hands'], 0)} mains "
                              f"({num(res['bb100'], 1, sign=True)} bb/100)", number)
    plot_x, plot_y, plot_w, plot_h = LEFT + 0.85, 1.75, WIDTH - 0.95, 4.1
    values = [v for _, net, ev in points for v in (net, ev)] + [0.0]
    lo, hi = min(values), max(values)
    if hi - lo < 1:
        lo, hi = lo - 1, hi + 1
    step = _nice_step(hi - lo)
    lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step
    tick = lo
    while tick <= hi + step / 2:
        y = plot_y + plot_h * (hi - tick) / (hi - lo)
        zero = abs(tick) < step / 1000
        slide.line(plot_x, y, plot_x + plot_w, y, "B9BFB9" if zero else "E3E7E3", 1.25 if zero else 0.75)
        slide.text(LEFT, y - 0.12, 0.75, 0.25, [para(num(tick, 0), 12, MUTED, align="r")], name="Axe")
        tick += step
    n = points[-1][0] or 1
    to_box = lambda i, v: (i / n, (hi - v) / (hi - lo))  # noqa: E731
    slide.path(plot_x, plot_y, plot_w, plot_h, [to_box(i, ev) for i, _, ev in points], AMBER, 1.75, name="EV all-in")
    slide.path(plot_x, plot_y, plot_w, plot_h, [to_box(i, net) for i, net, _ in points], FELT, 2.5,
               name="Résultat réel")
    slide.text(plot_x + plot_w - 2.0, plot_y + plot_h + 0.08, 2.0, 0.25,
               [para(f"{num(n, 0)} mains", 12, MUTED, align="r")], name="Axe")
    x = plot_x + 0.15
    for label, color in (("Résultat réel", FELT), ("EV all-in", AMBER)):
        slide.line(x, plot_y + 0.2, x + 0.4, plot_y + 0.2, color, 3)
        slide.text(x + 0.5, plot_y + 0.08, 1.8, 0.26, [para(label, 12, INK2)], name="Légende")
        x += 0.5 + px.width(label, 12) + 0.45
    luck = res["net_bb"] - res["ev_bb"]
    facts = [f"EV all-in : {num(res['ev_bb'], 1, sign=True)} bb (chance : {num(luck, 1, sign=True)} bb sur "
             f"{res['allin']} all-in)",
             f"Avec / sans abattage : {num(res['sd_bb'], 0, sign=True)} / {num(res['nosd_bb'], 0, sign=True)} bb"]
    slide.text(LEFT, 6.38, WIDTH, 0.3, [Para([Run(facts[0], 14, INK2), Run("   ·   ", 14, MUTED),
                                               Run(facts[1], 14, INK2)])], name="Détail")
    slide.notes = ("La courbe verte est le résultat réel, la jaune l'EV all-in (les tapis payés avant la river comptés "
                   f"à leur espérance). Écart dû à la chance : {num(luck, 1, sign=True)} bb.\n"
                   "Un gros écart entre réel et EV vient de la variance, pas du jeu : c'est le moment de le dire.")
    return True


def leaks_slide(deck: px.Presentation, s: Synthesis, number: int) -> None:
    slide = deck.add_slide()
    header(slide, "Priorités", "Les leaks à travailler", number)
    items = s.report.leaks
    if not items:
        text_block(slide, LEFT, 1.8, WIDTH, "Pas de leak net pour l'instant : pas assez de mains contre les "
                   "réguliers, ou un jeu proche de la théorie dans les situations mesurées.", 20, INK2, max_lines=3)
        slide.notes = "Rien de net : la séance peut porter sur les mains à revoir."
        return
    row = min(1.15, 5.0 / len(items))
    for k, leak in enumerate(items):
        y = 1.65 + k * row
        badge(slide, LEFT, y + 0.04, 0.5, str(k + 1))
        text_block(slide, LEFT + 0.75, y, WIDTH - 2.25, leak.title, 18, INK, bold=True, min_size=15)
        text_block(slide, LEFT + 0.75, y + 0.36, WIDTH - 2.25, first_sentence(leak.evidence), 14, INK2,
                   max_lines=1 if row < 0.9 else 2)
        pill(slide, RIGHT - 1.25, y + 0.05, leak.confidence)
        if k < len(items) - 1:
            slide.line(LEFT + 0.75, y + row - 0.05, RIGHT, y + row - 0.05, RULE, 0.75)
    slide.notes = "\n".join(f"{k}. {x.title} ({x.confidence}) : {x.evidence} {x.advice}"
                            for k, x in enumerate(items, start=1))


def _stat_panel(slide: px.Slide, s: Synthesis, stat: leaks.Stat, x: float, y: float, w: float) -> None:
    scope = s.report.solver_scope
    r = stat.ratios[scope]
    mine = r.hits / r.opps if r.opps else 0.0
    ref = stat.ref_for(scope)
    band = stat.band
    ref_text = f"{round(100 * ref)} %" if ref is not None else (
        f"{round(100 * band[0])}–{round(100 * band[1])} %" if band else "–")
    rows = [("Toi", f"{round(100 * mine)} %", mine, RED), ("Théorie", ref_text,
                                                          ref if ref is not None else (band[1] if band else None), FELT)]
    for k, (label, value, share, color) in enumerate(rows):
        top = y + k * 1.75
        slide.text(x, top, 2.0, 0.3, [para(label.upper(), 12, MUTED, bold=True)], name="Libellé")
        slide.text(x, top + 0.3, w, 0.85, [para(value, 44, color, bold=True)], name="Fréquence")
        slide.shape(x, top + 1.2, w, 0.16, fill="E3E7E3", geom="roundRect", radius=0.08, name="Barre")
        if share:
            slide.shape(x, top + 1.2, max(0.16, w * min(share, 1.0)), 0.16, fill=color, geom="roundRect", radius=0.08,
                        name="Barre")
    words = {"reg": "contre les réguliers", "all": "sur toutes tes mains", "rec": "contre les récréatifs"}
    text_block(slide, x, y + 3.5, w, f"{r.opps} occasions {words.get(scope, '')}, repère : {stat.ref_text()}", 13,
               MUTED, max_lines=2)


def leak_slide(deck: px.Presentation, s: Synthesis, leak: leaks.Leak, k: int, total: int, number: int) -> None:
    slide = deck.add_slide()
    header(slide, f"Leak {k} / {total} · {leak.confidence}", leak.title, number)
    panel_x, panel_w = LEFT, 5.2
    slide.shape(panel_x, 1.6, panel_w, 4.85, fill=CARD, geom="roundRect", radius=0.16, name="Panneau")
    inner_x, inner_w = panel_x + 0.4, panel_w - 0.8
    stat = s.stat_of(leak)
    example = s.example(leak)
    if stat is not None and stat.ratios[s.report.solver_scope].opps:
        _stat_panel(slide, s, stat, inner_x, 1.9, inner_w)
    elif leak.source == "solveur":
        slide.text(inner_x, 1.95, inner_w, 0.3, [para("FACE AU SOLVEUR", 12, MUTED, bold=True)])
        slide.text(inner_x, 2.3, inner_w, 0.9, [para(leak.evidence.split(" bb")[0] + " bb", 44, RED, bold=True)])
        text_block(slide, inner_x, 3.3, inner_w, "perdus" + leak.evidence.split(" bb", 1)[1], 16, INK2, max_lines=4)
    else:
        slide.text(inner_x, 1.95, inner_w, 0.3, [para("CE QUE DISENT TES MAINS", 12, MUTED, bold=True)])
        text_block(slide, inner_x, 2.35, inner_w, leak.evidence, 16, INK2, max_lines=9, min_size=14)
    if example is not None and (stat is not None or leak.source == "solveur"):
        hand, lost = example
        _example(slide, s, hand, lost, inner_x, 5.35, inner_w, leak.source == "solveur")
    right_x, right_w = LEFT + panel_w + 0.5, WIDTH - panel_w - 0.5
    y = 1.65
    if stat is not None or leak.source == "solveur":
        slide.text(right_x, y, right_w, 0.3, [para("CE QUE DISENT TES MAINS", 12, MUTED, bold=True)])
        y += 0.38
        y += text_block(slide, right_x, y, right_w, leak.evidence, 18, INK2, max_lines=5, min_size=14) + 0.35
    elif example is not None:
        hand, lost = example
        slide.text(right_x, y, right_w, 0.3, [para("LA MAIN LA PLUS CHÈRE", 12, MUTED, bold=True)])
        _example(slide, s, hand, lost, right_x, y + 0.4, right_w, leak.source == "solveur")
        y += 1.4
    slide.text(right_x, y, right_w, 0.3, [para("À TRAVAILLER", 12, AMBER, bold=True)])
    y += 0.38
    advice, size, lines = fit(leak.advice, right_w - 0.5, 20, False, 6, 15)
    box_h = lines * px.line_height(size) + 0.5
    slide.shape(right_x, y, right_w, box_h, fill="FFF6E0", geom="roundRect", radius=0.12,
                paras=[para(advice, size, FELT, bold=False)], anchor="ctr", inset=0.25, name="Conseil")
    slide.notes = (f"{leak.title} ({leak.confidence}).\n{leak.evidence}\nÀ travailler : {leak.advice}\n"
                   "Questions à poser : qu'est-ce qui te fait jouer ainsi dans cette situation ? Que ferait un "
                   "régulier solide ? Puis rejouez ensemble la main la plus chère dans l'explorateur ou l'entraîneur.")


def _example(slide: px.Slide, s: Synthesis, hand: Hand, lost: float, x: float, y: float, w: float,
             solver: bool) -> None:
    """Une main exemple : ses cartes et le board, sa date et ce qu'elle a coûté (EV perdue face au solveur, ou
    résultat du coup)."""
    hero_cards = hand.hole_cards.get(s.report.hero, [])
    runs = card_runs(hero_cards, 22) + ([Run("  sur  ", 16, MUTED)] + card_runs(hand.board, 22) if hand.board else [])
    slide.text(x, y, w, 0.42, [Para(runs)], name="Main")
    cost = f"{num(lost, 2)} bb perdus face au solveur" if solver else f"{num(-lost, 1, sign=True)} bb sur ce coup"
    slide.text(x, y + 0.48, w, 0.3, [para(f"{hand.date:%d/%m/%Y %H:%M} · {cost}", 12, MUTED)], name="Détail")


def gaps_slide(deck: px.Presentation, s: Synthesis, number: int) -> bool:
    found = s.gaps(6)
    if not found:
        return False
    slide = deck.add_slide()
    header(slide, "Face à la théorie", "Les écarts les plus importants", number)
    scope = s.report.solver_scope
    widths = [5.4, 1.6, 1.6, 1.4, WIDTH - 10.0]
    head = [Cell([para(t, 14, ON_FELT, bold=True, align="r" if k in (1, 2, 3) else "l")], fill=FELT)
            for k, t in enumerate(("Situation", "Toi", "Théorie", "Écart", "Confiance"))]
    rows, heights = [head], [0.5]
    for stat, direction, confidence in found:
        r = stat.ratios[scope]
        reference = (f"{round(100 * stat.reference)} %" if stat.reference is not None else
                     f"{round(100 * stat.band[0])}–{round(100 * stat.band[1])} %" if stat.band else "–")
        label, size, _ = fit(stat.label, widths[0] - 0.2, 14, True, 1, 12)
        rows.append([
            Cell([para(label, size, INK, bold=True), para(stat.section, 11, MUTED)]),
            Cell([para(f"{round(100 * r.hits / r.opps)} % ({r.opps})", 14, INK, bold=True, align="r")],
                 fill=HI_BG if direction == "plus" else LO_BG),
            Cell([para(reference, 14, INK2, align="r")]),
            Cell([para(f"{'+' if direction == 'plus' else '−'}{round(100 * stat.gap(scope))} pts", 14, INK, align="r")]),
            Cell([para(confidence, 14, RED if confidence == "solide" else BLUE, bold=True)]),
        ])
        heights.append(0.68)
    slide.table(LEFT, 1.6, widths, rows, heights)
    who = "contre les réguliers" if scope == "reg" else "sur toutes tes mains"
    slide.text(LEFT, 1.6 + sum(heights) + 0.15, WIDTH, 0.3, [para(
        f"Ta fréquence {who} (et le nombre d'occasions), face à la théorie. Orange : trop souvent ; bleu : pas assez.",
        12, MUTED)], name="Légende")
    slide.notes = "\n".join(
        f"{st.label} ({st.section}) : {round(100 * st.ratios[scope].hits / st.ratios[scope].opps)} % contre "
        f"{st.ref_text()}, écart {confidence}." for st, _, confidence in found)
    return True


def solver_slide(deck: px.Presentation, s: Synthesis, number: int) -> bool:
    rv = s.report.review
    groups = s.situations(5)
    if not rv["analyzed"] or not groups:
        return False
    slide = deck.add_slide()
    header(slide, "Face au solveur", f"{num(rv['lost'], 1)} bb perdus en {rv['decisions']} décisions", number)
    widths = [6.9, 1.4, 1.4, 2.4]
    head = [Cell([para(t, 14, ON_FELT, bold=True, align="l" if k == 0 else "r")], fill=FELT)
            for k, t in enumerate(("Situation", "Fois", "Erreurs", "EV perdue"))]
    rows, heights = [head], [0.5]
    for g in groups:
        label, size, _ = fit(g["label"], widths[0] - 0.2, 14, True, 1, 12)
        rows.append([Cell([para(label, size, INK, bold=True), para(leaks.family_name(g["family"]), 11, MUTED)]),
                     Cell([para(str(g["n"]), 14, INK2, align="r")]), Cell([para(str(g["errors"]), 14, INK2, align="r")]),
                     Cell([para(f"{num(g['lost'], 1)} bb", 14, RED if g["lost"] >= 0.5 else INK, bold=True,
                                align="r")])])
        heights.append(0.68)
    slide.table(LEFT, 1.6, widths, rows, heights)
    slide.text(LEFT, 1.6 + sum(heights) + 0.15, WIDTH, 0.3, [para(
        f"Sur {rv['analyzed']} mains passées au solveur : chaque décision comparée à la meilleure action pour sa main "
        "exacte.", 12, MUTED)])
    slide.notes = "\n".join(f"{g['label']} ({leaks.family_name(g['family'])}) : {num(g['lost'], 1)} bb perdus en "
                            f"{g['known']} décisions, {g['errors']} erreur(s)." for g in groups)
    return True


def picks_slide(deck: px.Presentation, s: Synthesis, number: int) -> bool:
    picks = s.picks(6)
    if not picks:
        return False
    slide = deck.add_slide()
    header(slide, "À revoir ensemble", "Les mains à revoir", number)
    widths = [3.6, 1.5, 2.8, 1.1, 1.3, WIDTH - 10.3]
    head = [Cell([para(t, 14, ON_FELT, bold=True, align="r" if k in (3, 4) else "l")], fill=FELT)
            for k, t in enumerate(("Ligne", "Main", "Board", "Pot", "Résultat", "Solveur"))]
    rows, heights = [head], [0.5]
    for p in picks:
        against = "contre un récréatif" if p.kind == "rec" else "contre un régulier"
        rows.append([
            Cell([para(p.line, 14, INK, bold=True), para(f"{p.hand.date:%d/%m %H:%M} · {against}", 11, MUTED)]),
            Cell([Para(card_runs(p.hand.hole_cards.get(s.report.hero, []), 15))]),
            Cell([Para(card_runs(p.hand.board, 15))]),
            Cell([para(f"{num(p.pot_bb, 0)} bb", 14, INK2, align="r")]),
            Cell([para(f"{num(p.net_bb, 1, sign=True)} bb", 14, tone(p.net_bb), bold=True, align="r")]),
            Cell([para(pick_status(p), 12, INK2)]),
        ])
        heights.append(0.68)
    slide.table(LEFT, 1.6, widths, rows, heights)
    slide.notes = ("Choisis-en deux ou trois et rejouez-les : que faire à chaque street, et pourquoi ? Les mains « à "
                   "analyser » peuvent passer au solveur depuis le Leakfinding avant la séance.\n"
                   + "\n".join(f"{p.hand.hand_id} : {p.line}, {num(p.net_bb, 1, sign=True)} bb ({pick_status(p)})"
                               for p in picks))
    return True


def rec_slide(deck: px.Presentation, s: Synthesis, number: int) -> bool:
    notes = s.report.rec_notes
    if not notes:
        return False
    slide = deck.add_slide()
    header(slide, "Contre les récréatifs", "Exploiter les récréatifs", number)
    y = 1.7
    for k, note in enumerate(notes[:4]):
        symbol, color = SUITS["hdcs"[k % 4]]
        badge(slide, LEFT, y, 0.5, symbol, fill=CARD, color=color, size=18)
        y += max(text_block(slide, LEFT + 0.8, y + 0.05, WIDTH - 0.8, note, 18, INK, max_lines=3, min_size=15),
                 0.5) + 0.35
    slide.notes = "Contre un récréatif, s'écarter de la théorie n'est pas une erreur : c'est ce qui rapporte."
    return True


def plan_slide(deck: px.Presentation, s: Synthesis, number: int) -> None:
    slide = deck.add_slide(FELT)
    slide.text(LEFT, 0.38, WIDTH, 0.3, [para("PLAN DE TRAVAIL", 12, AMBER, bold=True)])
    slide.text(LEFT, 0.66, WIDTH, 0.7, [para("D'ici la prochaine séance", 32, ON_FELT, bold=True)])
    items = s.report.leaks[:3]
    bottom = 1.9
    if items:
        gap = 0.3
        w = (WIDTH - gap * (len(items) - 1)) / len(items)
        inner = w - 0.7
        title_size = common_size([x.title for x in items], inner, 18, True, 2, 14)
        advice_size = common_size([x.advice for x in items], inner, 15, False, 7, 13)
        texts = [(fit(x.title, inner, title_size, True, 3), fit(x.advice, inner, advice_size, False, 7)) for x in items]
        height = max(0.35 + 0.55 + 0.3 + t[2] * px.line_height(t[1]) + 0.12 + a[2] * px.line_height(a[1]) + 0.35
                     for t, a in texts)
        height = max(height, 2.4)
        for k, (leak, (title, advice)) in enumerate(zip(items, texts)):
            x = LEFT + k * (w + gap)
            slide.shape(x, 1.75, w, height, fill=FELT_2, geom="roundRect", radius=0.16, name="Objectif")
            badge(slide, x + 0.35, 2.1, 0.55, str(k + 1))
            title_h = title[2] * px.line_height(title[1]) + 0.04
            slide.text(x + 0.35, 2.95, inner, title_h, [para(title[0], title[1], ON_FELT, bold=True)], name="Objectif")
            slide.text(x + 0.35, 2.95 + title_h + 0.08, inner, advice[2] * px.line_height(advice[1]) + 0.04,
                       [para(advice[0], advice[1], ON_FELT_2)], name="Conseil")
        bottom = 1.75 + height + 0.45
    else:
        text_block(slide, LEFT, 1.9, WIDTH, "Pas de leak net : on travaille les mains à revoir et on refait le point "
                   "sur les prochaines mains.", 20, ON_FELT_2, max_lines=2)
        bottom = 3.0
    text_block(slide, LEFT, min(bottom, 6.3), WIDTH, "Ensuite : rejouer les mains à revoir dans l'entraîneur, puis "
               "refaire ce bilan sur les prochaines mains (période d'analyse).", 16, ON_FELT_2, max_lines=2,
               min_size=14)
    slide.text(RIGHT - 1.0, 7.0, 1.0, 0.25, [para(str(number), 10, "9DBFAF", align="r")])
    slide.notes = ("Fais reformuler les objectifs à l'élève avec ses propres mots, et fixez ensemble la date du "
                   "prochain point.\n" + "\n".join(f"{k}. {x.title} : {x.advice}" for k, x in enumerate(items, start=1)))


def build_pptx(s: Synthesis) -> bytes:
    """La présentation d'une synthèse."""
    deck = px.Presentation(title=f"Leakfinding — {s.who}")
    title_slide(deck, s)
    numbers_slide(deck, s, 2)
    number = 3
    if curve_slide(deck, s, number):
        number += 1
    leaks_slide(deck, s, number)
    number += 1
    top = s.report.leaks[:MAX_LEAK_SLIDES]
    for k, leak in enumerate(top, start=1):
        leak_slide(deck, s, leak, k, len(top), number)
        number += 1
    for build in (gaps_slide, solver_slide, picks_slide, rec_slide):
        if build(deck, s, number):
            number += 1
    plan_slide(deck, s, number)
    return deck.output(s.generated)
