"""Étude du field › Statistiques : les stats de tes adversaires à un niveau de limites (réguliers, récréatifs,
tous), celles que tu choisis dans le catalogue (analyzer/statlib.py, paramètres : field_stat_selection), face aux
repères d'un régulier solide de ce niveau et à toi, puis position par position ; puis ce que leurs écarts changent
pour ton jeu (tes écarts que leurs réponses justifient, les écarts qui te rapporteraient), d'après la vérification
en jeu du Leakfinding (leakcheck)."""
from __future__ import annotations

from html import escape
from typing import Optional

from .. import statlib
from ..report import html_page, num
from ..selfreport import STATS_LEGEND
from .field_leaks_page import _band_note, _band_words
from .stats_page import BAR, NONE_CHOSEN, SCRIPT, chooser, overview_table, positions_block
from .stats_page import STYLE as STATS_STYLE

STYLE = """<style>
ul.fs-list { margin: 4px 0; padding-left: 18px; }
ul.fs-list li { margin: 6px 0; }
ul.fs-list .why { display: block; color: var(--ink-2); font-size: 12px; }
</style>"""
FIELD_NOTE = ("Ton choix vaut pour l'Étude du field, à tous les niveaux de limites, dans ton espace comme pour tes "
              "élèves.")


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


def build_field_stats_page(fmt: str, band: Optional[str], switch: str,
                           populations: list[tuple[str, str, Optional[statlib.Table]]], mine: Optional[statlib.Table],
                           players: dict[str, int], hands: dict[str, int], saved: Optional[dict] = None,
                           exploits: Optional[list] = None, chances: Optional[list] = None,
                           leaks_href: str = "/moi/leaks") -> str:
    """La page Statistiques de l'Étude du field. populations : [(clé, libellé, statlib.tally_group)] — réguliers,
    récréatifs, tous ; mine : tes stats sur les mêmes mains ; players, hands : leur nombre de joueurs et de
    mains-joueur par population (« reg », « rec », « all ») ; saved : les stats choisies (settings
    field_stat_selection) ; exploits, chances : tes écarts justifiés et les écarts qui te rapporteraient
    (leaks.Report)."""
    ring_game = fmt != "HU"
    shown = [(k, label, t) for k, label, t in populations if t is not None and t.total.hands]
    if not players.get("all") or not shown:
        body = (switch + '<p class="note">Aucun adversaire classé à ce niveau de limites : rien à compter pour '
                "l'instant.</p>")
        return html_page("Statistiques du field", STYLE + body, True)
    chosen = statlib.selection(fmt, saved, statlib.FIELD_DEFAULTS)
    where = "aux tables à plusieurs (3 à 9 joueurs ensemble)" if ring_game else "en heads-up"
    meta = (f'<div class="meta">Tes adversaires {where}{_band_words(band)} : {players.get("reg", 0)} régulier(s), '
            f'{players.get("rec", 0)} récréatif(s), {num(hands.get("all", 0), 0)} mains-joueur. {len(chosen)} '
            f"stat(s) choisie(s) sur {len(statlib.available(fmt))}, face aux repères d'un régulier solide de ce niveau "
            "et à toi.</div>")
    ref_of = lambda stat: statlib.field_ref(stat, fmt, band)  # noqa: E731
    you = [("me", "Toi", mine)] if mine is not None and mine.total.hands else []
    note = ('<p class="note">Chaque main compte une fois pour chaque joueur de la population qui y est servi. En '
            "couleur : hors du repère d'un régulier solide de ce niveau (nettement, d'après l'intervalle de "
            "confiance) ; chiffres en gris : moins de 15 occasions ; tailles : moyenne des mises (tapis exclus). "
            f"{escape(_band_note(band))}" + (" En heads-up, les repères d'avant le flop viennent de la solution "
                                               "préflop." if not ring_game else "") + "</p>")
    out = [switch, meta, BAR, chooser(fmt, chosen, saved, "field_stat_selection", FIELD_NOTE)]
    if chosen:
        out += ["<h2>Le field en chiffres</h2>", STATS_LEGEND, overview_table(chosen, fmt, shown, you, ref_of), note,
                "<h2>Par position</h2>",
                f'<div class="card">{positions_block(chosen, fmt, shown + you, ref_of, "Population")}'
                '<p class="note">Une position compte les mains de chaque taille de table, une fois par joueur.</p>'
                "</div>"]
    else:
        out.append(NONE_CHOSEN)
    out.append(your_game_html(exploits or [], chances or [], leaks_href))
    return html_page("Statistiques du field", f"<style>{STATS_STYLE}</style>" + STYLE + "".join(out), True,
                     script=SCRIPT)
