"""Étude du field, onglet « Les joueurs » : tes adversaires en heads-up et aux tables à plusieurs, réguliers et
récréatifs, avec leur type réglable, et les alias qui regroupent les pseudos d'un même joueur (les bluffs des réguliers
ont leur onglet, bluffs_page.py)."""
from __future__ import annotations

from html import escape
from typing import Optional

from ..report import html_page
from ..selfreport import OPP_SCRIPT, OPP_STYLE
from .leaks_page import KIND_SCRIPT, opponents_html

ALIAS_STYLE = """
.al-box p { margin: 0 0 8px; }
.al-list { margin: 0; padding: 0; list-style: none; }
.al-list li { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; padding: 7px 0;
  border-top: 1px solid var(--border); }
.al-list b { overflow-wrap: anywhere; }
.al-list .al-pseudos { flex: 1 1 220px; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.al-tag { margin-left: 6px; font-size: 11px; color: var(--muted); white-space: nowrap; cursor: help; }
th.al-cell, td.al-cell { width: 30px; padding-right: 0; }
.al-bar { position: fixed; left: 50%; bottom: 12px; transform: translateX(-50%); z-index: 5; width: min(680px, calc(100% - 32px));
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 10px 12px; border-radius: 10px;
  border: 1px solid var(--border); background: var(--surface); box-shadow: 0 6px 24px rgba(0,0,0,.18); }
.al-bar[hidden] { display: none; }
.al-bar .al-what { flex: 1 1 100%; font-size: 13px; overflow-wrap: anywhere; }
.al-bar input { flex: 1 1 160px; min-width: 0; font: inherit; font-size: 13px; padding: 5px 9px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--page); color: var(--ink); }
.al-bar button, .al-list button { font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.al-bar button.go { background: var(--series-1); border-color: var(--series-1); color: #fff; font-weight: 600; }
.al-bar button:disabled, .al-list button:disabled { opacity: .5; cursor: default; }
.al-bar .al-err { flex: 1 1 100%; color: var(--alert); font-size: 12px; }
main { padding-bottom: 120px; }
"""

ALIAS_SCRIPT = """
(function () {
  var bar = document.querySelector('.al-bar');
  if (!bar) return;
  var picked = [], what = bar.querySelector('.al-what'), name = bar.querySelector('.al-name');
  var go = bar.querySelector('.al-go'), err = bar.querySelector('.al-err');
  function boxes() { return Array.prototype.slice.call(document.querySelectorAll('input.al-pick')); }
  function update() {
    bar.hidden = !picked.length;
    what.textContent = picked.length === 1 ? '1 pseudo coché : ' + picked[0]
      : picked.length + ' pseudos cochés : ' + picked.join(' · ');
    name.placeholder = picked[0] || '';
    err.hidden = true;
  }
  document.addEventListener('change', function (e) {
    var box = e.target;
    if (!box.classList || !box.classList.contains('al-pick')) return;
    var i = picked.indexOf(box.value);
    if (box.checked && i < 0) picked.push(box.value);
    if (!box.checked && i >= 0) picked.splice(i, 1);
    boxes().forEach(function (b) { if (b.value === box.value) b.checked = box.checked; });  // le même dans les deux listes
    update();
  });
  bar.querySelector('.al-clear').addEventListener('click', function () {
    picked = [];
    boxes().forEach(function (b) { b.checked = false; });
    update();
  });
  function send(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (res) {
        return res.json().then(function (data) { if (!res.ok) throw new Error(data.error || res.statusText); return data; });
      })
      .then(function () {  // les noms ont changé : le menu de l'application aussi
        if (window.parent !== window) window.parent.postMessage({ type: 'analyzer-refresh' }, location.origin);
        location.reload();
      });
  }
  go.addEventListener('click', function () {
    go.disabled = true;
    send('/api/alias', { alias: name.value.trim() || picked[0], pseudos: picked }).catch(function (e) {
      err.textContent = e.message;
      err.hidden = false;
      go.disabled = false;
    });
  });
  name.addEventListener('keydown', function (e) { if (e.key === 'Enter') go.click(); });
  document.querySelectorAll('.al-undo').forEach(function (b) {
    b.addEventListener('click', function () {
      if (!confirm('Défaire l\\'alias « ' + b.dataset.alias + ' » ? Ses pseudos redeviennent des joueurs à part.')) return;
      b.disabled = true;
      send('/api/alias/defaire', { alias: b.dataset.alias }).catch(function () { b.disabled = false; });
    });
  });
})();
"""


def _tile(label: str, players: list[dict]) -> str:
    recs = sum(1 for o in players if o.get("kind") == "rec")
    return (f'<div class="tile"><div class="label">{escape(label)}</div><div class="value">{len(players)}</div>'
            f'<div class="sub">{len(players) - recs} régulier(s) · {recs} récréatif(s)</div></div>')


def aliases_html(groups: dict[str, list[str]]) -> str:
    """Les alias déjà faits, chacun avec ses pseudos et de quoi le défaire, et comment en faire un."""
    items = "".join(
        f'<li><b>{escape(alias)}</b><span class="al-pseudos">{escape(" · ".join(pseudos))}</span>'
        f'<button type="button" class="al-undo" data-alias="{escape(alias)}">Défaire</button></li>'
        for alias, pseudos in groups.items())
    listing = f'<ul class="al-list">{items}</ul>' if items else ""
    return (f'<h2>Alias</h2><div class="card al-box"><p class="note">Un même joueur peut avoir plusieurs pseudos (un par '
            "site, ou un pseudo changé) : coche-les dans les listes ci-dessous et donne-leur un nom. Ses mains, ses "
            "stats et son type sont alors réunis sous ce nom, partout dans l'application (tes mains et celles de tes "
            f'élèves).</p>{listing}</div>'
            '<div class="al-bar" hidden><span class="al-what"></span>'
            '<input class="al-name" maxlength="60" aria-label="Nom de l\'alias (par défaut, le premier pseudo coché)">'
            '<button type="button" class="go al-go">Regrouper sous ce nom</button>'
            '<button type="button" class="al-clear">Annuler</button><span class="al-err" hidden></span></div>')


def build_players_page(heads_up: list[dict], ring: list[dict], groups: Optional[dict[str, list[str]]] = None,
                       embed: bool = True) -> str:
    """heads_up, ring : tes adversaires (Library.summary()["opponents"], Library.ring_opponents_view()) ; groups :
    les alias ({alias: [ses pseudos]})."""
    groups = groups or {}
    if not heads_up and not ring:
        body = '<p class="note">Aucun adversaire pour l\'instant : importe des mains.</p>'
        return html_page("Les joueurs", body, embed)
    tiles = "".join(_tile(label, players) for label, players in (("En heads-up", heads_up),
                                                                    ("Aux tables à plusieurs", ring)) if players)
    body = f"""<div class="meta">Le field : tes adversaires en heads-up et aux tables à plusieurs, réguliers et récréatifs.
Contre un régulier, ton jeu se compare à la théorie ; contre un récréatif, l'exploitation prime.</div>
<div class="tiles">{tiles}</div>
{aliases_html(groups)}
{opponents_html(heads_up, title="En heads-up", pick=True, groups=groups)}
{opponents_html(ring, ring=True, title="Aux tables à plusieurs", pick=True, groups=groups)}
<p class="note">Le type d'un joueur est le même partout : choisi ici, dans sa fiche ou dans le Leakfinding, il vaut en
heads-up et aux tables à plusieurs. Sans choix, la suggestion dépend du jeu : en heads-up, ses stats au bouton et à la
BB ; aux tables à plusieurs, ses fréquences à la taille de table où il a le plus joué.</p>
"""
    return html_page("Les joueurs", f"<style>{OPP_STYLE}{ALIAS_STYLE}</style>{body}", embed,
                     script=KIND_SCRIPT + OPP_SCRIPT + ALIAS_SCRIPT)
