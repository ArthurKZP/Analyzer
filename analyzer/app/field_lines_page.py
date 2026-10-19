"""Étude du field : les bluffs ou la value des réguliers (ou des récréatifs) tous ensemble, les sous-onglets Bluffs et
Value de Réguliers et de Récréatifs, d'après leurs mises après le flop, ligne par ligne et taille par taille
(field.study), en heads-up comme aux tables à plusieurs.

- Ce qui ressort : les lignes dont les mises montrées disent nettement bluff (ou value), les tailles qui trahissent
  leur main, ce que cachent leurs checks ; en heads-up, pour la value, leurs fréquences de mise face au solveur
  (bluffs.analyze : là où ils misent bien moins que la théorie, leurs mises sont de la value).
- Taille par taille : leurs mises montrées à chaque street, petites, grosses, overbets et relances.
- Puis, repliés : ce qui distingue leur value de leurs bluffs, leurs checks, leurs lignes, leurs mises montrées.

Les bluffs en heads-up ont leur page à part, face au solveur (bluffs_page)."""
from __future__ import annotations

from html import escape
from statistics import median
from typing import Optional

from .. import field
from ..field import BLUFF, INTENT_LABELS, VALUE, Study
from ..lines import BOARD_SIZE
from ..models import POSTFLOP
from ..report import cards_html, composition_html, fold, html_page, num
from .bluffs_page import STYLE as BLUFFS_STYLE
from .bluffs_page import _pattern_items
from .field_leaks_page import FORMAT_WORDS, STYLE, _band_note, _band_words, _street, _tiles, checks_html, lines_html

CROWD = {"reg": "réguliers", "rec": "récréatifs"}
MAX_EXAMPLES = 40
MAX_PATTERNS = 4       # ce que disent leurs fréquences face au solveur, montré d'abord (les autres repliés)
STRONG_CHECKS = 0.35   # part de mains fortes dans leurs checks qui en fait un piège
EMPTY_CHECKS = 0.6     # part de mains sans rien dans leurs checks qui invite à miser derrière
MIN_CHECKS = 5


def _pct(n: int, total: int) -> str:
    return f"{round(100 * n / total)} %" if total else "–"


def _plural(text: str) -> str:
    """Les textes de field (« Ses mises grosses… ») pour un groupe de joueurs."""
    return "Leurs" + text[3:] if text.startswith("Ses ") else text


def findings(study: Study, focus: str) -> list[tuple[str, str]]:
    """Ce qui ressort de leurs mises montrées, pour la value (focus « value ») ou les bluffs : [(où, ce qu'il en
    faut retenir)], les lignes les plus montrées d'abord, puis les tailles, puis les checks."""
    want = "moins" if focus == "value" else "plus"
    out = []
    for ln in sorted(study.lines, key=lambda ln: -len(ln.shown)):
        if len(ln.shown) >= field.MIN_SHOWN:
            v = field.verdict(ln)
            if v.kind == want:
                out.append((f"{_street(ln.street)}, {ln.name.lower()}", v.text))
    for street in POSTFLOP:
        tell = field.size_tell(study.sizes, street)
        if tell:
            out.append((f"{_street(street)}, la taille", _plural(tell)))
    for street in POSTFLOP:
        c = study.checks.get(street)
        n = sum(c.values()) if c else 0
        if n < MIN_CHECKS:
            continue
        if focus == "value" and c["value"] / n >= STRONG_CHECKS:
            out.append((f"{_street(street)}, quand ils checkent",
                        f"Une main forte dans {c['value']} checks sur {n} : méfie-toi des pièges (check-raise, "
                        "check-call), ne mise pas fin derrière"))
        if focus == "bluffs" and (c["bluff"] + c["semi"]) / n >= EMPTY_CHECKS:
            out.append((f"{_street(street)}, quand ils checkent",
                        f"Rien de fait dans {c['bluff'] + c['semi']} checks sur {n} : mise derrière plus souvent"))
    return out


def findings_html(study: Study, focus: str, patterns: Optional[list] = None) -> str:
    items = [f'<li><span class="t">{escape(where)}</span><div class="x">→ {escape(what)}</div></li>'
             for where, what in findings(study, focus)]
    out = f'<ul class="fl-leaks">{"".join(items)}</ul>' if items else ""
    if items and patterns:
        out = f"<h3>À l'abattage</h3>{out}"
    if patterns:  # les plus nets d'abord (les solides) ; les autres repliés
        first, rest = patterns[:MAX_PATTERNS], patterns[MAX_PATTERNS:]
        out += (f'<h3 style="margin-top:{14 if items else 0}px">Face au solveur</h3><ul class="bl-list">'
                f"{_pattern_items(first, plural=True)}</ul>")
        if rest:
            out += (f'<details style="margin-top:12px"><summary>Les {len(rest)} autres</summary>'
                    f'<div style="padding:10px 16px"><ul class="bl-list">{_pattern_items(rest, plural=True)}</ul>'
                    "</div></details>")
    if not out:
        what = "presque toujours de la value" if focus == "value" else "souvent des bluffs"
        why = ("leurs mises montrées y sont mêlées (le détail, taille par taille, ci-dessous)."
               if study.shown >= field.MIN_SHOWN else
               f"il faut au moins {field.MIN_SHOWN} mises montrées dans une même ligne ({study.shown} pour l'instant).")
        out = f'<p class="note" style="margin:0">Aucune ligne ne dit encore nettement « {what} » : {why}</p>'
    return out


def _median_size(bets: list) -> str:
    sizes = [b.pct for b in bets if b.pct is not None]
    return f"{median(sizes):.0f} %" if sizes else "–"


def sizes_html(study: Study, focus: str) -> str:
    """Leurs mises montrées, street par street et taille par taille : ce qu'ils montrent, la part de value (ou de
    mains sans rien de fait), la taille médiane de leur value (ou de leurs bluffs) et ce qu'il faut en faire."""
    rows = []
    for street in POSTFLOP:
        lines = [ln for ln in study.sizes if ln.street == street]
        if not lines:
            continue
        rows.append(f'<tr class="street"><td colspan="6">{_street(street)}</td></tr>')
        for ln in lines:
            shown, c = ln.shown, ln.intents
            mine = [b for b in shown if b.intent in (VALUE if focus == "value" else BLUFF)]
            share = _pct(len(mine), len(shown))
            if focus == "value" and mine:
                share += f' <span class="muted">(dont fine {_pct(c["thin"], len(mine))})</span>'
            v = field.verdict(ln) if shown else None
            verdict = (f'<td class="small v-{v.kind or "none"}">{escape(v.text)}</td>' if v
                       else '<td class="small muted">jamais montrées</td>')
            rows.append(f'<tr><td>{escape(ln.size or "Relances")}</td><td class="num">{ln.count}</td>'
                        f'<td class="compcell">{composition_html(c, len(shown), INTENT_LABELS)}</td>'
                        f'<td class="num">{share}</td><td class="num">{_median_size(mine)}</td>{verdict}</tr>')
    if not rows:
        return '<p class="note">Aucune mise après le flop pour l\'instant.</p>'
    part = "Value" if focus == "value" else "Sans main faite"
    size = "Taille de la value" if focus == "value" else "Taille des bluffs"
    return ('<div class="scroll"><table class="stats fl"><thead><tr><th>Taille</th><th class="num">Fois</th>'
            f'<th>À l\'abattage</th><th class="num">{part}</th><th class="num">{size}</th><th>Verdict</th></tr></thead>'
            "<tbody>" + "".join(rows) + "</tbody></table></div>")


def tells_html(study: Study) -> str:
    items = [f"<li><b>{_street(street)}</b> : {escape(_plural(t))}</li>"
             for street, found in study.street_tells().items() for t in found]
    if not items:
        return ('<p class="note" style="margin:0">Pas encore de différence nette entre leur value et leurs bluffs : il '
                "faut au moins deux mains de value et deux bluffs montrés à une même street.</p>")
    return (f'<ul class="fl-plan">{"".join(items)}</ul><p class="note">D\'après leurs mises montrées : la taille, le '
            "temps de réflexion, la carte tombée et le nombre de joueurs dans le pot, value contre bluffs.</p>")


def examples_html(study: Study, focus: str, limit: int = MAX_EXAMPLES) -> str:
    """Leurs mises montrées de value (ou leurs bluffs et semi-bluffs), les plus récentes d'abord."""
    wanted = VALUE if focus == "value" else BLUFF
    seen = sorted((b for b in study.bets if b.intent in wanted), key=lambda b: b.hand.date, reverse=True)
    if not seen:
        return '<p class="note" style="margin:0">Aucune pour l\'instant.</p>'
    rows = []
    for b in seen[:limit]:
        board = b.hand.board[:BOARD_SIZE[b.street]]
        size = f"{b.pct:.0f} %" if b.pct is not None else "relance"
        rows.append(f'<tr><td>{escape(b.player)}</td><td>{_street(b.street)}</td><td>{escape(b.label)}</td>'
                    f'<td class="num">{size}</td><td>{cards_html(board)}</td>'
                    f'<td>{cards_html(b.hand.hole_cards[b.player])}</td>'
                    f'<td><span class="dot i-{b.intent}"></span>{INTENT_LABELS[b.intent]}</td>'
                    f'<td class="small">{escape(b.description)}</td></tr>')
    more = f'<p class="note">Les {limit} plus récentes sur {len(seen)}.</p>' if len(seen) > limit else ""
    return ('<div class="scroll"><table class="stats fl"><thead><tr><th>Joueur</th><th>Street</th><th>Ligne</th>'
            '<th class="num">Taille</th><th>Board</th><th>Cartes</th><th>Intention</th><th>Main faite</th></tr></thead>'
            "<tbody>" + "".join(rows) + f"</tbody></table></div>{more}")


def build_crowd_page(kind: str, focus: str, fmt: str, study: Optional[Study], switch: str = "",
                     band: Optional[str] = None, patterns: Optional[list] = None, embed: bool = True) -> str:
    """Les bluffs (focus « bluffs ») ou la value (« value ») des réguliers (kind « reg ») ou des récréatifs (« rec »)
    d'un format et d'un niveau de limites, tous ensemble : study, leur étude réunie (None : aucun joueur de ce type) ;
    patterns : en heads-up, ce que disent leurs fréquences de mise face au solveur (bluffs.Pattern)."""
    crowd = CROWD[kind]
    value = focus == "value"
    where = FORMAT_WORDS[fmt] + _band_words(band)
    title = f"{'Value' if value else 'Bluffs'} des {crowd}"
    if value:
        meta = (f"La value des {crowd} {where} : où leurs mises sont presque toujours de la value (folde tes "
                "bluff-catchers), à quelle taille, jusqu'où ils vont en value fine, ce que cachent leurs checks.")
    else:
        meta = (f"Les bluffs des {crowd} {where} : dans quelles lignes, à quelle taille, et quand leurs checks "
                "abandonnent le pot.")
    if study is None or not study.hands:
        body = (f'<div class="meta">{escape(meta)}</div>{switch}<div class="card"><p class="note" style="margin:0">'
                f"Aucun joueur classé parmi les {crowd} {escape(where)} pour l'instant. Le type de chaque joueur se "
                "règle dans Paramètres › Joueurs et alias.</p></div>")
        return html_page(title, f"<style>{STYLE}{BLUFFS_STYLE}</style>{body}", embed)
    shown = [b for b in study.bets if b.intent]
    mine = sum(1 for b in shown if b.intent in (VALUE if value else BLUFF))
    tiles = _tiles([
        (f"{crowd.capitalize()}", num(len(study.names), 0), "ensemble"),
        ("Mains", num(study.hands, 0), "à la même table" if fmt != "HU" else "contre toi"),
        ("Mises et relances", num(len(study.bets), 0), "après le flop"),
        ("Vues à l'abattage", num(len(shown), 0),
         f"dont {mine} de value" if value else f"dont {mine} sans main faite")])
    many = mine > 1
    seen = (f"mise{'s' if many else ''} de value vue{'s' if many else ''}" if value
            else f"bluff{'s' if many else ''} ou semi-bluff{'s' if many else ''} vu{'s' if many else ''}")
    their = "Leur value" if value else "Leurs bluffs"
    examples = "Leur value montrée" if value else "Leurs bluffs montrés"
    solver = ", et leurs fréquences de mise face au solveur" if patterns else ""
    other = ("Leurs bluffs : le sous-onglet Bluffs." if value else
             "Leur value : le sous-onglet Value.")
    body = f"""<div class="meta">{escape(meta)} {num(len(study.names), 0)} joueur(s), leurs mains réunies.</div>
{switch}
{tiles}
<h2>Ce qui ressort</h2>
<div class="card">{findings_html(study, focus, patterns)}
<p class="note">D'après leurs mises vues à l'abattage{solver}. À la river, la part de bluffs d'une ligne est comparée
à l'équité qu'il te faut pour payer (taille médiane de la ligne) ; avant, la part de mises sans main faite.
{escape(other)}{escape(_band_note(band))}</p></div>
<h2>{their}, taille par taille</h2>
<div class="card">{sizes_html(study, focus)}
<p class="note">Toutes lignes confondues, leurs mises par street et par taille (petite jusqu'à 55 % du pot, grosse
jusqu'à 120 %, overbet au-delà), relances à part : des échantillons plus gros que ligne par ligne.</p></div>
{fold("Ce qui distingue leur value de leurs bluffs", f'<div class="card">{tells_html(study)}</div>',
      "taille, temps de réflexion, carte tombée", "tells")}
{fold("Quand ils checkent", f'<div class="card">{checks_html(study, plural=True)}</div>',
      "ce qu'ils montrent à l'abattage après avoir checké", "checks")}
{fold("Leurs lignes", f'<div class="card">{lines_html(study, plural=True)}</div>',
      "chaque ligne, ce qu'ils y montrent et le verdict", "lignes")}
{fold(examples, f'<div class="card">{examples_html(study, focus)}</div>',
      f"{mine} {seen} à l'abattage", "exemples")}
"""
    return html_page(title, f"<style>{STYLE}{BLUFFS_STYLE}</style>{body}", embed)
