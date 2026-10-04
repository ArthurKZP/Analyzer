(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const frame = $('frame');
  const TABS = {
    moi: [['bilan', 'Bilan'], ['preflop', 'Mon préflop'], ['spots', 'Mes spots'], ['solveur', 'Face au solveur']],
    adv: [['plan', 'Plan de jeu'], ['preflop', 'Préflop'], ['rapport', 'Rapport'], ['spots', 'Spots'],
      ['solveur', 'Face au solveur']],
    // L'explorateur part du préflop ; les séries de spots et les coups joués ont chacun leur onglet.
    etudes: [['explorateur', 'Explorateur'], ['srp', 'SRP'], ['3bet', 'Pots 3bet'], ['4bet', 'Pots 4bet'], ['coups', 'Coups joués']],
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
    if (parts[0] === 'etudes') return { view: 'etudes', tab: tabOf('etudes', parts[1], 'explorateur') };
    if (parts[0] === 'entraineur') return { view: 'entraineur' };
    if (parts[0] === 'sauvegarde') return { view: 'sauvegarde' };
    if (parts[0] === 'adversaire' && parts[1]) return { view: 'adv', player: parts[1], tab: tabOf('adv', parts[2], 'plan') };
    if (parts[0] === 'moi') return { view: 'moi', tab: tabOf('moi', parts[1], 'bilan') };
    return state && state.hands ? { view: 'moi', tab: 'bilan' } : { view: 'importer' };
  }

  function hashFor(r) {
    if (r.view === 'adv') return '#/adversaire/' + encodeURIComponent(r.player) + '/' + r.tab;
    if (r.view === 'moi') return '#/moi/' + r.tab;
    if (r.view === 'etudes') return '#/etudes/' + r.tab;
    if (r.view === 'entraineur') return '#/entraineur';
    if (r.view === 'sauvegarde') return '#/sauvegarde';
    return '#/importer';
  }

  function srcFor(r) {
    if (r.view === 'adv') return '/p/' + encodeURIComponent(r.player) + '/' + r.tab;
    if (r.view === 'etudes') return r.tab === 'explorateur' ? '/explorateur/preflop' : '/etudes/' + r.tab;
    if (r.view === 'entraineur') return '/entraineur';
    return '/moi/' + r.tab;
  }

  function render() {
    route = parseRoute();
    closeDrawer();
    renderHeader();
    renderTabs();
    markActive();
    if (route.view === 'importer') return showImport();
    // sans mains : les spots d'étude suffisent à l'entraîneur et à l'explorateur
    if (route.view === 'entraineur' || route.view === 'etudes') return showFrame(srcFor(route));
    if (route.view === 'sauvegarde') return showBackup();
    if (!state.hands) return showWelcome();
    if (route.view === 'adv' && !state.opponents.some((o) => o.name === route.player)) {
      return showPanel(el('div', { class: 'welcome' }, el('h2', {}, 'Joueur introuvable'),
        el('p', {}, 'Aucune main contre ce joueur dans ton dossier.')));
    }
    showFrame(srcFor(route));
  }

  function renderHeader() {
    const title = $('title'), subtitle = $('subtitle');
    renderKind(null);
    if (route.view === 'adv') {
      const o = state.opponents.find((x) => x.name === route.player);
      title.textContent = route.player;
      subtitle.textContent = o
        ? o.hands + ' mains · ton résultat ' + signed(o.net_bb) + ' bb (' + signed(o.bb100) + ' bb/100) · dernière main le ' + o.last
        : '';
      renderKind(o);
    } else if (route.view === 'moi') {
      title.textContent = 'Mon jeu';
      subtitle.textContent = state.hands
        ? state.hands + ' mains contre ' + state.opponents.length + ' adversaire(s) · ' + state.first + ' → ' + state.last
        : '';
    } else if (route.view === 'etudes') {
      title.textContent = 'Études du solveur';
      subtitle.textContent = 'Coups résolus avec GTOpen, gardés sur ton ordinateur pour être réexplorés';
    } else if (route.view === 'sauvegarde') {
      title.textContent = 'Sauvegarde';
      subtitle.textContent = 'Tes calculs (tailles, résolutions, mains analysées, études) à l\'abri, en ligne si tu veux';
    } else if (route.view === 'entraineur') {
      title.textContent = 'Entraîneur';
      subtitle.textContent = 'Joue des mains sur les spots résolus : le solveur juge chaque décision';
    } else {
      title.textContent = 'Importer des mains';
      subtitle.textContent = 'Historiques Betclic (.txt) — les mains déjà présentes sont ignorées';
    }
    document.title = (route.view === 'adv' ? route.player : title.textContent) + ' — Analyzer HU';
  }

  // Type de l'adversaire : contre un récréatif, ses mains sortent des comparaisons à la théorie.
  const KIND_NAMES = { reg: 'Régulier', rec: 'Récréatif' };
  function renderKind(o) {
    const box = $('kind'), select = $('kind-select');
    box.hidden = !o;
    if (!o) return;
    select.textContent = '';
    const auto = o.suggestion ? 'Auto : ' + KIND_NAMES[o.suggestion] : 'Auto : Régulier (pas de signal net)';
    select.append(el('option', { value: '' }, auto), el('option', { value: 'reg' }, 'Régulier'), el('option', { value: 'rec' }, 'Récréatif'));
    select.value = o.source === 'toi' ? o.kind : '';
    box.title = (o.reasons.length ? 'Signaux : ' + o.reasons.join(', ') + '. ' : '')
      + 'Contre un récréatif, tes mains ne sont pas comparées à la théorie (Face au solveur, Préflop).';
    select.onchange = async () => {
      const res = await fetch('/api/joueurs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: o.name, kind: select.value || null }) });
      if (!res.ok) return;
      state = await res.json();
      renderSidebar();
      frame.dataset.src = '';  // les pages qui dépendent du type sont recalculées
      render();
    };
  }

  function renderTabs() {
    const tabs = $('tabs');
    tabs.textContent = '';
    const list = TABS[route.view] || [];
    tabs.hidden = !list.length || (!state.hands && route.view !== 'etudes');
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
        el('span', { class: 'n' }, o.name, o.kind === 'rec' ? el('span', { class: 'k', title: 'Récréatif' }, 'réc.') : null),
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

  // ---------- sauvegarde ----------
  const size = (n) => (n >= 1e9 ? num(n / 1e9, 1) + ' Go' : n >= 1e6 ? num(n / 1e6, 1) + ' Mo' : Math.max(1, Math.round(n / 1e3)) + ' Ko');
  let backupTimer = null;

  async function backupApi(path, body) {
    const res = await fetch('/api/sauvegarde' + path, body === undefined ? {}
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Erreur ' + res.status);
    return data;
  }

  async function showBackup() {
    clearTimeout(backupTimer);
    let v;
    try { v = await backupApi(''); } catch (err) { return showPanel(el('p', { class: 'error' }, err.message)); }
    if (route.view !== 'sauvegarde') return;
    const busy = !!v.running;
    const dest = el('input', { type: 'text', id: 'bk-dest', value: v.dest, spellcheck: 'false', disabled: busy,
      placeholder: 'C:\\Users\\toi\\OneDrive\\Analyzer   ou   gdrive:Analyzer' });
    const studies = el('input', { type: 'checkbox', checked: v.studies, disabled: busy });
    const auto = el('input', { type: 'checkbox', checked: v.auto, disabled: busy });
    const status = el('div', { 'aria-live': 'polite' });
    const settings = () => ({ dest: dest.value, studies: studies.checked, auto: auto.checked });
    const run = async (path) => {
      try {
        await backupApi('/reglages', settings());
        if (path === '/restaurer' && !window.confirm('Restaurer la dernière sauvegarde de ' + dest.value + ' ? '
          + 'Les fichiers plus récents sur cet ordinateur sont gardés.')) return showBackup();
        await backupApi(path, {});
      } catch (err) { status.textContent = err.message; return; }
      showBackup();
    };
    const save = el('button', { type: 'button', class: 'secondary', disabled: busy, onclick: async () => {
      try { await backupApi('/reglages', settings()); showBackup(); } catch (err) { status.textContent = err.message; }
    } }, 'Enregistrer les réglages');
    const now = el('button', { type: 'button', class: 'primary', disabled: busy, onclick: () => run('/lancer') }, 'Sauvegarder maintenant');
    const back = el('button', { type: 'button', class: 'secondary', disabled: busy, onclick: () => run('/restaurer') }, 'Restaurer');
    if (v.running) {
      status.append(el('p', {}, el('span', { class: 'spinner', style: 'display:inline-block;vertical-align:middle;margin-right:8px' }),
        v.running === 'restore' ? 'Restauration en cours…' : 'Sauvegarde en cours…'));
    } else if (v.error) status.append(el('p', { class: 'error' }, v.error));
    else if (v.result && v.result.restored !== undefined) {
      status.append(el('p', {}, 'Restauré : ' + v.result.restored + ' fichier(s) de ' + v.result.archive
        + (v.result.studies ? ', ' + v.result.studies + ' étude(s)' : '') + '.'));
    }
    if (v.last) {
      const d = new Date(v.last.t * 1000);
      status.append(el('p', { class: 'muted small' }, 'Dernière sauvegarde le ' + d.toLocaleDateString('fr-FR') + ' à '
        + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) + ' vers ' + v.last.dest + ' : archive de '
        + size(v.last.archive_size) + (v.last.with_studies ? ', ' + v.last.studies + ' étude(s) copiée(s)' : '') + '.'));
    }
    if (v.log.length) status.append(el('pre', { class: 'log' }, v.log.join('\n')));
    showPanel(el('div', { class: 'backup' },
      el('div', { class: 'box' },
        el('label', { class: 'field', for: 'bk-dest' }, 'Où sauvegarder', dest),
        el('label', { class: 'check' }, studies, el('span', {}, 'Avec les études (' + v.studies_count + ' sur cet ordinateur, '
          + size(v.studies_size) + ') : seules les nouvelles sont copiées ensuite. Sans elles, l\'essentiel ne pèse que '
          + size(v.essentials_size) + '.')),
        el('label', { class: 'check' }, auto, el('span', {}, 'Sauvegarder automatiquement après chaque calcul '
          + '(résolution, choix des tailles, main analysée ; au plus une fois par quart d\'heure)')),
        el('div', { class: 'buttons' }, now, save, back),
        status),
      el('div', { class: 'box help' },
        el('h2', {}, 'Où mettre tes sauvegardes ?'),
        el('p', {}, el('b', {}, 'Un dossier synchronisé'), ' (le plus simple) : un dossier de OneDrive, Google Drive ou Dropbox '
          + 'sur ton ordinateur, par exemple ', el('code', {}, 'C:\\Users\\toi\\OneDrive\\Analyzer'),
        '. Leur application envoie la sauvegarde en ligne toute seule.'),
        el('p', {}, el('b', {}, 'Un stockage en ligne avec rclone'), ' (Google Drive, OneDrive, Dropbox, un serveur SFTP, S3…) : '
          + 'installe rclone (rclone.org), configure ton stockage avec ', el('code', {}, 'rclone config'),
        ', puis indique-le sous la forme ', el('code', {}, 'nom:dossier'), ' (', v.rclone ? 'rclone est installé' : 'rclone n\'est pas installé', ').'),
        el('p', {}, 'Ce qui est gardé : les tailles de mise choisies (les plus longues à recalculer), les résolutions, '
          + 'les mains analysées, ton journal d\'entraînement et, en option, les études. Les 10 dernières archives sont '
          + 'gardées. Sur un autre ordinateur : même destination, puis « Restaurer ».'),
        el('p', { class: 'muted small' }, 'Dossier de travail d\'Analyzer : ' + v.home
          + ' (variable ANALYZER_HOME pour en changer).'))));
    if (v.running) backupTimer = setTimeout(() => { if (route.view === 'sauvegarde') showBackup(); }, 1500);
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
    else if (parts[0] === 'etudes' && parts.length <= 2) r = { view: 'etudes', tab: parts[1] || 'srp' };
    else if (parts[0] === 'entraineur' && parts.length === 1) r = { view: 'entraineur' };
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
