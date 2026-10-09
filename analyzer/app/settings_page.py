"""Onglet « Général » des Paramètres : ton pseudo, les formats que tu joues, le coaching, le seuil de l'Étude du
field et la précision du solveur. Chaque choix s'enregistre tout de suite (/api/parametres, /api/precision) ; le menu
de l'application suit (message « analyzer-refresh »). Les types des adversaires et les alias ont leur onglet
(field_page.build_players_page)."""
from __future__ import annotations

from html import escape

from .. import settings
from ..report import html_page, num
from ..theory import postflop

STYLE = """
.st-box fieldset { border: none; margin: 0; padding: 0; }
.st-box legend { font-weight: 600; margin-bottom: 6px; padding: 0; }
.st-opt { display: flex; gap: 8px; align-items: baseline; padding: 5px 0; cursor: pointer; }
.st-opt input { margin: 0; flex: none; }
.st-opt .muted { font-size: 13px; }
.st-row { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
.st-row input[type=number], .st-row select { font: inherit; font-size: 14px; padding: 4px 8px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--page); color: var(--ink); }
.st-row input[type=number] { width: 6em; }
.st-status { font-size: 12px; min-height: 1.2em; margin-top: 6px; color: var(--muted); }
.st-status.err { color: var(--alert); }
.st-links { margin: 0; padding-left: 18px; }
.st-links li { margin: 4px 0; }
"""

SCRIPT = """
(function () {
  function status(box, text, error) {
    var out = box.querySelector('.st-status');
    if (!out) return;
    out.textContent = text;
    out.classList.toggle('err', !!error);
  }
  function send(url, body, box, after) {
    status(box, 'Enregistrement…');
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (res) {
        return res.json().then(function (data) { if (!res.ok) throw new Error(data.error || res.statusText); return data; });
      })
      .then(function (data) {
        status(box, 'Enregistré.');
        if (window.parent !== window) window.parent.postMessage({ type: 'analyzer-refresh' }, location.origin);
        if (after) after(data);
      })
      .catch(function (e) { status(box, e.message, true); });
  }
  document.querySelectorAll('[data-setting]').forEach(function (box) {
    var key = box.dataset.setting;
    box.addEventListener('change', function (e) {
      var value;
      if (key === 'formats') {
        var picked = Array.prototype.filter.call(box.querySelectorAll('input[type=checkbox]'), function (c) { return c.checked; });
        if (!picked.length) { e.target.checked = true; status(box, 'Garde au moins un format.', true); return; }
        value = picked.length === box.querySelectorAll('input[type=checkbox]').length ? null
          : picked.map(function (c) { return c.value; });
      } else if (key === 'min_hands') {
        value = Number(e.target.value);
        if (!(value >= 10)) { status(box, 'Au moins 10 mains.', true); return; }
      } else if (key === 'coach') {
        value = e.target.value === '' ? null : e.target.value === 'true';
      } else if (key === 'hero') {
        value = e.target.value || null;
      }
      var body = {};
      body[key] = value;
      send('/api/parametres', body, box, key === 'hero' ? function () { location.reload(); } : null);
    });
  });
  var prec = document.querySelector('[data-precision]');
  if (prec) prec.addEventListener('change', function (e) {
    send('/api/precision', { precision: Number(e.target.value) }, prec);
  });
})();
"""


def _radio(name: str, value: str, label: str, checked: bool, detail: str = "") -> str:
    extra = f' <span class="muted">{detail}</span>' if detail else ""
    return (f'<label class="st-opt"><input type="radio" name="{name}" value="{escape(value)}"'
            f'{" checked" if checked else ""}><span>{label}{extra}</span></label>')


def _hero_html(view: dict) -> str:
    pseudos = view["pseudos"]
    chosen = view["chosen_hero"]
    first = pseudos[0]["name"] if pseudos else view["hero"]
    auto = f"Automatique : le plus fréquent ({escape(first)})" if first else "Automatique"
    options = [_radio("hero", "", auto, chosen is None)]
    for p in pseudos:
        detail = f"{', '.join(p['sites'])} · {num(p['hands'], 0)} mains"
        options.append(_radio("hero", p["name"], f"<b>{escape(p['name'])}</b>", chosen == p["name"], detail))
    if not pseudos:
        options.append('<p class="note">Aucun pseudo marqué comme toi pour l\'instant : importe tes historiques.</p>')
    shown = escape(view["hero"]) if view["hero"] else "personne pour l'instant"
    return f"""
<h2>Toi</h2>
<div class="card st-box" data-setting="hero">
<p style="margin-top:0">Le joueur analysé : <b>{shown}</b>.</p>
<fieldset><legend>Ton pseudo principal</legend>{"".join(options)}</fieldset>
<div class="st-status" aria-live="polite"></div>
<p class="note">Tes historiques marquent tes mains (« Dealt to … ») : tous ces pseudos sont toi, réunis sous ton pseudo
principal partout dans l'application (un par site, ou un pseudo changé). Un pseudo d'adversaire qui en a plusieurs se
regroupe dans l'onglet <b>Joueurs et alias</b>.</p>
</div>"""


def _formats_html(view: dict) -> str:
    plays = view["plays"]
    boxes = []
    for fmt in settings.FORMATS:
        n = view["formats"].get(fmt, 0)
        checked = not plays or fmt in plays
        label = "Heads-up" if fmt == "HU" else "Tables à plusieurs (3 à 9 joueurs)"
        boxes.append(f'<label class="st-opt"><input type="checkbox" value="{fmt}"{" checked" if checked else ""}>'
                     f'<span>{label} <span class="muted">{num(n, 0)} main(s)</span></span></label>')
    return f"""
<h2>Ce que tu joues</h2>
<div class="card st-box" data-setting="formats">
<fieldset><legend>Formats</legend>{"".join(boxes)}</fieldset>
<div class="st-status" aria-live="polite"></div>
<p class="note">Un format que tu ne joues pas sort des menus : onglets, choix de format des pages, études et entraîneur du
heads-up, adversaires du menu. Tes mains restent gardées : coche-le à nouveau pour le retrouver.</p>
</div>"""


def _coach_html(view: dict) -> str:
    chosen = view["coach"]
    n = view["students"]
    count = f"{n} élève{'s' if n > 1 else ''}" if n else "aucun élève pour l'instant"
    options = (_radio("coach", "true", "Je suis coach : le menu montre les <b>Élèves</b>", chosen is True)
               + _radio("coach", "false", "Je ne coache pas : pas d'Élèves dans le menu", chosen is False)
               + _radio("coach", "", "Automatique : les Élèves dès que tu en as un", chosen is None, f"({count})"))
    return f"""
<h2>Coaching</h2>
<div class="card st-box" data-setting="coach">
<fieldset><legend>Tes élèves</legend>{options}</fieldset>
<div class="st-status" aria-live="polite"></div>
<p class="note">Chaque élève a son espace : ses mains, son Leakfinding, son rapport PDF et la présentation de la séance.</p>
</div>"""


def _field_html(view: dict) -> str:
    lo, hi = settings.MIN_HANDS_RANGE
    return f"""
<h2>Étude du field</h2>
<div class="card st-box" data-setting="min_hands">
<label class="st-row">Mains minimum contre un adversaire pour étudier ses leaks
<input type="number" min="{lo}" max="{hi}" step="10" value="{view['min_hands']}" aria-label="Mains minimum"></label>
<div class="st-status" aria-live="polite"></div>
<p class="note">Les réguliers et les récréatifs que tu as croisés au moins autant ont leur fiche dans l'Étude du field :
leurs leaks à exploiter, et ce qui distingue leur value de leurs bluffs.</p>
</div>"""


def _solver_html(view: dict) -> str:
    options = "".join(f'<option value="{t:g}"{" selected" if abs(t - view["precision"]) < 1e-9 else ""}>'
                      f'{num(t, 1)} % du pot</option>' for t in postflop.PRECISIONS)
    return f"""
<h2>Solveur</h2>
<div class="card st-box">
<label class="st-row">Précision des résolutions <select data-precision aria-label="Précision">{options}</select></label>
<div class="st-status" aria-live="polite"></div>
<p class="note">L'exploitabilité visée en % du pot, pour les résolutions suivantes : explorateur, séries de spots, analyse
de tes mains (plus bas = plus précis, plus long).</p>
</div>"""


def build_settings_page(view: dict, embed: bool = True) -> str:
    """view : Library.settings_view()."""
    body = f"""<div class="meta">Tes réglages, pour toute l'application (ton espace : chaque élève garde les siens).</div>
{_hero_html(view)}
{_formats_html(view)}
{_coach_html(view)}
{_field_html(view)}
{_solver_html(view)}
<h2>Ailleurs</h2>
<div class="card"><ul class="st-links">
<li><b>Types des adversaires</b> (régulier ou récréatif) et <b>alias</b> : onglet Joueurs et alias.</li>
<li><b>Période d'analyse</b> : en haut des pages (en ce moment : {escape(view['period'])}).</li>
<li><b>Sauvegarde</b> de tes calculs : menu Sauvegarde.</li>
</ul></div>
"""
    return html_page("Paramètres", f"<style>{STYLE}</style>{body}", embed, script=SCRIPT)
