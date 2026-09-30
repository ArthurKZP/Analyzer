(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const frame = $('frame');
  const TABS = {
    moi: [['bilan', 'Bilan'], ['preflop', 'Mon préflop'], ['spots', 'Mes spots']],
    adv: [['plan', 'Plan de jeu'], ['preflop', 'Préflop'], ['rapport', 'Rapport'], ['spots', 'Spots']],
  };
  let state = null;
  let route = null;

  // ---------- utilitaires ----------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) {
      if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : document.createTextNode(c));
    }
    return node;
  }
  const num = (x, d = 1) => (Math.round(x * 10 ** d) / 10 ** d).toLocaleString('fr-FR', { maximumFractionDigits: d });
  const signed = (x) => (x > 0 ? '+' : x < 0 ? '−' : '') + num(Math.abs(x));
  const tone = (x) => (x > 0 ? 'pos' : x < 0 ? 'neg' : '');

  // ---------- données ----------
  async function loadState() {
    const res = await fetch('/api/state');
    state = await res.json();
    renderSidebar();
  }

  // ---------- navigation ----------
  function parseRoute() {
    const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
    const tabOf = (view, name, fallback) => (TABS[view].some(([id]) => id === name) ? name : fallback);
    if (parts[0] === 'importer') return { view: 'importer' };
    if (parts[0] === 'adversaire' && parts[1]) return { view: 'adv', player: parts[1], tab: tabOf('adv', parts[2], 'plan') };
    if (parts[0] === 'moi') return { view: 'moi', tab: tabOf('moi', parts[1], 'bilan') };
    return state && state.hands ? { view: 'moi', tab: 'bilan' } : { view: 'importer' };
  }

  function hashFor(r) {
    if (r.view === 'adv') return '#/adversaire/' + encodeURIComponent(r.player) + '/' + r.tab;
    if (r.view === 'moi') return '#/moi/' + r.tab;
    return '#/importer';
  }

  function srcFor(r) {
    if (r.view === 'adv') return '/p/' + encodeURIComponent(r.player) + '/' + r.tab;
    return '/moi/' + r.tab;
  }

  function render() {
    route = parseRoute();
    closeDrawer();
    renderHeader();
    renderTabs();
    markActive();
    if (route.view === 'importer') return showImport();
    if (!state.hands) return showWelcome();
    if (route.view === 'adv' && !state.opponents.some((o) => o.name === route.player)) {
      return showPanel(el('div', { class: 'welcome' }, el('h2', {}, 'Joueur introuvable'),
        el('p', {}, 'Aucune main contre ce joueur dans ton dossier.')));
    }
    showFrame(srcFor(route));
  }

  function renderHeader() {
    const title = $('title'), subtitle = $('subtitle');
    if (route.view === 'adv') {
      const o = state.opponents.find((x) => x.name === route.player);
      title.textContent = route.player;
      subtitle.textContent = o
        ? o.hands + ' mains · ton résultat ' + signed(o.net_bb) + ' bb (' + signed(o.bb100) + ' bb/100) · dernière main le ' + o.last
        : '';
    } else if (route.view === 'moi') {
      title.textContent = 'Mon jeu';
      subtitle.textContent = state.hands
        ? state.hands + ' mains contre ' + state.opponents.length + ' adversaire(s) · ' + state.first + ' → ' + state.last
        : '';
    } else {
      title.textContent = 'Importer des mains';
      subtitle.textContent = 'Historiques Betclic (.txt) — les mains déjà présentes sont ignorées';
    }
    document.title = (route.view === 'adv' ? route.player : title.textContent) + ' — Analyzer HU';
  }

  function renderTabs() {
    const tabs = $('tabs');
    tabs.textContent = '';
    const list = route.view === 'adv' ? TABS.adv : route.view === 'moi' ? TABS.moi : [];
    tabs.hidden = !list.length || !state.hands;
    for (const [id, label] of list) {
      const r = Object.assign({}, route, { tab: id });
      tabs.append(el('a', { href: hashFor(r), 'aria-current': id === route.tab ? 'page' : null }, label));
    }
  }

  function markActive() {
    document.querySelectorAll('.nav a').forEach((a) => {
      a.setAttribute('aria-current', a.dataset.view === route.view ? 'page' : 'false');
    });
    document.querySelectorAll('.opps a').forEach((a) => {
      a.setAttribute('aria-current', route.view === 'adv' && a.dataset.name === route.player ? 'page' : 'false');
    });
  }

  // ---------- menu latéral ----------
  function renderSidebar() {
    $('hero-name').textContent = state.hero ? 'Toi : ' + state.hero : 'Aucune main chargée';
    $('opp-count').textContent = state.opponents.length ? '(' + state.opponents.length + ')' : '';
    const q = $('opp-search').value.trim().toLowerCase();
    const list = $('opps');
    list.textContent = '';
    const shown = state.opponents.filter((o) => !q || o.name.toLowerCase().includes(q));
    for (const o of shown) {
      list.append(el('li', {}, el('a', { href: '#/adversaire/' + encodeURIComponent(o.name), 'data-name': o.name },
        el('span', { class: 'n' }, o.name),
        el('span', { class: 'h' }, o.hands + ' mains'),
        el('span', { class: 'r ' + tone(o.net_bb) }, signed(o.net_bb) + ' bb'))));
    }
    if (!shown.length) {
      list.append(el('li', { class: 'muted' }, state.opponents.length ? 'Aucun joueur ne correspond.' : 'Aucun adversaire pour l\'instant.'));
    }
    if (route) markActive();
  }

  function closeDrawer() { $('app').classList.remove('drawer-open'); }

  // ---------- contenu ----------
  function showFrame(src) {
    $('panel').hidden = true;
    frame.hidden = false;
    if (frame.dataset.src === src) return;
    frame.dataset.src = src;
    $('loading').hidden = false;
    frame.src = src;
  }

  function showPanel(content) {
    frame.hidden = true;
    $('loading').hidden = true;
    const panel = $('panel');
    panel.hidden = false;
    panel.textContent = '';
    panel.append(content);
  }

  function showWelcome() {
    showPanel(el('div', { class: 'welcome' },
      el('h2', {}, 'Bienvenue'),
      el('p', {}, 'Importe tes historiques de mains pour analyser tes adversaires et ton propre jeu.'),
      el('a', { class: 'primary', href: '#/importer', style: 'display:inline-block;text-decoration:none' }, 'Importer des mains')));
  }

  function showImport() {
    const input = el('input', { type: 'file', multiple: true, accept: '.txt,.log,.hh', hidden: true });
    const result = el('div', { class: 'result', 'aria-live': 'polite' });
    const drop = el('div', { class: 'drop' },
      el('div', { class: 'drop-title' }, 'Dépose tes historiques ici'),
      el('div', { class: 'muted' }, 'ou'),
      el('button', { type: 'button', class: 'primary', onclick: () => input.click() }, 'Choisir des fichiers'),
      el('div', { class: 'muted small' }, 'Betclic (.txt), plusieurs fichiers à la fois'));
    input.addEventListener('change', () => upload(input.files, result));
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      drop.classList.remove('over');
      upload(e.dataTransfer.files, result);
    });
    const folder = el('p', { class: 'muted small' }, 'Dossier des mains : ' + state.folder + ' · ',
      el('button', { type: 'button', class: 'link', onclick: () => reload(result) }, 'Recharger le dossier'),
      ' (si tu y as copié des fichiers à la main)');
    showPanel(el('div', { class: 'import' }, drop, input, result, folder));
  }

  async function upload(fileList, result) {
    if (!fileList || !fileList.length) return;
    result.textContent = 'Import en cours…';
    try {
      const files = await Promise.all([...fileList].map(async (f) => ({ name: f.name, content: await f.text() })));
      const res = await fetch('/api/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ files }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Import impossible.');
      state = data.state;
      frame.dataset.src = '';
      renderSidebar();
      showImportResult(data, result);
    } catch (err) {
      result.textContent = '';
      result.append(el('p', { class: 'error' }, err.message || String(err)));
    }
  }

  function showImportResult(data, result) {
    result.textContent = '';
    result.append(el('p', {}, data.added
      ? el('b', {}, data.added + ' nouvelle(s) main(s) ajoutée(s).')
      : 'Aucune nouvelle main.', ' ',
    data.added ? el('a', { href: '#/moi' }, 'Voir mon jeu') : ''));
    const rows = data.files.map((f) => el('tr', {},
      el('td', {}, f.name),
      el('td', { class: f.status === 'importé' ? 'ok' : f.status === 'déjà importé' ? '' : 'ko' }, f.status),
      el('td', { class: 'num' }, String(f.hands)),
      el('td', { class: 'num' }, String(f.new))));
    result.append(el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'Fichier'), el('th', {}, 'Statut'), el('th', { class: 'num' }, 'Mains'), el('th', { class: 'num' }, 'Nouvelles'))),
      el('tbody', {}, rows)));
  }

  async function reload(result) {
    result.textContent = 'Rechargement…';
    const res = await fetch('/api/reload', { method: 'POST' });
    state = await res.json();
    frame.dataset.src = '';
    renderSidebar();
    result.textContent = state.hands + ' mains chargées.';
  }

  // ---------- événements ----------
  frame.addEventListener('load', () => { $('loading').hidden = true; });

  // Une page du cadre a suivi un lien interne (ex. « voir les mains ») : on met l'onglet à jour.
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin || !e.data || e.data.type !== 'analyzer-page') return;
    const parts = String(e.data.path).split('/').filter(Boolean).map(decodeURIComponent);
    let r = null;
    if (parts[0] === 'p' && parts.length === 3) r = { view: 'adv', player: parts[1], tab: parts[2] };
    else if (parts[0] === 'moi' && parts.length === 2) r = { view: 'moi', tab: parts[1] };
    if (!r) return;
    frame.dataset.src = srcFor(r);
    if (route && route.view === r.view && route.player === r.player && route.tab === r.tab) return;
    route = r;
    history.replaceState(null, '', hashFor(r));
    renderHeader();
    renderTabs();
    markActive();
  });

  $('opp-search').addEventListener('input', renderSidebar);
  $('menu').addEventListener('click', () => $('app').classList.toggle('drawer-open'));
  $('backdrop').addEventListener('click', closeDrawer);
  window.addEventListener('hashchange', render);

  loadState().then(render).catch((err) => {
    showPanel(el('p', { class: 'error' }, 'Impossible de joindre le serveur Analyzer : ' + err.message));
  });
})();
