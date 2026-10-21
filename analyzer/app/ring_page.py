"""Page « Tables à plusieurs » de Mon jeu › Mes mains : tes coups à deux joueurs au flop, à ouvrir au solveur ; et tes
stats position par position à toutes tes tables de 3 joueurs et plus (analyzer/ring.py), sur toutes tes mains, contre
les réguliers et contre les récréatifs, pour le bilan des tables à plusieurs (positions_html)."""
from __future__ import annotations

from html import escape
from typing import Optional
from urllib.parse import quote

from .. import ring
from ..report import cards_html, fold, format_query, html_page, num, pct_cell
from ..ring_leaks import FORMAT
from .solver_pages import PANEL_SCRIPT

STYLE = """<style>
.rg-switch { display: flex; gap: 6px; margin-top: 14px; }
.rg-switch button { font: inherit; font-size: 13px; padding: 4px 12px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); color: var(--ink-2); cursor: pointer; }
.rg-switch button[aria-pressed="true"] { background: var(--ink); color: var(--page); border-color: var(--ink); }
.rg-ref { color: var(--muted); font-size: 11px; display: block; }
table.stats td.pos { font-weight: 600; white-space: nowrap; }
table.stats td.nowrap { white-space: nowrap; }
a.rg-open { font-weight: 600; text-decoration: none; color: var(--series-1); white-space: nowrap; }
.rg-tools { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: end; margin: 0 0 10px; font-size: 12px;
  color: var(--ink-2); }
.rg-tools label { display: flex; flex-direction: column; gap: 2px; }
.rg-tools select { font: inherit; font-size: 13px; padding: 3px 6px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--page); color: var(--ink); }
.rg-count { margin-left: auto; align-self: center; }
.rg-sv-bad { color: var(--alert); font-weight: 600; }
button.rg-solve { font: inherit; font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 6px;
  cursor: pointer; border: 1px solid var(--series-1); background: none; color: var(--series-1); white-space: nowrap; }
button.rg-solve:disabled { opacity: .6; cursor: default; }
</style>"""

SCRIPT = """
document.querySelectorAll('.rg-load').forEach((b) => b.addEventListener('click', async () => {
  const msg = b.parentElement.querySelector('.rg-load-msg');
  b.disabled = true;
  msg.textContent = 'Téléchargement…';
  const res = await fetch('/api/ranges/hand2note', { method: 'POST' }).catch(() => null);
  const data = res ? await res.json().catch(() => ({})) : {};
  if (res && res.ok) location.reload();
  else { msg.textContent = data.error || 'Téléchargement impossible.'; b.disabled = false; }
}));
// Tes coups à deux joueurs au flop : filtres et tri sur la page, et « Résoudre » qui met le coup en file au solveur
const rgTable = document.querySelector('table.rg-spots');
if (rgTable) {
  const rows = Array.from(rgTable.tBodies[0].rows);
  const tools = document.querySelector('.rg-tools');
  const value = (name) => tools.querySelector('[name="' + name + '"]').value;
  const SORTS = { pot: (r) => -r.dataset.pot, date: (r) => -Date.parse(r.dataset.date), loss: (r) => +r.dataset.net,
    win: (r) => -r.dataset.net, lost: (r) => -(r.dataset.lost === '' ? -1 : +r.dataset.lost) };
  const apply = () => {
    const f = { fmt: value('fmt'), pos: value('pos'), sv: value('sv'), res: value('res') };
    let n = 0;
    rows.forEach((r) => {
      const d = r.dataset;
      const ok = (!f.fmt || d.fmt === f.fmt) && (!f.pos || d.pos === f.pos)
        && (!f.sv || (f.sv === 'err' ? d.err === '1' : d.sv === f.sv))
        && (!f.res || (f.res === 'w' ? +d.net > 0 : +d.net < 0));
      r.hidden = !ok;
      n += ok;
    });
    const key = SORTS[value('sort')] || SORTS.pot;
    rows.slice().sort((a, b) => key(a) - key(b)).forEach((r) => rgTable.tBodies[0].append(r));
    tools.querySelector('.rg-count').textContent = n + ' coup' + (n > 1 ? 's' : '') + ' affiché' + (n > 1 ? 's' : '');
  };
  tools.querySelectorAll('select').forEach((s) => s.addEventListener('change', apply));
  apply();
  const api = rgTable.dataset.api;
  const call = (body) => fetch(api, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) }).then((res) => res.json().then((data) => (res.ok ? data : Promise.reject(data))));
  // le panneau « Face au solveur » : montrer tes coups analysés (aussi depuis le bilan : « #sv=done »), et la liste à
  // jour après une analyse
  window.fsFilter = (sv) => {
    tools.querySelector('[name="sv"]').value = sv;
    tools.querySelector('[name="sort"]').value = 'lost';
    apply();
    const box = rgTable.closest('details');
    if (box) box.open = true;
    tools.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  window.fsRefresh = () => location.reload();
  const wanted = new URLSearchParams(location.hash.slice(1)).get('sv');
  if (['done', 'todo', 'err'].includes(wanted)) window.fsFilter(wanted);
  rgTable.querySelectorAll('button.rg-solve').forEach((b) => b.addEventListener('click', () => {
    b.disabled = true;
    b.textContent = 'En file…';
    const ids = [b.dataset.id];
    call({ action: 'resoudre', ids }).then((first) => {
      if (!first.ready) { b.textContent = 'Solveur absent'; return; }
      const watch = () => setTimeout(() => call({ action: 'etat', ids }).then((v) => {
        if (v.done > first.done) { location.reload(); return; }
        const c = v.current;
        b.textContent = c && c.progress && c.progress.iteration
          ? 'Résolution… ' + Math.round(100 * Math.min(1, c.progress.iteration / c.max_iterations)) + ' %'
          : (v.busy ? 'En file…' : 'Résoudre');
        if (v.busy) watch(); else b.disabled = false;
      }).catch(() => { b.disabled = false; b.textContent = 'Résoudre'; }), 3000);
      watch();
    }).catch(() => { b.disabled = false; b.textContent = 'Réessayer'; });
  }));
}
document.querySelectorAll('.rg-switch button').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('.rg-fmt').forEach((s) => { s.hidden = s.dataset.scope !== b.dataset.scope; });
  document.querySelectorAll('.rg-switch button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
}));
"""

PREFLOP = ("vpip", "pfr", "open", "limp", "threebet", "flat", "fold_3bet", "fold_steal", "threebet_steal")
POSTFLOP = ("cbet_hu", "cbet_multi", "fold_cbet", "wtsd", "wsd", "wwsf")


def _ref_text(r) -> str:
    return f'<span class="rg-ref">repère {r[0]:.0f}–{r[1]:.0f}&nbsp;%</span>' if r else ""


def _table(fs: ring.FormatStats, keys: tuple[str, ...], with_result: bool,
           positions: Optional[tuple[str, ...]] = None) -> str:
    """Une ligne par position (positions : seulement celles-là), puis toutes ensemble."""
    head = "".join(f'<th class="num">{escape(ring.LABEL[k])}</th>' for k in keys)
    lead = '<th class="num">Mains</th><th class="num">bb/100</th>' if with_result else ""
    rows = []
    shown = [ps for ps in fs.positions if positions is None or ps.position in positions]
    for ps in shown + [fs.total]:
        cells = []
        for k in keys:
            stat = ring.stat_def(fs.table_format, k, ps.position if ps is not fs.total else None)
            cell = pct_cell(ps.ratios[k], stat=stat)
            if stat.ref and k == "open" and ps is not fs.total:
                cell = cell.replace("</td>", _ref_text(stat.ref) + "</td>", 1)
            cells.append(cell)
        result = (f'<td class="num">{ps.hands}</td><td class="num">{num(ps.bb100, 1, sign=True)}</td>'
                  if with_result else "")
        style = ' style="border-top:2px solid var(--axis)"' if ps is fs.total else ""
        rows.append(f'<tr{style}><td class="pos">{escape(ps.position)}</td>{result}{"".join(cells)}</tr>')
    return (f'<div class="card scroll"><table class="stats"><thead><tr><th>Position</th>{lead}{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


MAX_SPOTS = 200
WHY = (("Pas de range", "pas de range préflop"), ("Ta solution ne donne pas", "range manquante"),
       ("Un troisième joueur", "argent mort d'un 3e joueur"), ("Tapis préflop", "tapis préflop"))


def _why(status: str) -> str:
    short = next((label for prefix, label in WHY if status.startswith(prefix)), "non couvert")
    return f'<span class="muted" title="{escape(status)}">{escape(short)}</span>'


def _solver_cells(r: dict, sv: Optional[dict]) -> str:
    """EV théorique, EV perdue et ce qu'on peut faire du coup au solveur : le revoir (analysé), le résoudre (en file)
    ou l'ouvrir dans l'explorateur, ou pourquoi il ne se résout pas."""
    href = f'/explorateur/{quote(r["id"], safe="")}'
    if r["status"] is not None:
        return f'<td></td><td></td><td>{_why(r["status"])}</td>'
    if sv and "todo" not in sv:
        ev = "–" if sv["ev"] is None else f'{num(sv["ev"], 2, sign=True)} bb'
        lost = (f'<span class="{"rg-sv-bad" if sv["errors"] else ""}">{num(sv["lost"], 2)} bb</span>'
                + (f' <span class="muted">({sv["errors"]} err.)</span>' if sv["errors"] else ""))
        at = f'#d={sv["d"]}' if sv.get("d") is not None else ""
        return (f'<td class="num">{ev}</td><td class="num">{lost}</td><td><a class="rg-open" href="{href}{at}" '
                'target="_blank" rel="noopener">Revoir ↗</a></td>')
    return (f'<td class="num muted">–</td><td class="num muted">–</td><td class="nowrap">'
            f'<button type="button" class="rg-solve" data-id="{escape(r["id"])}">Résoudre</button> '
            f'<a class="rg-open" href="{href}" target="_blank" rel="noopener" title="Ouvrir au solveur">↗</a></td>')


def _sv_state(r: dict, sv: Optional[dict]) -> str:
    if r["status"] is not None:
        return "none"
    return "done" if sv and "todo" not in sv else "todo"


def _tools(rows: list[dict]) -> str:
    """Les filtres et le tri des coups (sur la page)."""
    def select(name: str, label: str, options: list[tuple[str, str]]) -> str:
        opts = "".join(f'<option value="{escape(v)}">{escape(t)}</option>' for v, t in options)
        return f'<label>{label}<select name="{name}">{opts}</select></label>'
    formats = sorted({r["format"] for r in rows})
    order = ("UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN", "SB", "BB")
    positions = sorted({r["hero"] for r in rows}, key=lambda p: order.index(p) if p in order else len(order))
    return ('<div class="rg-tools">'
            + (select("fmt", "Table", [("", "Toutes")] + [(f, f) for f in formats]) if len(formats) > 1
               else '<input type="hidden" name="fmt" value="">')
            + select("pos", "Ta position", [("", "Toutes")] + [(p, p) for p in positions])
            + select("sv", "Solveur", [("", "Tous"), ("done", "Analysés"), ("todo", "À résoudre"),
                                       ("err", "Avec une erreur (0,25 bb et plus)")])
            + select("res", "Résultat", [("", "Tous"), ("w", "Gagnés"), ("l", "Perdus")])
            + select("sort", "Tri", [("pot", "Plus gros pots"), ("date", "Plus récents"),
                                     ("loss", "Plus grosses pertes"), ("win", "Plus gros gains"),
                                     ("lost", "Plus d'EV perdue (solveur)")])
            + '<span class="rg-count muted"></span></div>')


def _spots(rows: list[dict], ready: bool, opened: bool = False, status: Optional[dict] = None,
           api: str = "/api/coups") -> str:
    """Les coups à deux joueurs au flop (toutes tables), à ouvrir au solveur postflop ; status : ce qu'en dit le
    solveur (Library.solver_status), pour leur EV théorique et l'EV perdue, ou les résoudre ; api : où les mettre en
    file (tes mains, ou celles d'un élève)."""
    mine = rows
    if not mine:
        return ""
    status = status or {}

    def row(r: dict) -> str:
        sv = status.get(r["id"])
        lost = "" if not sv or "todo" in sv else f'{sv["lost"]:.2f}'
        data = (f' data-date="{r["date"]:%Y-%m-%dT%H:%M}" data-pot="{r["total_bb"]}" data-net="{r["net_bb"]}"'
                f' data-fmt="{escape(r["format"])}" data-pos="{escape(r["hero"])}" data-sv="{_sv_state(r, sv)}"'
                f' data-err="{int(bool(sv and sv.get("errors")))}" data-lost="{lost}"')
        return (f'<tr{data}><td class="nowrap">{r["date"]:%d/%m %H:%M}</td>'
                f'<td class="nowrap">{escape(r["format"])}</td><td>{escape(r["line"])}</td>'
                f'<td class="pos">{escape(r["hero"])} <span class="muted">vs {escape(r["villain"])}</span></td>'
                f'<td>{cards_html(r["cards"])}</td><td>{cards_html(r["board"])}</td>'
                f'<td class="num">{num(r["total_bb"], 1)}</td><td class="num">{num(r["net_bb"], 1, sign=True)}</td>'
                f"{_solver_cells(r, sv)}</tr>")
    # les plus gros pots, et tous tes coups déjà analysés
    shown = mine[:MAX_SPOTS] + [r for r in mine[MAX_SPOTS:] if _sv_state(r, status.get(r["id"])) == "done"]
    body = "".join(row(r) for r in shown)
    note = "" if ready else (
        '<p class="note">Tes ranges préflop ne sont pas encore là : ces coups s\'ouvriront au solveur dès qu\'elles '
        "seront ajoutées.</p>")
    if not ready:  # les charts 6-max couvrent aussi le 3-max et, place par place, les tables de 7 à 9 joueurs
        note += ('<p><button type="button" class="rg-load">Charger les charts 100 bb de Hand2Note Guide (6-max et 3-max)'
                 '</button> <span class="muted small">pots simples, 3bet et 4bet ; téléchargés sur ta machine, pour ton '
                 "usage personnel (conditions du site)</span> <span class=\"rg-load-msg small\"></span></p>")
    extra = (f" Les {MAX_SPOTS} plus gros pots sur {len(mine)}, et tous ceux déjà analysés." if len(mine) > MAX_SPOTS
             else "")
    done = sum(1 for r in mine if _sv_state(r, status.get(r["id"])) == "done")
    table = (f'<div class="card">{_tools(shown)}<div class="scroll">'
             f'<table class="stats rg-spots" data-api="{escape(api)}"><thead><tr><th>Date</th><th>Table</th>'
             '<th>Ligne préflop</th><th>Toi</th><th>Main</th><th>Flop</th><th class="num">Pot (bb)</th>'
             '<th class="num">Résultat</th><th class="num">EV théorique</th><th class="num">EV perdue</th>'
             f"<th>Solveur</th></tr></thead><tbody>{body}</tbody></table></div></div>"
             f'<p class="note">Résolus comme un coup heads-up à partir du flop, avec les ranges de ta solution préflop '
             "pour la ligne jouée (à une table sans solution à elle, les charts 6-max à même nombre de joueurs "
             f"derrière) ; le pot compte l'argent mort des joueurs qui ont foldé. EV théorique : ce que vaut ta main à "
             "ta première décision au flop, jouée comme le solveur ; EV perdue : ce que tes décisions ont coûté face "
             "à lui (une erreur : 0,25 bb et plus). <b>Résoudre</b> met le coup en file au solveur (quelques "
             f"minutes) ; la page se met à jour quand il est analysé.{extra}</p>")
    return fold("Coups à deux joueurs au flop", note + table,
                f"{len(mine)} coup{'s' if len(mine) > 1 else ''}, dont {done} analysé{'s' if done > 1 else ''} au "
                "solveur", "coups", opened=opened or not ready)


def _gaps(found: Optional[list]) -> str:
    """Tes écarts à la théorie les plus importants contre les réguliers (charts, plans de jeu des flops résolus), en
    tête de page ; le détail est dans le Leakfinding des tables à plusieurs."""
    if found is None:
        return ""
    from .leaks_page import gaps_table
    link = (f'<a href="leaks{escape(format_query(FORMAT))}">Le Leakfinding des tables à plusieurs</a> donne le détail de '
            "toutes tes stats, les mains à passer au solveur et les leaks à travailler.")
    if not found:
        body = ('<p class="muted">Aucun écart net à la théorie contre les réguliers pour l\'instant (charts avant le '
                "flop, plans de jeu des flops résolus après) : pas assez d'occasions, ou un jeu proche de la théorie. "
                f"{link}</p>")
    else:
        body = (gaps_table(found, "reg", "Contre réguliers") + '<p class="note">Contre les réguliers, comparé aux charts '
                "avec les mêmes cartes (avant le flop) et aux plans de jeu des flops 6-max résolus (après le flop, pots à "
                "deux joueurs) ; les nets d'abord, puis selon la taille de l'écart, sa fréquence et ce qu'il met en jeu. "
                f"{link}</p>")
    return f'<h2>Tes écarts les plus importants</h2><div class="card">{body}</div>'


LABELS = {"all": "Toutes tes mains", "reg": "Contre les réguliers", "rec": "Contre les récréatifs"}


def positions_html(shown: list[tuple[str, str, ring.FormatStats]], positions: Optional[tuple[str, ...]] = None,
                   preflop: tuple[str, ...] = PREFLOP, postflop: tuple[str, ...] = POSTFLOP) -> str:
    """Tes stats position par position (avant et après le flop), une portée à la fois : shown, [(portée, libellé,
    stats de toutes tes tables ensemble)] — toutes tes mains, contre les réguliers, contre les récréatifs ; positions :
    seulement celles-là (ring.SIX_MAX : le bilan). Avec STYLE et SCRIPT."""
    switch = ('<div class="rg-switch" role="group" aria-label="Adversaires">' + "".join(
        f'<button type="button" data-scope="{escape(scope)}" aria-pressed="{str(k == 0).lower()}">'
        f"{escape(LABELS.get(scope, label))} ({fs.total.hands})</button>" for k, (scope, label, fs) in enumerate(shown))
        + "</div>") if len(shown) > 1 else ""
    sections = "".join(
        f'<section class="rg-fmt" data-scope="{escape(scope)}"{"" if k == 0 else " hidden"}>'
        f'<h3>Préflop par position</h3>{_table(fs, preflop, True, positions)}'
        f'<h3>Après le flop</h3>{_table(fs, postflop, False, positions)}</section>'
        for k, (scope, _, fs) in enumerate(shown))
    only = (" Les positions d'une table 6-max seulement (les autres, à 7-9 joueurs : onglet Statistiques) ; « Toutes » "
            "compte toutes tes mains." if positions else "")
    note = ('<p class="note">Une position compte ses mains de chaque taille de table. Repères indicatifs d\'un régulier '
            "en 6-max à 100 bb ; chiffres en gris : moins de 15 occasions. Open : tu parles le premier. Vol : ouverture "
            "du CO, du bouton ou de la SB sans caller ; la défense se lit en SB et BB. C-bet : tu étais le dernier "
            "relanceur préflop et on te laisse miser au flop. WWSF : tu as gagné le pot après avoir vu le flop."
            f"{only}</p>")
    return switch + sections + note


def build_ring_page(scopes: list[tuple[str, str, list]], hero: str, embed: bool = True,
                    spots: Optional[list[dict]] = None, ranges: Optional[dict[str, int]] = None,
                    status: Optional[dict] = None, api: str = "/api/coups", panel: str = "") -> str:
    """Tes coups à deux joueurs au flop aux tables à plusieurs, à ouvrir au solveur. scopes : [(portée, libellé,
    ring.analyze(…, merge=True))] — toutes tes mains, contre les réguliers, contre les récréatifs ; spots :
    Library.ring_spots() ; ranges : formats dont la solution préflop est là (ring_ranges.available) ; status : ce
    qu'en dit le solveur (Library.solver_status) ; api : où mettre un coup en file au solveur ; panel : la place du
    panneau « Face au solveur » (solver_pages.slot_html), en tête."""
    shown = [(scope, label, stats[0]) for scope, label, stats in scopes if stats]
    if not shown:
        body = ('<p class="note">Aucune main à une table de 3 joueurs ou plus. Importe des historiques 3-max, 6-max ou de '
                "7 à 9 joueurs : tes stats par position s'afficheront ici.</p>")
        if not ranges:  # les charts servent aussi à l'explorateur (coup à plusieurs), même sans mains
            body += ('<p><button type="button" class="rg-load">Charger les charts 100 bb de Hand2Note Guide (6-max et '
                     '3-max)</button> <span class="muted small">pour étudier les coups à plusieurs dans l\'explorateur ; '
                     'téléchargés sur ta machine, pour ton usage personnel (conditions du site)</span> '
                     '<span class="rg-load-msg small"></span></p>')
        return html_page("Tables à plusieurs", body, embed, script=SCRIPT)
    heading = "" if embed else f"<h1>Tables à plusieurs — {escape(hero)}</h1>"
    fs = shown[0][2]
    period = f"{fs.first:%d/%m/%Y} → {fs.last:%d/%m/%Y} · " if fs.first else ""
    meta = (f'<div class="meta">{fs.total.hands} mains aux tables à plusieurs ({escape(", ".join(fs.formats))}) · '
            f"{period}tes stats position par position et tes écarts à la théorie sont dans le "
            f'<a href="bilan{escape(format_query(FORMAT))}">bilan des tables à plusieurs</a> et le '
            f'<a href="leaks{escape(format_query(FORMAT))}">Leakfinding</a>.</div>')
    found = _spots(spots or [], bool(ranges), opened=True, status=status, api=api)
    if not found:
        found = ('<p class="note">Aucun coup à deux joueurs au flop pour l\'instant : tes pots à plusieurs joueurs au '
                 "flop ne se résolvent pas.</p>")
    return html_page(f"Tables à plusieurs — {hero}", STYLE + heading + meta + panel + found, embed,
                     script=SCRIPT + (PANEL_SCRIPT if panel else ""))
