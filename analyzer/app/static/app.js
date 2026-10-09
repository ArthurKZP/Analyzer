(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const frame = $('frame');
  const TABS = {
    moi: [['bilan', 'Bilan'], ['leaks', 'Leakfinding'], ['mains', 'Mains de départ'], ['preflop', 'Mon préflop'], ['spots', 'Mes spots'],
      ['solveur', 'Face au solveur'], ['tables', 'Tables à plusieurs']],
    // étude du field : le leakfinding de tes adversaires (réguliers, récréatifs), les bluffs des réguliers en heads-up
    field: [['regs', 'Réguliers'], ['recs', 'Récréatifs'], ['bluffs', 'Bluffs des réguliers']],
    parametres: [['general', 'Général'], ['joueurs', 'Joueurs et alias']],
    eleve: [['leaks', 'Leakfinding'], ['mains', 'Mains de départ'], ['preflop', 'Préflop'], ['solveur', 'Face au solveur'], ['spots', 'Mains'],
      ['tables', 'Tables à plusieurs'], ['importer', 'Importer']],
    adv: [['plan', 'Plan de jeu'], ['preflop', 'Préflop'], ['rapport', 'Rapport'], ['spots', 'Spots'],
      ['solveur', 'Face au solveur'], ['bluffs', 'Ses bluffs']],
    // L'explorateur part du préflop ; les séries heads-up (SRP, pots 3bet et 4bet), les séries 6-max et les coups joués
    // ont chacun leur onglet.
    etudes: [['explorateur', 'Explorateur'], ['plan', 'Plan de jeu suggéré'], ['hu', 'Heads-up'], ['6max', '6-max'],
      ['coups', 'Coups joués']],
  };
  const FORMAT_TABS = ['bilan', 'leaks', 'preflop'];  // pages qui ont un choix heads-up / tables à plusieurs
  // Les formats que tu joues (Paramètres) : sans choix, tous. Un format que tu ne joues pas sort des menus.
  const plays = (fmt) => !state || !state.settings || !state.settings.plays || state.settings.plays.includes(fmt);
  const HIDDEN_TABS = { HU: { moi: ['spots', 'solveur'], field: ['bluffs'], etudes: ['hu'] },
    ring: { moi: ['tables'], etudes: ['6max'] } };
  function tabsOf(view) {
    const hidden = ['HU', 'ring'].filter((f) => !plays(f)).flatMap((f) => HIDDEN_TABS[f][view] || []);
    return (TABS[view] || []).filter(([id]) => !hidden.includes(id));
  }
  const HU_SERIES = ['srp', '3bet', '4bet'];  // anciens onglets des études : l'onglet Heads-up
  let state = null;
  let route = null;
  let students = [];
  const hasHands = () => !!(state && (state.hands || state.ring_hands));  // heads-up ou tables à plusieurs, sur la période
  const hasAnyHands = () => !!(state && state.period && (state.period.all_hands || state.period.all_ring));  // toutes

  // Petites préférences gardées dans le navigateur (tri des adversaires, format du Leakfinding).
  function pref(key, value) {
    try {
      if (value === undefined) return localStorage.getItem('analyzer-' + key);
      if (value === null) localStorage.removeItem('analyzer-' + key);
      else localStorage.setItem('analyzer-' + key, value);
    } catch (e) { /* stockage indisponible : on fait sans */ }
    return null;
  }

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
    const tabOf = (view, name, fallback) => {
      const list = tabsOf(view);
      return list.some(([id]) => id === name) ? name : list.some(([id]) => id === fallback) ? fallback : (list[0] || [fallback])[0];
    };
    if (parts[0] === 'importer') return { view: 'importer' };
    if (parts[0] === 'etudes' && HU_SERIES.includes(parts[1])) return { view: 'etudes', tab: 'hu', family: parts[1] };
    if (parts[0] === 'etudes') return { view: 'etudes', tab: tabOf('etudes', parts[1], 'explorateur') };
    if (parts[0] === 'entraineur') return { view: 'entraineur' };
    if (parts[0] === 'sauvegarde') return { view: 'sauvegarde' };
    if (parts[0] === 'adversaire' && parts[1]) return { view: 'adv', player: parts[1], tab: tabOf('adv', parts[2], 'plan') };
    if (parts[0] === 'moi' && parts[1] === 'bluffs') return { view: 'field', tab: 'bluffs' };  // ancienne adresse
    if (parts[0] === 'moi') return { view: 'moi', tab: tabOf('moi', parts[1], 'bilan') };
    if (parts[0] === 'field' && parts[1] === 'joueurs') return { view: 'parametres', tab: 'joueurs' };  // ancienne adresse
    if (parts[0] === 'field' && parts[1] === 'joueur' && parts[2]) return { view: 'field', tab: 'joueur', player: parts[2], fmt: parts[3] || null };
    if (parts[0] === 'field' && parts[1] === 'groupe' && parts[2]) return { view: 'field', tab: 'groupe', group: parts[2], fmt: parts[3] || null };
    if (parts[0] === 'field') return { view: 'field', tab: tabOf('field', parts[1], 'regs') };
    if (parts[0] === 'parametres') return { view: 'parametres', tab: tabOf('parametres', parts[1], 'general') };
    if (parts[0] === 'eleves') return { view: 'eleves' };
    if (parts[0] === 'eleve' && parts[1]) return { view: 'eleve', student: parts[1], tab: tabOf('eleve', parts[2], 'leaks') };
    return hasHands() || hasAnyHands() ? { view: 'moi', tab: 'bilan' } : { view: 'importer' };
  }

  function hashFor(r) {
    if (r.view === 'adv') return '#/adversaire/' + encodeURIComponent(r.player) + '/' + r.tab;
    if (r.view === 'moi') return '#/moi/' + r.tab;
    if (r.view === 'field' && r.tab === 'joueur') return '#/field/joueur/' + encodeURIComponent(r.player) + (r.fmt ? '/' + r.fmt : '');
    if (r.view === 'field' && r.tab === 'groupe') return '#/field/groupe/' + encodeURIComponent(r.group) + (r.fmt ? '/' + r.fmt : '');
    if (r.view === 'field') return '#/field/' + r.tab;
    if (r.view === 'parametres') return '#/parametres/' + r.tab;
    if (r.view === 'etudes') return '#/etudes/' + r.tab;
    if (r.view === 'entraineur') return '#/entraineur';
    if (r.view === 'sauvegarde') return '#/sauvegarde';
    if (r.view === 'eleves') return '#/eleves';
    if (r.view === 'eleve') return '#/eleve/' + encodeURIComponent(r.student) + '/' + r.tab;
    return '#/importer';
  }

  function srcFor(r) {
    if (r.view === 'adv') return '/p/' + encodeURIComponent(r.player) + '/' + r.tab;
    if (r.view === 'etudes') {  // l'explorateur part du 6-max quand tu ne joues pas le heads-up
      if (r.tab === 'explorateur') return '/explorateur/preflop' + (plays('HU') ? '' : '#table=6-max');
      return '/etudes/' + r.tab + (r.family ? '#' + r.family : '');
    }
    if (r.view === 'entraineur') return '/entraineur';
    if (r.view === 'parametres') return '/parametres/' + r.tab;
    if (r.view === 'field') {  // le format choisi en dernier dans l'Étude du field (ou celui de la fiche)
      const fmt = r.fmt || pref('format:/field');
      const query = fmt ? '?format=' + encodeURIComponent(fmt) : '';
      if (r.tab === 'joueur') return '/field/joueur/' + encodeURIComponent(r.player) + query;
      if (r.tab === 'groupe') return '/field/groupe/' + encodeURIComponent(r.group) + query;
      return '/field/' + r.tab + query;
    }
    const space = r.view === 'eleve' ? '/eleve/' + encodeURIComponent(r.student) : '/moi';
    // le dernier format choisi (heads-up ou tables à plusieurs), le même pour le bilan, le Leakfinding et le préflop
    const fmt = FORMAT_TABS.includes(r.tab) ? pref('format:' + space) : null;
    return space + '/' + r.tab + (fmt ? '?format=' + encodeURIComponent(fmt) : '');
  }

  async function loadStudents() {
    try {
      const res = await fetch('/api/eleves');
      students = res.ok ? await res.json() : [];
    } catch (e) { students = []; }
  }

  async function render() {
    route = parseRoute();
    closePeriod();  // une autre page : le panneau de la période se ferme
    if (route.view === 'field' && location.hash !== hashFor(route)) history.replaceState(null, '', hashFor(route));  // #/moi/bluffs
    if (route.view === 'eleves' || route.view === 'eleve') await loadStudents();
    closeDrawer();
    renderHeader();
    renderTabs();
    markActive();
    if (route.view === 'importer') return showImport();
    // sans mains : les spots d'étude suffisent à l'entraîneur et à l'explorateur
    if (route.view === 'entraineur' || route.view === 'etudes' || route.view === 'parametres') return showFrame(srcFor(route));
    if (route.view === 'sauvegarde') return showBackup();
    if (route.view === 'eleves') return showStudents();
    if (route.view === 'eleve') {
      if (!students.some((s) => s.id === route.student)) {
        return showPanel(el('div', { class: 'welcome' }, el('h2', {}, 'Élève introuvable'), el('a', { href: '#/eleves' }, 'Voir les élèves')));
      }
      if (route.tab === 'importer') return showImport(route.student);
      const s = students.find((x) => x.id === route.student);
      if (!s.hands && !s.ring_hands) {
        return s.period && (s.period.all_hands || s.period.all_ring) ? showEmptyPeriod() : showImport(route.student);
      }
      return showFrame(srcFor(route));
    }
    if (!hasHands()) return hasAnyHands() ? showEmptyPeriod() : showWelcome();
    if (route.view === 'adv' && !state.opponents.some((o) => o.name === route.player)) {
      return showPanel(el('div', { class: 'welcome' }, el('h2', {}, 'Joueur introuvable'),
        el('p', {}, state.period.kind === 'all' ? 'Aucune main contre ce joueur dans ton dossier.'
          : 'Aucune main contre ce joueur sur la période choisie (' + state.period.label.toLowerCase() + ').')));
    }
    showFrame(srcFor(route));
  }

  function renderHeader() {
    const title = $('title'), subtitle = $('subtitle');
    renderKind(null);
    renderPeriod();
    if (route.view === 'adv') {
      const o = state.opponents.find((x) => x.name === route.player);
      title.textContent = route.player;
      const pseudos = (state.aliases || {})[route.player];  // un alias : ses pseudos
      subtitle.textContent = o
        ? o.hands + ' mains · ton résultat ' + signed(o.net_bb) + ' bb (' + signed(o.bb100) + ' bb/100) · dernière main le ' + o.last
          + (pseudos ? ' · pseudos : ' + pseudos.join(', ') : '')
        : '';
      renderKind(o);
    } else if (route.view === 'moi') {
      title.textContent = 'Mon jeu';
      subtitle.textContent = state.hands
        ? state.hands + ' mains contre ' + state.opponents.length + ' adversaire(s) · ' + state.first + ' → ' + state.last
          + (state.ring_hands ? ' · ' + state.ring_hands + ' mains aux tables à plusieurs' : '')
        : state.ring_hands ? state.ring_hands + ' mains aux tables à plusieurs' : '';
    } else if (route.view === 'field' && route.tab === 'joueur') {
      title.textContent = route.player;
      subtitle.textContent = 'Étude du field : ses leaks à exploiter, sa value et ses bluffs ligne par ligne';
    } else if (route.view === 'field' && route.tab === 'groupe') {
      title.textContent = 'Récréatifs ' + ({ passif: 'passifs', agressif: 'agressifs', prudent: 'prudents' }[route.group] || 'sans style marqué');
      subtitle.textContent = 'Étude du field : comment les jouer, leurs leaks et leurs lignes réunis';
    } else if (route.view === 'field') {
      title.textContent = 'Étude du field';
      subtitle.textContent = 'Comment jouent tes adversaires : leurs leaks à exploiter, leur value et leurs bluffs';
    } else if (route.view === 'parametres') {
      title.textContent = 'Paramètres';
      subtitle.textContent = 'Tes pseudos réunis, ce que tu joues, tes élèves, le type de tes adversaires et leurs alias';
    } else if (route.view === 'etudes') {
      title.textContent = 'Études du solveur';
      subtitle.textContent = 'Coups résolus avec GTOpen, gardés sur ton ordinateur pour être réexplorés';
    } else if (route.view === 'sauvegarde') {
      title.textContent = 'Sauvegarde';
      subtitle.textContent = 'Tes calculs (tailles, résolutions, mains analysées, études) à l\'abri, en ligne si tu veux';
    } else if (route.view === 'eleves') {
      title.textContent = 'Élèves';
      subtitle.textContent = 'Le leakfinding de tes élèves : ils t\'envoient leurs mains, Merlin trouve ce qu\'ils doivent travailler';
    } else if (route.view === 'eleve') {
      const s = students.find((x) => x.id === route.student);
      title.textContent = s ? s.name : 'Élève';
      subtitle.textContent = s ? (s.hands ? s.hands + ' mains · ' + (s.hero || '') + ' · ' + s.first + ' → ' + s.last
          + (s.ring_hands ? ' · ' + s.ring_hands + ' mains aux tables à plusieurs' : '')
        : s.ring_hands ? s.ring_hands + ' mains aux tables à plusieurs · ' + (s.hero || '')
          : 'Pas encore de mains : importe ses historiques') : '';
    } else if (route.view === 'entraineur') {
      title.textContent = 'Entraîneur';
      subtitle.textContent = 'Joue des mains sur les spots résolus : le solveur juge chaque décision';
    } else {
      title.textContent = 'Importer des mains';
      subtitle.textContent = 'Historiques Betclic, Winamax, Unibet (.txt, dossier ou archive .zip) — les mains déjà présentes sont ignorées';
    }
    document.title = (route.view === 'adv' ? route.player : title.textContent) + ' — Merlin';
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
    const list = tabsOf(route.view);
    tabs.hidden = !list.length || (!hasHands() && !hasAnyHands() && !['etudes', 'eleve', 'parametres'].includes(route.view));
    for (const [id, label] of list) {
      const r = Object.assign({}, route, { tab: id });
      tabs.append(el('a', { href: hashFor(r), 'aria-current': id === route.tab ? 'page' : null }, label));
    }
  }

  function markActive() {
    document.querySelectorAll('.nav a').forEach((a) => {
      const view = route.view === 'eleve' ? 'eleves' : route.view;
      a.setAttribute('aria-current', a.dataset.view === view ? 'page' : 'false');
    });
    document.querySelectorAll('.opps a').forEach((a) => {
      const current = a.dataset.fmt === 'ring' ? route.view === 'field' && route.tab === 'joueur' : route.view === 'adv';
      a.setAttribute('aria-current', current && a.dataset.name === route.player ? 'page' : 'false');
    });
  }

  // ---------- menu latéral ----------
  // Le menu des adversaires : ceux du heads-up (leur fiche) ou ceux des tables à plusieurs (leur fiche du field), selon
  // les formats que tu joues ; avec les deux, un choix (gardé).
  function oppFormat() {
    const both = plays('HU') && plays('ring') && state.opponents.length && (state.ring_opponents || []).length;
    if (both) return pref('opp-fmt') === 'ring' ? 'ring' : 'HU';
    return plays('HU') && (state.opponents.length || !(state.ring_opponents || []).length) ? 'HU' : 'ring';
  }

  function renderSidebar() {
    $('hero-name').textContent = state.hero ? 'Toi : ' + state.hero : 'Aucune main chargée';
    // Paramètres : les Élèves si tu es coach, l'entraîneur (spots heads-up) si tu joues le heads-up
    document.querySelector('.nav a[data-view="eleves"]').hidden = !(state.settings ? state.settings.coach : true);
    document.querySelector('.nav a[data-view="entraineur"]').hidden = !plays('HU');
    const fmt = oppFormat();
    const switcher = $('opp-fmt');
    const both = plays('HU') && plays('ring') && state.opponents.length && (state.ring_opponents || []).length;
    switcher.hidden = !both;
    switcher.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.fmt === fmt)));
    const source = fmt === 'HU' ? state.opponents : (state.ring_opponents || []);
    const plain = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();  // sans accents
    const q = plain($('opp-search').value.trim()), kind = $('opp-kind').value, sort = $('opp-sort').value;
    const list = $('opps');
    list.textContent = '';
    const shown = source.filter((o) => (!q || plain(o.name).includes(q)) && (!kind || o.kind === kind));
    const day = (d) => d.split('/').reverse().join('-');  // jj/mm/aaaa -> aaaa-mm-jj, pour comparer
    const order = {
      hands: (a, b) => b.hands - a.hands, net: (a, b) => b.net_bb - a.net_bb, loss: (a, b) => a.net_bb - b.net_bb,
      last: (a, b) => (day(a.last) < day(b.last) ? 1 : day(a.last) > day(b.last) ? -1 : 0),
      name: (a, b) => a.name.localeCompare(b.name, 'fr', { sensitivity: 'base' }),
    }[sort] || ((a, b) => b.hands - a.hands);
    shown.sort((a, b) => order(a, b) || b.hands - a.hands);
    const total = source.length;
    $('opp-count').textContent = !total ? '' : shown.length === total ? '(' + total + ')' : '(' + shown.length + ' / ' + total + ')';
    for (const o of shown) {
      const href = fmt === 'HU' ? '#/adversaire/' + encodeURIComponent(o.name) : '#/field/joueur/' + encodeURIComponent(o.name) + '/ring';
      list.append(el('li', {}, el('a', { href, 'data-name': o.name, 'data-fmt': fmt },
        el('span', { class: 'n' }, o.name, o.kind === 'rec' ? el('span', { class: 'k', title: 'Récréatif' }, 'réc.') : null),
        el('span', { class: 'h' }, o.hands + ' mains'),
        el('span', { class: 'r ' + tone(o.net_bb) }, signed(o.net_bb) + ' bb'))));
    }
    if (!shown.length) {
      list.append(el('li', { class: 'muted' }, source.length ? 'Aucun joueur ne correspond.'
        : state.period && state.period.kind !== 'all' ? 'Aucun adversaire sur cette période.' : 'Aucun adversaire pour l\'instant.'));
    }
    if (route) markActive();
  }

  function closeDrawer() { $('app').classList.remove('drawer-open'); }

  // ---------- période d'analyse ----------
  // Toutes les mains, les N dernières (de chaque format), les N derniers jours ou d'une date à une autre : le choix
  // vaut pour toutes les analyses de l'espace (toi, ou l'élève affiché) et reste gardé.
  const PERIOD_VIEWS = ['moi', 'field', 'adv', 'eleve'];

  function periodOwner() {
    if (route.view === 'eleve') {
      const s = students.find((x) => x.id === route.student);
      return s && s.period ? { api: '/api/eleves/' + encodeURIComponent(s.id) + '/periode', period: s.period, student: s.id } : null;
    }
    return state && state.period ? { api: '/api/periode', period: state.period } : null;
  }

  function renderPeriod() {
    const box = $('period'), button = $('period-btn');
    const owner = route && PERIOD_VIEWS.includes(route.view) ? periodOwner() : null;
    const p = owner && owner.period;
    if (!owner || $('period-pop').dataset.owner !== owner.api) closePeriod();  // un autre espace : le panneau se ferme
    box.hidden = !p || !(p.all_hands || p.all_ring);
    if (box.hidden) return;
    button.textContent = '';
    button.append(el('span', { class: 'muted' }, 'Période : '), p.label, ' ▾');
    button.classList.toggle('on', p.kind !== 'all');
    button.onclick = () => ($('period-pop').hidden ? openPeriod(owner) : closePeriod());
  }

  function closePeriod() {
    $('period-pop').hidden = true;
    $('period-btn').setAttribute('aria-expanded', 'false');
  }

  async function setPeriod(owner, body) {
    const res = await fetch(owner.api, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Période impossible.');
    if (owner.student) await loadStudents();
    else { state = data; renderSidebar(); }
    closePeriod();
    frame.dataset.src = '';  // les pages se recalculent sur la période
    render();
  }

  function openPeriod(owner) {
    const p = owner.period, pop = $('period-pop');
    const choice = (value, ...content) => {
      const input = el('input', { type: 'radio', name: 'period-kind', value });
      input.checked = p.kind === value;
      const row = el('label', { class: 'period-row' }, input, el('span', {}, ...content));
      row.addEventListener('focusin', (e) => { if (e.target !== input) input.checked = true; });  // un champ de la ligne : elle est choisie
      return row;
    };
    const number = (value, max, label) => el('input', { type: 'number', min: '1', max: String(max), step: '1',
      value: String(value), 'aria-label': label });
    const n = number(p.kind === 'last' ? p.n : 1000, 10000000, 'Nombre de mains');
    const days = number(p.kind === 'days' ? p.days : 30, 36500, 'Nombre de jours');
    const day = (value, label) => el('input', { type: 'date', value: value || '', min: p.first, max: p.last, 'aria-label': label });
    const from = day(p.kind === 'range' ? p.from : p.first, 'Premier jour');
    const to = day(p.kind === 'range' ? p.to : p.last, 'Dernier jour');
    const counts = [p.all_hands ? p.all_hands + ' en heads-up' : '', p.all_ring ? p.all_ring + ' aux tables à plusieurs' : '']
      .filter(Boolean).join(', ');
    const error = el('p', { class: 'period-err', hidden: true });
    const apply = el('button', { type: 'button', class: 'primary' }, 'Appliquer');
    pop.textContent = '';
    pop.append(
      el('div', { class: 'period-title' }, 'Analyser'),
      choice('all', 'Toutes les mains', el('span', { class: 'muted small' }, '(' + counts + ')')),
      choice('last', 'Les', n, 'dernières mains',
        el('span', { class: 'muted small period-hint' }, 'de chaque format (heads-up, tables à plusieurs)')),
      choice('days', 'Les', days, 'derniers jours'),
      choice('range', 'Du', from, 'au', to),
      el('p', { class: 'muted small' }, 'Pour toutes les analyses ' + (owner.student ? 'de l\'élève' : 'de ton jeu')
        + ' : bilan, Leakfinding, préflop, mains de départ, adversaires, rapports. Le choix reste gardé.'),
      error,
      el('div', { class: 'period-actions' }, apply, el('button', { type: 'button', class: 'secondary', onclick: closePeriod }, 'Annuler')));
    apply.addEventListener('click', () => {
      const kind = (pop.querySelector('input[name="period-kind"]:checked') || { value: 'all' }).value;
      const body = { kind };
      if (kind === 'last') body.n = Number(n.value);
      if (kind === 'days') body.days = Number(days.value);
      if (kind === 'range') Object.assign(body, { from: from.value || null, to: to.value || null });
      apply.disabled = true;
      setPeriod(owner, body).catch((err) => {
        error.textContent = err.message || String(err);
        error.hidden = false;
        apply.disabled = false;
      });
    });
    pop.dataset.owner = owner.api;
    pop.hidden = false;
    $('period-btn').setAttribute('aria-expanded', 'true');
    (pop.querySelector('input[name="period-kind"]:checked') || pop.querySelector('input')).focus();
  }

  // Aucune main sur la période choisie (il y en a ailleurs) : revenir à toutes, ou en choisir une autre.
  function showEmptyPeriod() {
    const owner = periodOwner();
    showPanel(el('div', { class: 'welcome' }, el('h2', {}, 'Aucune main sur cette période'),
      el('p', {}, 'Période choisie : ' + owner.period.label.toLowerCase() + '. Les mains importées sont ailleurs dans le temps.'),
      el('button', { type: 'button', class: 'primary', onclick: (e) => {
        e.target.disabled = true;
        setPeriod(owner, { kind: 'all' }).catch((err) => { e.target.disabled = false; window.alert(err.message || String(err)); });
      } }, 'Revenir à toutes les mains'), ' ',
      el('button', { type: 'button', class: 'link', onclick: (e) => { e.stopPropagation(); openPeriod(owner); } },
        'Choisir une autre période')));
  }

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

  function showImport(student) {
    const input = el('input', { type: 'file', multiple: true, accept: '.txt,.log,.hh,.zip', hidden: true });
    const result = el('div', { class: 'result', 'aria-live': 'polite' });
    const drop = el('div', { class: 'drop' },
      el('div', { class: 'drop-title' }, 'Dépose tes historiques ici'),
      el('div', { class: 'muted' }, 'ou'),
      el('button', { type: 'button', class: 'primary', onclick: () => input.click() }, 'Choisir des fichiers'),
      el('div', { class: 'muted small' }, 'Betclic, Winamax, Unibet (.txt) : plusieurs fichiers à la fois, un dossier, '
        + 'ou une archive .zip avec ses dossiers'));
    input.addEventListener('change', () => upload(input.files, result, student));
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      drop.classList.remove('over');
      droppedFiles(e.dataTransfer).then((files) => upload(files, result, student));
    });
    const who = student && students.find((s) => s.id === student);
    // Les historiques importés vont dans la base ; le dossier reste une boîte d'arrivée (lue au lancement).
    const folder = who
      ? el('p', { class: 'muted small' }, 'Les mains de ' + who.name + ' sont gardées dans la base de Merlin. '
        + 'Boîte d\'arrivée : ' + who.folder)
      : el('p', { class: 'muted small' }, 'Tes mains sont gardées dans la base de Merlin. Boîte d\'arrivée : '
        + state.folder + ' · ',
        el('button', { type: 'button', class: 'link', onclick: () => reload(result) }, 'Relire le dossier'),
        ' (si tu y as déposé des historiques à la main)');
    const imported = el('div', { class: 'imported' });
    showPanel(el('div', { class: 'import' }, drop, input, result, folder, imported));
    showFiles(imported, student);
  }

  // Les historiques importés : retirer les mains de l'un d'eux (il reste gardé, pour le rétablir).
  const fileApi = (student) => (student ? '/api/eleves/' + encodeURIComponent(student) : '/api') + '/fichiers';
  const day = (iso) => (iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4) : '');

  async function showFiles(box, student) {
    let files;
    try {
      const res = await fetch(fileApi(student));
      files = res.ok ? await res.json() : [];
    } catch (e) { files = []; }
    box.textContent = '';
    if (!files.length) return;
    const action = async (button, verb, f) => {
      if (verb === 'retirer' && !window.confirm('Retirer les ' + f.hands + ' main(s) de « ' + f.name + ' » ? Elles ne '
        + 'compteront plus dans les analyses. L\'historique reste gardé : tu pourras le rétablir.')) return;
      button.disabled = true;
      try {
        const res = await fetch(fileApi(student) + '/' + verb, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: f.id }) });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Action impossible.');
        frame.dataset.src = '';
        if (student) { await loadStudents(); renderHeader(); } else { state = data.state; renderSidebar(); }
        showFiles(box, student);
      } catch (err) {
        button.disabled = false;
        window.alert(err.message || String(err));
      }
    };
    const rows = files.map((f) => {
      const formats = Object.entries(f.formats || {}).map(([k, n]) => n + ' ' + k).join(' · ');
      const period = f.first ? (day(f.first) === day(f.last) ? day(f.first) : day(f.first) + ' → ' + day(f.last)) : '';
      const button = el('button', { type: 'button', class: 'link' }, f.removed ? 'Rétablir' : 'Retirer');
      button.addEventListener('click', () => action(button, f.removed ? 'retablir' : 'retirer', f));
      return el('tr', { class: f.removed ? 'removed' : null },
        el('td', {}, f.name, f.inbox ? el('span', { class: 'muted small' }, ' · dossier des mains') : ''),
        el('td', {}, (f.sites || []).join(', ')),
        el('td', { class: 'num' }, f.removed ? '–' : String(f.hands)),
        el('td', {}, f.removed ? 'retiré le ' + day(f.removed) : formats),
        el('td', { class: 'nowrap' }, period),
        el('td', { class: 'muted nowrap' }, day(f.imported)),
        el('td', {}, button));
    });
    box.append(el('h3', {}, 'Historiques importés (' + files.length + ')'),
      el('div', { class: 'scroll' }, el('table', {},
        el('thead', {}, el('tr', {}, el('th', {}, 'Fichier'), el('th', {}, 'Site'), el('th', { class: 'num' }, 'Mains'),
          el('th', {}, 'Tables'), el('th', {}, 'Joué'), el('th', {}, 'Importé le'), el('th', {}, ''))),
        el('tbody', {}, rows))),
      el('p', { class: 'muted small' }, 'Retirer un historique enlève ses mains de toutes les analyses (celles qu\'un '
        + 'autre historique contient aussi restent) ; il reste gardé, et le dossier des mains ne le réimporte pas. '
        + '« Rétablir », ou l\'importer à nouveau, le remet.'));
  }

  const HISTORY = /\.(txt|log|hh|zip)$/i;

  // Fichiers déposés ; un dossier déposé est parcouru avec ses sous-dossiers (historiques et archives seulement).
  async function droppedFiles(dt) {
    const entries = [...(dt.items || [])].map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean);
    if (!entries.some((e) => e.isDirectory)) return [...dt.files];
    const out = [];
    const walk = async (entry) => {
      if (entry.isFile) {
        if (HISTORY.test(entry.name)) out.push(await new Promise((ok, ko) => entry.file(ok, ko)));
        return;
      }
      const reader = entry.createReader();
      for (;;) {  // readEntries rend les fichiers par paquets
        const batch = await new Promise((ok, ko) => reader.readEntries(ok, ko));
        if (!batch.length) break;
        for (const child of batch) await walk(child);
      }
    };
    for (const entry of entries) await walk(entry);
    return out;
  }

  // Un historique part en texte ; une archive zip en base64 (le serveur l'ouvre, dossiers compris).
  function readUpload(f) {
    if (!/\.zip$/i.test(f.name)) return f.text().then((content) => ({ name: f.name, content }));
    return new Promise((ok, ko) => {
      const reader = new FileReader();
      reader.onload = () => ok({ name: f.name, zip: String(reader.result).split(',')[1] || '' });
      reader.onerror = () => ko(reader.error);
      reader.readAsDataURL(f);
    });
  }

  async function upload(fileList, result, student) {
    if (!fileList || !fileList.length) {
      result.textContent = 'Aucun historique (.txt) ni archive .zip dans ce que tu as déposé.';
      return;
    }
    result.textContent = 'Import en cours…';
    try {
      const files = await Promise.all([...fileList].map(readUpload));
      const url = student ? '/api/eleves/' + encodeURIComponent(student) + '/import' : '/api/import';
      const res = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ files }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Import impossible.');
      frame.dataset.src = '';
      if (student) {
        await loadStudents();
        renderHeader();
      } else {
        state = data.state;
        renderSidebar();
      }
      showImportResult(data, result, student);
      const box = document.querySelector('.import .imported');
      if (box) showFiles(box, student);
    } catch (err) {
      result.textContent = '';
      result.append(el('p', { class: 'error' }, err.message || String(err)));
    }
  }

  function showImportResult(data, result, student) {
    result.textContent = '';
    const next = student ? el('a', { href: '#/eleve/' + encodeURIComponent(student) + '/leaks' }, 'Voir son leakfinding')
      : el('a', { href: '#/moi' }, 'Voir mon jeu');
    result.append(el('p', {}, data.added
      ? el('b', {}, data.added + ' nouvelle(s) main(s) ajoutée(s).')
      : 'Aucune nouvelle main.', ' ',
    data.added ? next : ''));
    const rows = data.files.map((f) => el('tr', { class: f.archive ? 'archive' : null },
      el('td', {}, f.name),
      el('td', {}, (f.sites || []).join(', ')),
      el('td', { class: f.status === 'importé' ? 'ok' : f.status === 'déjà importé' || f.archive ? '' : 'ko' }, f.status),
      el('td', { class: 'num' }, String(f.hands)),
      el('td', {}, Object.entries(f.formats || {}).map(([k, n]) => n + ' ' + k).join(' · ')),
      el('td', { class: 'num' }, String(f.new))));
    const table = el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'Fichier'), el('th', {}, 'Site'), el('th', {}, 'Statut'), el('th', { class: 'num' }, 'Mains'),
        el('th', {}, 'Tables'), el('th', { class: 'num' }, 'Nouvelles'))),
      el('tbody', {}, rows));
    const singles = data.files.filter((f) => !f.archive);
    if (singles.length > 20) {  // une archive ou un dossier : le bilan, puis le détail sur demande
      const count = {};
      singles.forEach((f) => { count[f.status] = (count[f.status] || 0) + 1; });
      const plural = { 'importé': 'importés', 'déjà importé': 'déjà importés', vide: 'vides', 'format non reconnu': 'au format non reconnu' };
      result.append(el('p', {}, singles.length + ' fichiers : '
        + Object.entries(count).map(([k, n]) => n + ' ' + (n > 1 && plural[k] || k)).join(', ') + '.'));
      result.append(el('details', {}, el('summary', {}, 'Détail par fichier'), table));
    } else result.append(table);
    if (data.files.some((f) => f.formats && Object.keys(f.formats).some((k) => k !== 'HU'))) {
      result.append(el('p', { class: 'small' }, 'Les mains heads-up vont dans toute l\'analyse (adversaires, solveur, '
        + 'leakfinding) ; celles des tables à 3 joueurs et plus ont leur leakfinding (6-max, 3-max), leurs mains de '
        + 'départ et leurs stats par position.'));
    }
  }

  async function reload(result) {
    result.textContent = 'Rechargement…';
    const res = await fetch('/api/reload', { method: 'POST' });
    state = await res.json();
    frame.dataset.src = '';
    renderSidebar();
    result.textContent = state.hands + ' mains chargées.';
  }

  // ---------- élèves ----------
  function showStudents() {
    const nameInput = el('input', { type: 'text', maxlength: '60', placeholder: 'Nom de l\'élève', 'aria-label': 'Nom de l\'élève' });
    const pseudoInput = el('input', { type: 'text', maxlength: '60', placeholder: 'Son pseudo à la table (facultatif)',
      'aria-label': 'Pseudo à la table' });
    const message = el('p', { class: 'small', 'aria-live': 'polite' });
    const form = el('form', { class: 'student-form', onsubmit: async (e) => {
      e.preventDefault();
      const name = nameInput.value.trim();
      if (!name) return;
      const res = await fetch('/api/eleves', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, pseudo: pseudoInput.value.trim() || null }) });
      const data = await res.json();
      if (!res.ok) { message.textContent = data.error || 'Ajout impossible.'; return; }
      location.hash = '#/eleve/' + encodeURIComponent(data.id) + '/importer';
    } }, nameInput, pseudoInput, el('button', { type: 'submit', class: 'primary' }, 'Ajouter un élève'));
    const cards = students.map((s) => el('a', { class: 'student', href: '#/eleve/' + encodeURIComponent(s.id) + '/leaks' },
      el('b', {}, s.name),
      el('span', { class: 'muted small' }, (s.hands ? s.hands + ' mains · ' + (s.hero || '') + ' · dernière le ' + s.last
        + (s.ring_hands ? ' · ' + s.ring_hands + ' aux tables à plusieurs' : '')
        : s.ring_hands ? s.ring_hands + ' mains aux tables à plusieurs'
          : s.period && (s.period.all_hands || s.period.all_ring) ? 'aucune main sur la période' : 'pas encore de mains')
        + (s.period && s.period.kind !== 'all' ? ' · période : ' + s.period.label.toLowerCase() : ''))));
    showPanel(el('div', { class: 'students' },
      el('p', {}, 'Chaque élève a son dossier de mains et son rapport de leakfinding : ses stats face à la théorie, '
        + 'contre les réguliers et contre les récréatifs, les mains à revoir avec l\'avis du solveur, et les leaks à travailler.'),
      cards.length ? el('div', { class: 'student-list' }, cards) : el('p', { class: 'muted' }, 'Aucun élève pour l\'instant.'),
      el('h3', {}, 'Nouvel élève'), form, message));
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
      placeholder: 'C:\\Users\\toi\\OneDrive\\Merlin   ou   gdrive:Merlin' });
    const studies = el('input', { type: 'checkbox', checked: v.studies, disabled: busy });
    const auto = el('input', { type: 'checkbox', checked: v.auto, disabled: busy });
    const status = el('div', { 'aria-live': 'polite' });
    const settings = () => ({ dest: dest.value, studies: studies.checked, auto: auto.checked });
    const run = async (path) => {
      try {
        await backupApi('/reglages', settings());
        if (path === '/restaurer' && !window.confirm('Restaurer la dernière sauvegarde de ' + dest.value + ' ? '
          + 'Elle est fusionnée avec ce qui est sur cet ordinateur : rien n\'est effacé ni remplacé par plus ancien.')) return showBackup();
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
      status.append(el('p', {}, (v.result.restored ? 'Restauré depuis ' : 'Déjà à jour avec ') + v.result.archive
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
          + 'sur ton ordinateur, par exemple ', el('code', {}, 'C:\\Users\\toi\\OneDrive\\Merlin'),
        '. Leur application envoie la sauvegarde en ligne toute seule.'),
        el('p', {}, el('b', {}, 'Un stockage en ligne avec rclone'), ' (Google Drive, OneDrive, Dropbox, un serveur SFTP, S3…) : '
          + 'installe rclone (rclone.org), configure ton stockage avec ', el('code', {}, 'rclone config'),
        ', puis indique-le sous la forme ', el('code', {}, 'nom:dossier'), ' (', v.rclone ? 'rclone est installé' : 'rclone n\'est pas installé', ').'),
        el('p', {}, 'Ce qui est gardé : les tailles de mise choisies (les plus longues à recalculer), les résolutions, '
          + 'les mains analysées, ton journal d\'entraînement et, en option, les études. Les 10 dernières archives sont '
          + 'gardées. Sur un autre ordinateur : même destination, puis « Restaurer ».'),
        el('p', { class: 'muted small' }, 'Dossier de travail de Merlin : ' + v.home
          + ' (variable ANALYZER_HOME pour en changer).'))));
    if (v.running) backupTimer = setTimeout(() => { if (route.view === 'sauvegarde') showBackup(); }, 1500);
  }

  // ---------- événements ----------
  frame.addEventListener('load', () => { $('loading').hidden = true; });

  // Une page du cadre a suivi un lien interne (ex. « voir les mains ») : on met l'onglet à jour.
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin || !e.data) return;
    if (e.data.type === 'analyzer-refresh') {  // une page a changé les noms (alias) ou les Paramètres : le menu suit
      loadState().then(() => { renderHeader(); renderTabs(); markActive(); }).catch(() => {});
      return;
    }
    if (e.data.type !== 'analyzer-page') return;
    const parts = String(e.data.path).split('/').filter(Boolean).map(decodeURIComponent);
    if (FORMAT_TABS.includes(parts[parts.length - 1]) && (parts[0] === 'moi' || parts[0] === 'eleve')) {
      // le format choisi (bilan, Leakfinding, préflop) : gardé pour la prochaine fois
      const fmt = new URLSearchParams(e.data.search || '').get('format');
      const space = '/' + parts.slice(0, -1).map(encodeURIComponent).join('/');
      pref('format:' + space, fmt && fmt !== 'HU' ? fmt : null);
    }
    let r = null;
    if (parts[0] === 'p' && parts.length === 3) r = { view: 'adv', player: parts[1], tab: parts[2] };
    else if (parts[0] === 'moi' && parts.length === 2) r = parts[1] === 'bluffs' ? { view: 'field', tab: 'bluffs' } : { view: 'moi', tab: parts[1] };
    else if (parts[0] === 'field' && parts.length === 2) r = { view: 'field', tab: parts[1] };
    else if (parts[0] === 'field' && parts.length === 3 && parts[1] === 'joueur') r = { view: 'field', tab: 'joueur', player: parts[2] };
    else if (parts[0] === 'field' && parts.length === 3 && parts[1] === 'groupe') r = { view: 'field', tab: 'groupe', group: parts[2] };
    else if (parts[0] === 'parametres' && parts.length === 2) r = { view: 'parametres', tab: parts[1] };
    else if (parts[0] === 'etudes' && parts.length <= 2) r = { view: 'etudes', tab: !parts[1] || HU_SERIES.includes(parts[1]) ? 'hu' : parts[1] };
    else if (parts[0] === 'entraineur' && parts.length === 1) r = { view: 'entraineur' };
    else if (parts[0] === 'eleve' && parts.length === 3) r = { view: 'eleve', student: parts[1], tab: parts[2] };
    if (!r) return;
    if (r.view === 'field') {  // le format de la page : gardé pour les pages suivantes du field
      const fmt = new URLSearchParams(e.data.search || '').get('format');
      if (fmt && ['regs', 'recs'].includes(r.tab)) pref('format:/field', fmt === 'HU' ? 'HU' : 'ring');
      if (fmt && (r.tab === 'joueur' || r.tab === 'groupe')) r.fmt = fmt === 'HU' ? 'HU' : 'ring';
    }
    frame.dataset.src = srcFor(r);
    if (route && route.view === r.view && route.player === r.player && route.tab === r.tab && route.group === r.group
      && (route.fmt || null) === (r.fmt || null)) return;
    route = r;
    history.replaceState(null, '', hashFor(r));
    renderHeader();
    renderTabs();
    markActive();
  });

  $('opp-search').addEventListener('input', renderSidebar);
  $('opp-fmt').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { pref('opp-fmt', b.dataset.fmt); renderSidebar(); }));
  [['opp-sort', 'hands'], ['opp-kind', '']].forEach(([id, fallback]) => {  // tri et type gardés d'une visite à l'autre
    const select = $(id), saved = pref(id);
    select.value = saved !== null && select.querySelector('option[value="' + saved + '"]') ? saved : fallback;
    select.addEventListener('change', () => { pref(id, select.value); renderSidebar(); });
  });
  $('menu').addEventListener('click', () => $('app').classList.toggle('drawer-open'));
  document.addEventListener('click', (e) => { if (!$('period').contains(e.target)) closePeriod(); });  // hors du panneau
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('period-pop').hidden) { closePeriod(); $('period-btn').focus(); }
  });
  $('backdrop').addEventListener('click', closeDrawer);
  window.addEventListener('hashchange', render);

  loadState().then(render).catch((err) => {
    showPanel(el('p', { class: 'error' }, 'Impossible de joindre le serveur de Merlin : ' + err.message));
  });
})();
