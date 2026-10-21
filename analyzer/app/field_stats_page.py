"""Étude du field › Statistiques : les stats globales de tes adversaires à un niveau de limites (réguliers,
récréatifs, tous), face aux repères d'un régulier solide de ce niveau et à toi ; aux tables à plusieurs, le détail par
position ; puis ce que leurs écarts changent pour ton jeu (tes écarts que leurs réponses justifient, les écarts qui te
rapporteraient), d'après la vérification en jeu du Leakfinding (leakcheck)."""
from __future__ import annotations

from html import escape
from typing import Optional

from .. import field, ring
from ..insights import SECTIONS, StatDef
from ..report import html_page, num, pct_cell
from ..stats import Ratio
from .field_leaks_page import _band_note, _band_words
from .ring_page import SCRIPT as RING_SCRIPT
from .ring_page import STYLE as RING_STYLE
from .ring_page import positions_html

STYLE = """<style>
table.fs-stats th.grp { text-align: left; padding-top: 12px; font-size: 12px; color: var(--ink-2); }
table.fs-stats td.ref { color: var(--muted); font-size: 12px; white-space: nowrap; }
table.fs-stats td.lbl { white-space: nowrap; }
ul.fs-list { margin: 4px 0; padding-left: 18px; }
ul.fs-list li { margin: 6px 0; }
ul.fs-list .why { display: block; color: var(--ink-2); font-size: 12px; }
</style>"""

# Aux tables à plusieurs : les stats de ring.read et de field.postflop_read, par moment du coup.
RING_SECTIONS = (
    ("Avant le flop", ("vpip", "pfr", "open", "limp", "threebet", "flat", "fold_3bet", "fold_steal",
                       "threebet_steal")),
    ("Après le flop", ("cbet_hu", "cbet_multi", "fold_cbet", "cbet_turn", "fold_turn", "fold_river", "xr_flop",
                       "stab_flop", "afq", "fold_bet")),
    ("Abattage", ("wtsd", "wsd", "wwsf")),
)
RING_LABELS = {**ring.LABEL, "open": "Open (RFI : premier à entrer)", "fold_bet": "Fold face à une mise (toutes streets)",
               "cbet_turn": "2e barrel (turn)", "fold_turn": "Fold vs mise turn", "fold_river": "Fold vs mise river",
               "xr_flop": "Check-raise flop", "stab_flop": "Mise quand l'agresseur checke (flop)",
               "afq": "Agressivité après le flop (AFq)"}
HU_GENERAL = (StatDef("vpip", "VPIP"), StatDef("pfr", "PFR"))


def ring_defs(band: Optional[str]) -> dict[str, StatDef]:
    """Les stats des tables à plusieurs et leur repère à ce niveau de limites (field.band_defs, ring.REF_6MAX)."""
    found = {d.key: d for d in field.band_defs(field.RING_DEFS, band)}
    out = {}
    for _, keys in RING_SECTIONS:
        for key in keys:
            known = found.get(key)
            ref = known.ref if known else ring.REF_6MAX.get(key)
            out[key] = StatDef(key, RING_LABELS.get(key, key), ref)
    return out


def hu_sections(band: Optional[str]) -> list[tuple[str, list[StatDef]]]:
    """Les stats heads-up (insights.SECTIONS, repères de ce niveau de limites), après VPIP et PFR."""
    adjusted = {d.key: d for d in field.band_defs([d for _, defs in SECTIONS for d in defs], band, heads_up=True)}
    return [("En général", list(HU_GENERAL))] + [(title, [adjusted[d.key] for d in defs]) for title, defs in SECTIONS]


def _ref(stat: StatDef) -> str:
    if not stat.ref:
        return '<td class="ref">–</td>'
    lo, hi = stat.ref
    return f'<td class="ref">{lo:.0f}–{hi:.0f}&nbsp;%</td>'


def stats_table(sections: list[tuple[str, list[StatDef]]], columns: list[tuple[str, dict]]) -> str:
    """Une ligne par stat, groupées par moment du coup ; une colonne par population (columns : [(libellé, {stat:
    Ratio})]), le repère avant la dernière (toi)."""
    head = "".join(f'<th class="num">{escape(label)}</th>' for label, _ in columns[:-1])
    head += f'<th class="num">Repère</th><th class="num">{escape(columns[-1][0])}</th>'
    rows = []
    for title, stats in sections:
        shown = [s for s in stats if any((ratios.get(s.key) or Ratio()).opps for _, ratios in columns)]
        if not shown:
            continue
        rows.append(f'<tr><th class="grp" colspan="{len(columns) + 2}">{escape(title)}</th></tr>')
        for stat in shown:
            cells = [pct_cell(ratios.get(stat.key) or Ratio(), stat) for _, ratios in columns]
            rows.append(f'<tr><td class="lbl">{escape(stat.label)}</td>{"".join(cells[:-1])}{_ref(stat)}{cells[-1]}'
                        "</tr>")
    return (f'<div class="scroll"><table class="stats fs-stats"><thead><tr><th>Stat</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _leak_items(found: list) -> str:
    items = []
    for leak in found:
        proofs = [p for p in (leak.check.proofs if leak.check else [])][:2]
        why = " ".join([leak.check.summary] + proofs) if leak.check else leak.evidence
        items.append(f'<li><b>{escape(leak.title)}</b> — {escape(leak.evidence)}<span class="why">{escape(why)}</span>'
                     "</li>")
    return "".join(items)


def your_game_html(exploits: list, chances: list, leaks_href: str) -> str:
    """Ce que les écarts du field changent pour ton jeu : tes écarts à la théorie que leurs réponses justifient (ou qui
    te rapportent), et là où tu joues comme la théorie alors qu'un écart te rapporterait (leaks.kept, leaks.chances)."""
    kept = [x for x in exploits if x.check and x.check.verdict == "exploit"]
    body = []
    if kept:
        body.append(f"<h3>Tes écarts que le field justifie</h3><ul class=\"fs-list\">{_leak_items(kept[:6])}</ul>")
    if chances:
        body.append(f"<h3>Ce que le field te laisse exploiter</h3><ul class=\"fs-list\">{_leak_items(chances[:6])}</ul>")
    if not body:
        body.append('<p class="muted">Rien de net pour l\'instant : aucun de tes écarts à la théorie n\'est justifié '
                    "par leurs réponses, et aucun écart ne te rapporterait de façon nette.</p>")
    return ('<h2>Ce que ça change pour ton jeu</h2><div class="card">' + "".join(body)
            + '<p class="note">Un écart à la théorie n\'est pas forcément une fuite : si tu 3bet plus que la théorie et '
            "que tes adversaires se couchent plus qu'elle face au 3bet, c'est une exploitation. Le Leakfinding le vérifie "
            "en jeu, contre les réguliers : ce que l'écart te rapporte sur tes mains (en bb/100) d'abord, sinon leurs "
            "réponses dans la situation miroir. Un écart justifié sort des leaks (« Les écarts justifiés ») ; il reste "
            f'à surveiller s\'ils s\'adaptent. Sur toutes tes mains de ce format. <a href="{escape(leaks_href)}">Ton '
            "Leakfinding</a></p></div>")


def build_field_stats_page(fmt: str, band: Optional[str], switch: str, columns: list[tuple[str, dict]],
                           players: dict[str, int], hands: dict[str, int], positions: Optional[list] = None,
                           exploits: Optional[list] = None, chances: Optional[list] = None,
                           leaks_href: str = "/moi/leaks") -> str:
    """La page Statistiques de l'Étude du field. columns : [(libellé, {stat: Ratio})] — réguliers, récréatifs, tous,
    puis toi (le dernier) ; players, hands : leur nombre de joueurs et de mains par population (« reg », « rec »,
    « all ») ; positions : aux tables à plusieurs, [(clé, libellé, ring.FormatStats)] de chaque population ;
    exploits, chances : tes écarts justifiés et les écarts qui te rapporteraient (leaks.Report)."""
    ring_game = fmt != "HU"
    if not players.get("all"):
        body = (switch + '<p class="note">Aucun adversaire classé à ce niveau de limites : rien à compter pour '
                "l'instant.</p>")
        return html_page("Statistiques du field", STYLE + body, True)
    sections = ([(title, [ring_defs(band)[k] for k in keys]) for title, keys in RING_SECTIONS] if ring_game
                else hu_sections(band))
    where = "aux tables à plusieurs (3 à 9 joueurs ensemble)" if ring_game else "en heads-up"
    meta = (f'<div class="meta">Tes adversaires {where}{_band_words(band)} : {players.get("reg", 0)} régulier(s), '
            f'{players.get("rec", 0)} récréatif(s), {num(hands.get("all", 0), 0)} mains-joueur. Leurs fréquences '
            "réunies, face aux repères d'un régulier solide de ce niveau et à toi.</div>")
    table = (f'<div class="card">{stats_table(sections, columns)}<p class="note">Chaque main compte une fois pour chaque '
             "joueur de la population qui y est servi. En couleur : hors du repère d'un régulier solide de ce niveau "
             "(nettement, d'après l'intervalle de confiance) ; chiffres en gris : moins de 15 occasions. "
             f"{escape(_band_note(band))}" + (" En heads-up, les repères d'avant le flop viennent de la solution "
                                               "préflop." if not ring_game else "") + "</p></div>")
    out = [switch, meta, "<h2>Le field en chiffres</h2>", table]
    if ring_game and positions:
        note = ('<p class="note">Une position compte les mains de chaque taille de table, une fois par joueur. Open : '
                "il parle le premier (RFI). Vol : ouverture du CO, du bouton ou de la SB sans caller ; la défense se lit "
                "en SB et BB. C-bet : il était le dernier relanceur préflop. bb/100 : leur résultat à cette position "
                "(contre toi et les autres).</p>")
        out += ["<h2>Par position</h2>", positions_html(positions, note=note)]
    out.append(your_game_html(exploits or [], chances or [], leaks_href))
    return html_page("Statistiques du field", STYLE + RING_STYLE + "".join(out), True,
                     script=RING_SCRIPT if ring_game else "")
