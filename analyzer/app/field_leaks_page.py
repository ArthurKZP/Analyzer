"""Étude du field : le leakfinding de tes adversaires (analyzer/field.py).

- Réguliers et Récréatifs : ceux que tu croises le plus (Paramètres : mains minimum), avec leurs leaks à exploiter et
  ce que montrent leurs lignes ; les récréatifs sont aussi rangés par style (passifs, agressifs, prudents).
- La fiche d'un joueur : tous ses leaks, sa value et ses bluffs ligne par ligne (ce qui les distingue), ce qu'il
  montre quand il checke, ses mains montrées avant le flop.
- La fiche d'un groupe de récréatifs : ses joueurs, son plan, ses lignes.
"""
from __future__ import annotations

from collections import Counter
from html import escape
from typing import Optional
from urllib.parse import quote

from .. import field
from ..field import BLUFF, INTENT_LABELS, STYLES, Study
from ..lines import BOARD_SIZE
from ..models import POSTFLOP
from ..report import PASSIVE_LABELS, cards_html, composition_html, html_page, num
from .leaks_page import KIND_SCRIPT

STYLE = """
.fl-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.fl-card { border: 1px solid var(--border); border-radius: 10px; background: var(--surface); padding: 12px 14px;
  display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.fl-card header { display: flex; flex-wrap: wrap; gap: 2px 10px; align-items: baseline; }
.fl-card h3 { margin: 0; font-size: 16px; overflow-wrap: anywhere; }
.fl-card h3 a { text-decoration: none; color: var(--ink); }
.fl-card h3 a:hover { text-decoration: underline; }
.fl-sub { font-size: 12px; color: var(--muted); }
.fl-style { font-size: 11px; border-radius: 999px; padding: 0 8px; border: 1px solid var(--border); white-space: nowrap; }
.fl-style.s-passif { background: var(--lo-bg); } .fl-style.s-agressif { background: var(--hi-bg); }
.fl-style.s-prudent { background: var(--page); }
.fl-leaks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.fl-leaks li { border-left: 3px solid var(--axis); padding: 1px 0 1px 9px; font-size: 13px; }
.fl-leaks li.strong { border-left-color: var(--series-1); }
.fl-leaks .t { font-weight: 600; color: var(--ink); }
.fl-leaks .n { font-size: 12px; color: var(--muted); }
.fl-leaks .x { color: var(--ink-2); }
.fl-tell { font-size: 13px; color: var(--ink-2); }
.fl-tell b { color: var(--ink); }
.fl-more { margin-top: auto; font-size: 13px; font-weight: 600; text-decoration: none; }
.fl-empty { font-size: 13px; color: var(--muted); }
.fl-plan { margin: 0; padding-left: 18px; }
.fl-plan li { margin: 3px 0; }
table.fl td.v-plus { background: var(--hi-bg); }
table.fl td.v-moins { background: var(--lo-bg); }
table.fl td.small, .fl-small { font-size: 12px; color: var(--ink-2); }
table.fl tr.street td { font-weight: 600; background: var(--page); }
.fl-tells { margin: 0; padding-left: 16px; font-size: 12px; color: var(--ink-2); }
.fl-kind { display: inline-flex; gap: 6px; align-items: center; font-size: 13px; }
.fl-kind select { font: inherit; font-size: 13px; padding: 2px 6px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--page); color: var(--ink); }
.fl-combos { font-size: 13px; line-height: 1.7; }
.fl-combos span { display: inline-block; margin-right: 6px; }
"""

FORMAT_WORDS = {"HU": "en heads-up", "ring": "aux tables à plusieurs"}
MAX_CARDS = 24     # joueurs en fiche dans une liste ; les autres en tableau
MAX_LINES = 12     # lignes par street dans une fiche (les autres repliées)


def player_href(name: str, fmt: str) -> str:
    return f"/field/joueur/{quote(name, safe='')}?format={quote(fmt)}"


def group_href(style: str, fmt: str) -> str:
    return f"/field/groupe/{quote(style)}?format={quote(fmt)}"


def _result(row: dict, fmt: str) -> str:
    where = "dans vos pots" if fmt != "HU" else ""
    return (f"ton résultat {num(row['net_bb'], 1, sign=True)} bb {where}".strip()
            + (f" ({num(row['bb100'], 1, sign=True)} bb/100)" if fmt == "HU" else ""))


def _style_badge(study: Study) -> str:
    key = study.style[0]
    if key == "autre":
        return ""
    reasons = ", ".join(study.style[1])
    return f'<span class="fl-style s-{key}" title="{escape(reasons)}">{escape(STYLES[key].word)}</span>'


def leaks_list(study: Study, n: Optional[int] = None) -> str:
    """Ses leaks à exploiter : constat, chiffres, exploit (les solides en bleu)."""
    found = study.top_leaks(n) if n else sorted(study.leaks, key=lambda f: (not f.strong, -f.score))
    if not found:
        return ('<p class="fl-empty">Pas de leak net pour l\'instant : des fréquences proches de celles d\'un régulier, '
                "ou trop peu d'occasions.</p>")
    items = []
    for f in found:
        fact, numbers, exploit = field.finding_text(f)
        conf = "" if f.strong else ' <span class="conf c-à-confirmer">à confirmer</span>'
        items.append(f'<li class="{"strong" if f.strong else ""}"><span class="t">{escape(fact)}</span>{conf}'
                     f'<div class="n">{escape(numbers)}</div><div class="x">→ {escape(_cap(exploit))}</div></li>')
    return f'<ul class="fl-leaks">{"".join(items)}</ul>'


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _street(street: str) -> str:
    return {"flop": "Flop", "turn": "Turn", "river": "River"}[street]


def key_lines_html(study: Study, n: int = 2) -> str:
    rows = [f'<div class="fl-tell"><b>{_street(ln.street)}, {escape(ln.name.lower())}</b> : '
            f'{escape(_lower_first(v.text))}</div>' for ln, v in study.key_lines(n)]
    return "".join(rows)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def player_card(row: dict, study: Study, fmt: str) -> str:
    """La carte d'un joueur dans une liste : ses chiffres, ses leaks principaux, ce que disent ses lignes."""
    name = row["name"]
    head = (f'<header><h3><a href="{escape(player_href(name, fmt))}">{escape(name)}</a></h3>{_style_badge(study)}'
            f'<span class="fl-sub">{num(row["hands"], 0)} mains · {escape(_result(row, fmt))}</span></header>')
    return (f'<article class="fl-card">{head}{leaks_list(study, field.LEAKS_SHOWN)}{key_lines_html(study)}'
            f'<a class="fl-more" href="{escape(player_href(name, fmt))}">Analyse détaillée →</a></article>')


def _others_table(rows: list[tuple[dict, Study]], fmt: str) -> str:
    body = []
    for row, study in rows:
        top = study.top_leaks(1)
        leak = escape(field.finding_text(top[0])[0]) if top else '<span class="muted">–</span>'
        body.append(f'<tr><td><a href="{escape(player_href(row["name"], fmt))}">{escape(row["name"])}</a></td>'
                    f'<td class="num">{num(row["hands"], 0)}</td><td>{leak}</td></tr>')
    return (f'<details><summary>Les {len(rows)} autres</summary><div class="scroll"><table class="stats fl"><thead><tr>'
            '<th>Joueur</th><th class="num">Mains</th><th>Son leak le plus net</th></tr></thead><tbody>'
            + "".join(body) + "</tbody></table></div></details>")


def cards_html_list(rows: list[tuple[dict, Study]], fmt: str) -> str:
    if not rows:
        return ""
    cards = "".join(player_card(row, study, fmt) for row, study in rows[:MAX_CARDS])
    rest = _others_table(rows[MAX_CARDS:], fmt) if len(rows) > MAX_CARDS else ""
    return f'<div class="fl-cards">{cards}</div>{rest}'


def _tiles(items: list[tuple[str, str, str]]) -> str:
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="label">{escape(label)}</div><div class="value">{value}</div>'
        f'<div class="sub">{sub}</div></div>' for label, value, sub in items) + "</div>"


def _pct(r) -> str:
    return "–" if r is None or not r.opps else f"{r.pct:.0f} %"


def _num(r) -> str:
    return "–" if r is None or not r.opps else f"{r.pct:.0f}"


def style_tiles(study: Study, hands_label: str = "Mains") -> list[tuple[str, str, str]]:
    r = study.ratios
    return [(hands_label, num(study.hands, 0), ""),
            ("VPIP / PFR", f"{_num(r.get('vpip'))} / {_num(r.get('pfr'))}", "% des mains jouées / relancées avant le flop"),
            ("Agressivité", _pct(r.get("afq")), "mises et relances après le flop, parmi ses actions"),
            ("Fold face aux mises", _pct(r.get("fold_bet")), "après le flop"),
            ("Abattage", _pct(r.get("wtsd")), "quand il voit le flop")]


def build_list_page(kind: str, fmt: str, rows: list[tuple[dict, Study]], population: Optional[Study],
                    min_hands: int, total: int, switch: str = "", groups: Optional[list[dict]] = None,
                    embed: bool = True) -> str:
    """Réguliers (kind « reg ») ou récréatifs (« rec ») d'un format : rows, ceux qui ont assez de mains (du plus joué
    au moins joué), chacun avec son étude ; population : eux tous ensemble ; total : tous les joueurs de ce type ;
    groups (récréatifs) : [{"style", "study", "members": [rows]}]."""
    regs = kind == "reg"
    who = "réguliers" if regs else "récréatifs"
    where = FORMAT_WORDS[fmt]
    if not rows:
        body = (f'<div class="meta">Les {who} que tu croises le plus {where}.</div>{switch}'
                f'<div class="card"><p class="note" style="margin:0">Aucun {who[:-1]} avec au moins {min_hands} mains '
                f'{"ensemble" if fmt != "HU" else "contre toi"} pour l\'instant ({total} au total). Le seuil se règle '
                "dans Paramètres.</p></div>")
        return html_page(f"Field — {who}", f"<style>{STYLE}</style>{body}", embed)
    hands = sum(row["hands"] for row, _ in rows)
    solid = sum(1 for _, s in rows for f in s.leaks if f.strong)
    tiles = _tiles([(f"{_cap(who)} étudiés", str(len(rows)), f"sur {total}, au moins {min_hands} mains"),
                    ("Mains", num(hands, 0), "ensemble" if fmt != "HU" else "contre eux"),
                    ("Leaks solides", str(solid), "écarts que le hasard explique mal")])
    pop = ""
    if population is not None and len(rows) > 1:
        lines = key_lines_html(population, 3)
        lines = f'<div style="margin-top:8px">{lines}</div>' if lines else ""
        pop = (f'<h2>Les {who} en général</h2><div class="card">{leaks_list(population, 4)}{lines}'
               f'<p class="note">Leurs fréquences réunies ({num(population.hands, 0)} mains), face aux repères d\'un '
               "régulier solide ; et ce que montrent leurs lignes à l'abattage.</p></div>")
    groups_html = _groups_html(groups or [], fmt) if not regs else ""
    body = f"""<div class="meta">Les {who} que tu croises le plus {where} (au moins {min_hands} mains) : leurs leaks à
exploiter, d'après leurs fréquences et leurs mains montrées. Le détail de chacun s'ouvre d'un clic.</div>
{switch}
{tiles}
{groups_html}
{pop}
<h2>Joueur par joueur</h2>
{cards_html_list(rows, fmt)}
<p class="note">Un leak : une fréquence nettement hors des repères d'un régulier solide (intervalle de confiance à 90 %
hors du repère, au moins 15 occasions) ; « à confirmer » : l'écart est net, l'échantillon encore petit. À l'abattage :
la part de mises sans main faite dans une ligne (à la river, face à l'équité qu'il te faut pour payer).
{"Toutes les mains allées à l'abattage comptent, même celles où tu n'étais plus." if fmt != "HU" else ""}
Le type de chaque joueur se règle dans Paramètres › Joueurs et alias.</p>
"""
    return html_page(f"Field — {who}", f"<style>{STYLE}</style>{body}", embed)


def _groups_html(groups: list[dict], fmt: str) -> str:
    """Les récréatifs par style : membres, leaks communs, plan."""
    cards = []
    for g in groups:
        style, study, members = STYLES[g["style"]], g["study"], g["members"]
        names = ", ".join(escape(m["name"]) for m in members[:6]) + (
            f" et {len(members) - 6} autres" if len(members) > 6 else "")
        plan = "".join(f"<li>{escape(step)}</li>" for step in style.plan[:3])
        leaks = leaks_list(study, 2) if g["style"] != "autre" else ""
        cards.append(f'<article class="fl-card"><header><h3><a href="{escape(group_href(g["style"], fmt))}">'
                     f'{escape(style.name)}</a></h3><span class="fl-sub">{len(members)} joueur(s) · '
                     f'{num(study.hands, 0)} mains</span></header><div class="fl-small">{names}</div>'
                     f'<ul class="fl-plan">{plan}</ul>{leaks}'
                     f'<a class="fl-more" href="{escape(group_href(g["style"], fmt))}">Le groupe en détail →</a></article>')
    if not cards:
        return ""
    return (f'<h2>Par style</h2><div class="fl-cards">{"".join(cards)}</div>'
            '<p class="note">Le style vient de leurs fréquences : passifs (peu d\'agressivité après le flop, ils paient '
            "beaucoup), agressifs (beaucoup de mises et de relances), prudents (ils foldent beaucoup face aux mises ou "
            "jouent peu de mains).</p>")


# --- Fiche d'un joueur ou d'un groupe -----------------------------------------------------------------------------

def _size(b) -> str:
    return f"{b.pct:.0f} %" if b.pct is not None else "relance"


def differences_html(study: Study, plural: bool = False) -> str:
    """Ce qui distingue sa value de ses bluffs : street par street (taille, temps, carte, nombre de joueurs), puis les
    lignes qui disent le plus clairement value ou bluff."""
    items = [f"<li><b>{_street(street)}</b> : {escape(t)}</li>"
             for street, found in study.street_tells().items() for t in found]
    items += [f"<li><b>{_street(ln.street)}, {escape(ln.name.lower())}</b> : {escape(_lower_first(v.text))}</li>"
              for ln, v in study.key_lines(4)]
    who, has = ("leurs", "ils montrent") if plural else ("ses", "il montre")
    if not items:
        return (f'<p class="note" style="margin-top:0">Pas encore de différence nette entre {who} value et {who} bluffs : '
                f"il faut des mains montrées de chaque sorte dans une même street ({study.shown} mise(s) montrée(s) pour "
                "l'instant).</p>")
    return (f'<ul class="fl-plan">{"".join(items)}</ul><p class="note">D\'après les mises qu\'{has} à l\'abattage : au '
            "moins deux mains de value et deux bluffs (ou semi-bluffs) à une street pour comparer leurs tailles, leurs "
            "temps de réflexion et les cartes tombées.</p>")


def lines_html(study: Study, plural: bool = False) -> str:
    """Ses lignes, street par street : fois, montrées, ce qu'il montre, tailles de sa value et de ses bluffs, ce qui
    les distingue, ce qu'il faut en faire ; les lignes jamais montrées ensemble, en fin de street."""
    if not study.bets:
        return '<p class="note">Aucune mise après le flop pour l\'instant.</p>'
    rows = []
    for street in POSTFLOP:
        lines = [ln for ln in study.lines if ln.street == street]
        if not lines:
            continue
        rows.append(f'<tr class="street"><td colspan="6">{_street(street)}</td></tr>')
        shown = sorted((ln for ln in lines if ln.shown), key=lambda ln: (-len(ln.shown), -ln.count))
        for ln in shown[:MAX_LINES]:
            v = field.verdict(ln)
            value = [b.pct for b in ln.shown if b.intent in field.VALUE and b.pct is not None]
            bluff = [b.pct for b in ln.shown if b.intent in BLUFF and b.pct is not None]
            sizes = " · ".join(part for part in (
                f"value {sum(value) / len(value):.0f} %" if value else "",
                f"bluff {sum(bluff) / len(bluff):.0f} %" if bluff else "") if part) or "–"
            tells = field.tells(ln.bets)
            tells_html = (f'<ul class="fl-tells">{"".join(f"<li>{escape(t)}</li>" for t in tells)}</ul>'
                          if tells else "")
            rows.append(f'<tr><td>{escape(ln.name)}</td><td class="num">{ln.count}</td>'
                        f'<td class="compcell">{composition_html(ln.intents, len(ln.shown), INTENT_LABELS)}</td>'
                        f'<td class="small">{sizes}</td><td class="small">{tells_html or "–"}</td>'
                        f'<td class="small v-{v.kind or "none"}">{escape(v.text)}</td></tr>')
        never = [ln for ln in lines if not ln.shown] + shown[MAX_LINES:]
        if never:
            names = ", ".join(f"{escape(ln.name.lower())} ({ln.count})" for ln in never)
            rows.append(f'<tr><td colspan="6" class="small">Jamais ou peu montrées : {names}</td></tr>')
    who = "leurs" if plural else "ses"
    return (f'<div class="scroll"><table class="stats fl"><thead><tr><th>Ligne</th><th class="num">Fois</th>'
            f'<th>À l\'abattage</th><th>Taille moyenne</th><th>Ce qui distingue value et bluff</th><th>Verdict</th>'
            '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
            f'<div class="legend-int">{"".join(_legend(k) for k in ("value", "thin", "semi", "bluff"))}</div>'
            f'<p class="note">Chaque mise ou relance après le flop, rangée par ligne et par taille ; quand {who} cartes '
            "sont montrées, l'intention au moment de miser : value (top paire ou mieux), value fine (paire moyenne ou "
            "faible), semi-bluff (un tirage, sans main faite, avant la river), bluff (rien). Biais : une mise n'est vue "
            "que si le coup va à l'abattage ; à la river, la décision de payer ne dépend pas de ses cartes, "
            "l'échantillon est honnête. Verdict à la river : la part de bluffs face à l'équité qu'il te faut pour payer "
            "(taille médiane de la ligne).</p>")


def _legend(key: str) -> str:
    return f'<span><i class="i-{key}"></i>{INTENT_LABELS[key]}</span>'


def shown_examples(study: Study, limit: int = 40) -> str:
    """Ses mises montrées, les plus récentes d'abord."""
    shown = sorted((b for b in study.bets if b.intent), key=lambda b: b.hand.date, reverse=True)
    if not shown:
        return ""
    many = len(study.names) > 1
    rows = []
    for b in shown[:limit]:
        board = b.hand.board[:BOARD_SIZE[b.street]]
        who = f"<td>{escape(b.player)}</td>" if many else ""
        think = f"{b.think:.0f} s" if b.think is not None else "–"
        rows.append(f'<tr>{who}<td>{_street(b.street)}</td><td>{escape(b.label)}</td><td class="num">{_size(b)}</td>'
                    f'<td>{cards_html(board)}</td><td>{cards_html(b.hand.hole_cards[b.player])}</td>'
                    f'<td><span class="dot i-{b.intent}"></span>{INTENT_LABELS[b.intent]}</td>'
                    f'<td class="small">{escape(b.description)}</td><td class="num small">{think}</td></tr>')
    head = "<th>Joueur</th>" if many else ""
    return (f'<details class="inner"><summary>Les mises montrées ({len(shown)})</summary><div class="inner-body">'
            f'<div class="scroll"><table class="stats fl"><thead><tr>{head}<th>Street</th><th>Ligne</th>'
            '<th class="num">Taille</th><th>Board</th><th>Cartes</th><th>Intention</th><th>Main faite</th>'
            '<th class="num">Temps</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div></div></details>")


def checks_html(study: Study, plural: bool = False) -> str:
    rows = []
    for street in POSTFLOP:
        c = study.checks.get(street) or Counter()
        n = sum(c.values())
        if not n:
            continue
        strong = c["value"]
        note = ""
        if n >= 5 and strong / n >= 0.35:
            note = ("souvent une main forte : attention aux pièges (check-raise, check-call)" if street != "river"
                    else "souvent une main forte : ne mise pas fin derrière")
        elif n >= 5 and (c["bluff"] + c["semi"]) / n >= 0.6:
            note = "surtout rien : mise derrière quand il checke" if not plural else "surtout rien : mise derrière"
        rows.append(f'<tr><td>{_street(street)}</td><td class="num">{n}</td>'
                    f'<td class="compcell">{composition_html(c, n, PASSIVE_LABELS)}</td>'
                    f'<td class="small">{escape(note) or "–"}</td></tr>')
    if not rows:
        return '<p class="note">Rien de montré après un check pour l\'instant.</p>'
    return ('<div class="scroll"><table class="stats fl"><thead><tr><th>Street checkée</th><th class="num">Montrées</th>'
            '<th>Main montrée</th><th>À retenir</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def preflop_html(study: Study, limit: int = 8) -> str:
    if not study.preflop:
        return '<p class="note">Aucune main montrée pour l\'instant.</p>'
    rows = []
    for line, items in list(study.preflop.items())[:limit]:
        counts = Counter(combo for combo, _ in items)
        combos = " ".join(f"<span>{escape(c)}{' ×' + str(k) if k > 1 else ''}</span>" for c, k in counts.most_common())
        rows.append(f'<tr><td>{escape(line)}</td><td class="num">{len(items)}</td>'
                    f'<td class="fl-combos">{combos}</td></tr>')
    return ('<div class="scroll"><table class="stats fl"><thead><tr><th>Ligne avant le flop</th><th class="num">Montrées</th>'
            '<th>Mains</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _kind_select(name: str, info: dict) -> str:
    names = {"reg": "Régulier", "rec": "Récréatif"}
    auto = f"Auto : {names[info['suggestion']]}" if info.get("suggestion") else "Auto : Régulier"
    options = "".join(
        f'<option value="{v}"{" selected" if (info.get("source") == "toi" and info["kind"] == v) or (info.get("source") != "toi" and not v) else ""}>'
        f'{escape(t)}</option>' for v, t in (("", auto), ("reg", "Régulier"), ("rec", "Récréatif")))
    return (f'<label class="fl-kind">Type <select class="lk-kind" data-name="{escape(name)}" '
            f'aria-label="Type de {escape(name)}">{options}</select></label>')


def build_player_page(row: dict, study: Study, fmt: str, info: dict, switch: str = "", fiche: str = "",
                      embed: bool = True) -> str:
    """La fiche d'un adversaire : row (mains, ton résultat), study (field.study), info (son type : players.classify),
    fiche : le lien vers sa fiche heads-up (plan de jeu, rapport, bluffs)."""
    name = row["name"]
    style_key, reasons = study.style
    style = "" if style_key == "autre" else f" · style {STYLES[style_key].word} ({', '.join(reasons)})"
    kind = "Récréatif" if info["kind"] == "rec" else "Régulier"
    tiles = style_tiles(study)[1:]
    tiles.insert(0, ("Ton résultat", f"{num(row['net_bb'], 1, sign=True)} bb",
                     "dans vos pots disputés" if fmt != "HU" else f"{num(row['bb100'], 1, sign=True)} bb/100"))
    more = (f'<p style="margin:0">Sa fiche heads-up (plan de jeu, rapport complet, ses bluffs face au solveur) : '
            f'<a href="{escape(fiche)}" target="_top">ouvrir</a>.</p>' if fiche else "")
    hands = f"{num(study.hands, 0)} mains {'à la même table' if fmt != 'HU' else 'contre toi'}"
    body = f"""<div class="meta">{escape(name)} {FORMAT_WORDS[fmt]} · {hands} · {kind}{escape(style)}</div>
{switch}
<div class="card" style="display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center">{_kind_select(name, info)}{more}</div>
{_tiles(tiles)}
<h2>Ses leaks à exploiter</h2>
<div class="card">{leaks_list(study)}
<p class="note">Ses fréquences nettement hors des repères d'un régulier solide, de la plus marquée à la moins marquée,
et ce qu'il faut en faire. « À confirmer » : l'écart est net, l'échantillon encore petit.</p></div>
<h2>Value ou bluff ?</h2>
<div class="card"><h3 style="margin-top:0">Ce qui distingue sa value de ses bluffs</h3>{differences_html(study)}</div>
<div class="card" style="margin-top:12px"><h3 style="margin-top:0">Ses lignes</h3>{lines_html(study)}{shown_examples(study)}</div>
<h2>Quand il checke</h2>
<div class="card">{checks_html(study)}
<p class="note">Ce qu'il montre à l'abattage après avoir checké une street (sans y miser ensuite) : des mains fortes
cachées dans ses checks, ou rien.</p></div>
<h2>Ses mains montrées avant le flop</h2>
<div class="card">{preflop_html(study)}</div>
"""
    return html_page(f"{name} — field", f"<style>{STYLE}</style>{body}", embed, script=KIND_SCRIPT)


def build_group_page(style_key: str, fmt: str, study: Study, members: list[tuple[dict, Study]], switch: str = "",
                     embed: bool = True) -> str:
    """La fiche d'un groupe de récréatifs (un style) : son plan, ses joueurs, ses leaks et ses lignes réunis."""
    style = STYLES[style_key]
    plan = "".join(f"<li>{escape(step)}</li>" for step in style.plan)
    rows = []
    for row, s in members:
        r = s.ratios
        rows.append(f'<tr><td><a href="{escape(player_href(row["name"], fmt))}">{escape(row["name"])}</a></td>'
                    f'<td class="num">{num(row["hands"], 0)}</td><td class="num">{num(row["net_bb"], 1, sign=True)} bb</td>'
                    f'<td class="num">{_pct(r.get("vpip"))}</td><td class="num">{_pct(r.get("pfr"))}</td>'
                    f'<td class="num">{_pct(r.get("afq"))}</td><td class="num">{_pct(r.get("fold_bet"))}</td>'
                    f'<td class="num">{_pct(r.get("wtsd"))}</td><td class="small">{escape(", ".join(s.style[1]))}</td></tr>')
    table = ('<div class="scroll"><table class="stats fl"><thead><tr><th>Joueur</th><th class="num">Mains</th>'
             '<th class="num">Ton résultat</th><th class="num">VPIP</th><th class="num">PFR</th><th class="num">Agressivité</th>'
             '<th class="num">Fold vs mises</th><th class="num">Abattage</th><th>Pourquoi ce style</th></tr></thead><tbody>'
             + "".join(rows) + "</tbody></table></div>")
    body = f"""<div class="meta">{escape(style.title)} {FORMAT_WORDS[fmt]} : {len(members)} joueur(s), leurs mains réunies.</div>
{switch}
{_tiles(style_tiles(study))}
<h2>Comment les jouer</h2>
<div class="card"><ul class="fl-plan">{plan}</ul>
<h3 style="margin-top:14px">Leurs leaks, ensemble</h3>{leaks_list(study, 6)}</div>
<h2>Les joueurs du groupe</h2>
<div class="card">{table}</div>
<h2>Value ou bluff ?</h2>
<div class="card"><h3 style="margin-top:0">Ce qui distingue leur value de leurs bluffs</h3>{differences_html(study, True)}</div>
<div class="card" style="margin-top:12px"><h3 style="margin-top:0">Leurs lignes</h3>{lines_html(study, plural=True)}{shown_examples(study)}</div>
<h2>Quand ils checkent</h2>
<div class="card">{checks_html(study, plural=True)}</div>
"""
    return html_page(f"{style.name} — field", f"<style>{STYLE}</style>{body}", embed)
