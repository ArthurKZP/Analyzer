(function () {
  'use strict';

  // Entraîneur : une main tirée dans la range du solveur, jouée face à la stratégie du solveur (session de
  // l'étude ouverte, mêmes nœuds que l'explorateur). Le serveur tient le journal des décisions.
  const $ = (id) => document.getElementById(id);
  const SUITS = { s: '♠', h: '♥', d: '♦', c: '♣' };
  const POS = ['BB', 'BTN'];
  const STREET = ['Flop', 'Turn', 'River'];
  const SHADES = ['var(--g-bet1)', 'var(--g-bet2)', 'var(--g-bet3)', 'var(--g-bet4)'];
  const ERROR = 0.25;   // bb : en dessous, l'écart d'EV n'est pas une erreur (comme « Face au solveur »)
  const MIXED = 0.10;   // une action jouée au moins 10 % du temps avec la main ne coûte rien
  const MAIN = 0.50;
  const FAMILY_OF = { SRP: 'srp', 'pot 3bet': '3bet', 'pot 4bet': '4bet' };
  const STORE = 'analyzer-entraineur';
  const MAX_NODES = 400;

  let data = null;       // /api/entraineur : spots résolus, situations, progrès
  let cfg = null;        // réglages
  let fixed = null;      // départ imposé par l'adresse (explorateur) : { ident, path }
  let ident = null;      // spot dont l'étude est ouverte
  let family = 'srp';
  const nodes = new Map();
  let hand = null;       // main en cours
  let flopHands = 0;     // mains jouées sur le flop actuel
  let resume = null;     // attente d'un clic (action du joueur, « Continuer », « Main suivante »)
  const session = { hands: 0, decisions: 0, errors: 0, lost: 0 };

  // ---------- utilitaires ----------
  function el(tag, attrs, ...children) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== '') n.append(c instanceof Node ? c : String(c));
    return n;
  }
  const num = (x, d = 1) => ((Math.round(x * 10 ** d) / 10 ** d) || 0).toLocaleString('fr-FR', { maximumFractionDigits: d });
  const pct = (x) => Math.round(100 * x) + ' %';
  const card = (c) => el('span', { class: 'pc s' + c[1] }, c[0] + SUITS[c[1]]);
  const cardText = (c) => c[0] + SUITS[c[1]];
  const split = (combo) => [combo.slice(0, 2), combo.slice(2, 4)];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function sample(weights) {
    const total = weights.reduce((a, b) => a + Math.max(0, b || 0), 0);
    if (!(total > 0)) return -1;
    let x = Math.random() * total;
    for (let i = 0; i < weights.length; i++) {
      x -= Math.max(0, weights[i] || 0);
      if (x < 0) return i;
    }
    return weights.length - 1;
  }

  function actLabels(actions, pot, put, player) {
    const call = actions.find((x) => x.kind === 'call');
    const base = call && put && player !== null && player !== undefined ? 2 * put[1 - player] : 0;
    return actions.map((a) => {
      if ((a.kind === 'bet' || a.kind === 'raise') && a.allin) return 'Tapis ' + num(a.amount);
      if (a.kind === 'bet') return 'Mise ' + num(a.amount) + ' (' + Math.round(100 * a.amount / pot) + ' %)';
      if (a.kind === 'raise') return 'Relance ' + num(a.amount) + (base ? ' (' + Math.round(100 * (a.amount - call.amount) / base) + ' %)' : '');
      return { check: 'Check', call: 'Call', fold: 'Fold' }[a.kind] || a.kind;
    });
  }
  const labelsOf = (n) => actLabels(n.actions, n.pot, n.put, n.player);

  function colors(actions) {
    const sized = actions.map((a, k) => k).filter((k) => (actions[k].kind === 'bet' || actions[k].kind === 'raise') && !actions[k].allin);
    return actions.map((a, k) => {
      if (a.kind === 'fold') return 'var(--g-fold)';
      if (a.kind === 'check' || a.kind === 'call') return 'var(--g-pass)';
      if (a.allin) return 'var(--g-allin)';
      const rank = sized.indexOf(k);
      return SHADES[sized.length === 1 ? 1 : Math.round(rank * 3 / (sized.length - 1))];
    });
  }

  async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const reply = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(reply.error || 'Erreur ' + res.status);
      err.status = res.status;
      throw err;
    }
    return reply;
  }

  function status(text, isError) {
    const box = $('status');
    box.textContent = '';
    if (text) box.append(el('span', { class: isError ? 'err' : '' }, text));
  }

  // ---------- réglages ----------
  function defaults() {
    return { family: 'srp', flop: '*', per: '10', side: 'both', start: 'root', single: false, auto: true };
  }
  function loadCfg() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(STORE) || '{}') || {}; } catch (e) { saved = {}; }
    return Object.assign(defaults(), saved);
  }
  function saveCfg() {
    try { localStorage.setItem(STORE, JSON.stringify(cfg)); } catch (e) { /* réglages non retenus */ }
  }

  // ---------- étude et nœuds ----------
  async function open(id) {
    if (ident !== id) { nodes.clear(); ident = id; }
    let st = await api('/api/explorateur/etat', { hand: id });
    family = id.startsWith('spot:') ? id.split(':')[1] : FAMILY_OF[st.result && st.result.pot_type] || 'srp';
    if (st.live) return;
    if (!['waiting', 'running'].includes(st.state)) {
      if (!st.study && !id.startsWith('spot:')) throw new Error('Ce coup n\'a pas d\'étude enregistrée : recalcule-le dans l\'explorateur.');
      if (!st.study) throw new Error('Ce spot n\'est pas résolu : lance-le depuis « Études du solveur ».');
      st = await api('/api/resoudre', { hand: id, start: true, force: true });
    }
    const t0 = Date.now();
    while (['waiting', 'running'].includes(st.state)) {
      const p = st.progress || {};
      status(st.mode === 'load' || st.study ? 'Ouverture de l\'étude ' + spotName(id) + '… ' + Math.round((Date.now() - t0) / 1000) + ' s'
        : 'Résolution en cours' + (p.iteration ? ' : itération ' + p.iteration : '') + '…');
      await sleep(800);
      st = await api('/api/resoudre/' + encodeURIComponent(st.job));
    }
    if (st.state !== 'done') throw new Error(st.error || 'Ouverture interrompue.');
    status('');
  }

  async function node(path, retry = true) {
    const key = JSON.stringify(path);
    if (nodes.has(key)) return nodes.get(key);
    try {
      const reply = await api('/api/explorateur/noeud', { hand: ident, path });
      if (nodes.size >= MAX_NODES) nodes.delete(nodes.keys().next().value);
      nodes.set(key, reply.node);
      return reply.node;
    } catch (e) {
      // Session fermée (inactivité, ou l'explorateur a ouvert une autre étude) : on la rouvre une fois.
      if (e.status === 409 && retry) {
        await open(ident);
        return node(path, false);
      }
      throw e;
    }
  }

  function rowOf(n, p, combo) {
    const [a, b] = split(combo);
    return n.hands[p].find((r) => (r[0].slice(0, 2) === a && r[0].slice(2) === b) || (r[0].slice(0, 2) === b && r[0].slice(2) === a)) || null;
  }

  // Stratégie de toute la range du joueur qui agit.
  function rangeStrategy(n) {
    const na = n.actions.length, out = new Array(na).fill(0);
    let total = 0;
    for (const r of n.hands[n.player]) {
      total += r[1];
      for (let a = 0; a < na; a++) out[a] += r[1] * r[4 + a];
    }
    return out.map((x) => (total > 0 ? x / total : 1 / na));
  }

  // Situation d'un nœud, comme review.situation : « bet:ti:i », « face:fo::1 »…
  function situationOf(n) {
    let past = '', agg = null, level = 0;
    for (const s of n.history.slice(0, -1)) {
      if (s.kind === 'card') { past += agg === null ? 'x' : agg === 0 ? 'o' : 'i'; agg = null; level = 0; }
      else if (s.kind === 'action' && s.chosen !== null && s.chosen !== undefined) {
        const k = s.actions[s.chosen].kind;
        if (k === 'bet' || k === 'raise') { agg = s.player; level += 1; }
      }
    }
    const where = 'ftr'[past.length] + (n.player === 0 ? 'o' : 'i');
    return { street: past.length, past, agg, level, key: level ? 'face:' + where + ':' + past + ':' + level : 'bet:' + where + ':' + past };
  }

  function parseKey(key) {
    const [kind, where, past, level] = key.split(':');
    return { kind: 'situation', key, street: 'ftr'.indexOf(where[0]), player: where[1] === 'o' ? 0 : 1, past, level: kind === 'face' ? Number(level) : 0 };
  }

  // ---------- tirage d'une ligne jusqu'au départ ----------
  // Une action qui rend le départ inatteignable est écartée (fold, street close avec la mauvaise lettre…) ;
  // les autres gardent leur fréquence dans la range du solveur.
  function allowed(n, s, a, target) {
    if (a.kind === 'fold') return false;
    const aggressive = a.kind === 'bet' || a.kind === 'raise';
    const closes = a.kind === 'call' || (a.kind === 'check' && n.player === 1);
    if (target.kind === 'street') {
      return !(a.allin && s.street < target.street);
    }
    if (a.allin && (s.street < target.street || s.level + 1 < target.level)) return false;
    if (s.street < target.street) {
      const want = target.past[s.street];
      if (closes) return (a.kind === 'call' ? (s.agg === 0 ? 'o' : 'i') : 'x') === want;
      if (aggressive) return want !== 'x';
      return true;
    }
    if (s.street === target.street) {
      if (closes) return false;
      if (aggressive) return s.level + 1 <= target.level;
      return true;
    }
    return false;
  }

  function reached(n, s, target, hero) {
    if (target.kind === 'street') return s.street === target.street && n.player === hero;
    return n.player === target.player && s.street === target.street && s.past === target.past && s.level === target.level;
  }

  async function walk(target, hero) {
    for (let attempt = 0; attempt < 25; attempt++) {
      let path = [];
      for (let guard = 0; guard < 40; guard++) {
        const n = await node(path);
        if (n.type === 'chance') {
          path = path.concat([{ type: 'card', card: n.cards[Math.floor(Math.random() * n.cards.length)] }]);
          continue;
        }
        if (n.type !== 'action') break;
        const s = situationOf(n);
        if (reached(n, s, target, hero)) return path;
        const strat = rangeStrategy(n);
        let weights = n.actions.map((a, i) => (allowed(n, s, a, target) ? strat[i] : 0));
        if (!weights.some((w) => w > 0)) weights = n.actions.map((a) => (allowed(n, s, a, target) ? 1 : 0));
        const i = sample(weights);
        if (i < 0) break;
        path = path.concat([{ type: 'action', index: i }]);
      }
    }
    return null;
  }

  // Les deux mains, tirées ensemble dans les ranges du nœud (cartes communes exclues).
  function deal(n) {
    const A = n.hands[0], B = n.hands[1];
    const key = (c) => split(c).sort().join('');
    const perCard = {}, exact = new Map();
    let total = 0;
    for (const r of B) {
      total += r[1];
      for (const c of split(r[0])) perCard[c] = (perCard[c] || 0) + r[1];
      exact.set(key(r[0]), r[1]);
    }
    const weights = A.map((r) => {
      const [c1, c2] = split(r[0]);
      return r[1] * Math.max(0, total - (perCard[c1] || 0) - (perCard[c2] || 0) + (exact.get(key(r[0])) || 0));
    });
    const i = sample(weights);
    if (i < 0) return null;
    const a = A[i][0], used = split(a);
    const j = sample(B.map((r) => (split(r[0]).some((c) => used.includes(c)) ? 0 : r[1])));
    return j < 0 ? null : [a, B[j][0]];
  }

  // ---------- choix du spot ----------
  function spotName(id) {
    const parts = id.split(':');
    if (parts[0] !== 'spot') return 'main ' + id.slice(-6);
    return (data && data.families[parts[1]] ? data.families[parts[1]].name : parts[1]) + ' ' + parts[2].match(/../g).map(cardText).join('');
  }

  function candidates() {
    const spots = data.families[cfg.family].spots;
    if (cfg.flop === '*') return spots.map((s) => s.id);
    if (cfg.flop.startsWith('tex:')) return spots.filter((s) => s.texture === cfg.flop.slice(4)).map((s) => s.id);
    return spots.some((s) => s.id === cfg.flop) ? [cfg.flop] : [];
  }

  function nextSpot() {
    if (fixed) return fixed.ident;
    const list = candidates();
    if (!list.length) return null;
    if (ident && list.includes(ident) && flopHands < Number(cfg.per)) return ident;
    flopHands = 0;
    const others = list.length > 1 ? list.filter((x) => x !== ident) : list;
    return others[Math.floor(Math.random() * others.length)];
  }

  function targetOf() {
    if (fixed) return { kind: 'path' };
    if (cfg.start === 'turn') return { kind: 'street', street: 1 };
    if (cfg.start === 'river') return { kind: 'street', street: 2 };
    if (cfg.start.startsWith('sit:')) return parseKey(cfg.start.slice(4));
    return { kind: 'root' };
  }

  // ---------- une main ----------
  async function newHand() {
    resume = null;
    const id = nextSpot();
    if (!id) { status('Aucun flop résolu pour ces réglages.', true); return showSetup(); }
    try {
      await open(id);
      const target = targetOf();
      let hero = target.kind === 'situation' ? target.player
        : cfg.side === 'BB' ? 0 : cfg.side === 'BTN' ? 1 : Math.random() < 0.5 ? 0 : 1;
      let path = [];
      if (target.kind === 'path') {
        path = fixed.path.slice();
        let at = await node(path);
        while (at.type === 'action' && at.actions.length === 1) {  // check forcé : la vraie décision suit
          path.push({ type: 'action', index: 0 });
          at = await node(path);
        }
        if (at.type !== 'action') throw new Error('Ce moment du coup n\'est pas une décision.');
        hero = at.player;
      } else if (target.kind !== 'root') {
        status('Tirage d\'une ligne…');
        path = await walk(target, hero);
        if (!path) throw new Error('Cette situation n\'arrive presque jamais sur ce flop : choisis-en une autre ou un autre flop.');
      }
      const start = await node(path);
      const combos = deal(start);
      if (!combos) throw new Error('Aucune main possible à ce moment du coup.');
      status('');
      flopHands += 1;
      hand = { ident: id, family, hero, combos, path: path.slice(), start: path.length, decisions: [], node: start,
        holdings: {}, done: false, result: null, feedback: null };
      showPlay();
      await play();
    } catch (e) {
      status(e.message, true);
      if (!hand || hand.done || hand.ident !== id) showSetup();
    }
  }

  async function play() {
    const h = hand;
    for (let guard = 0; guard < 60 && h === hand; guard++) {
      const n = await node(h.path);
      h.node = n;
      if (n.type === 'chance') {
        const used = h.combos.flatMap(split);
        const deck = n.cards.filter((c) => !used.includes(c));
        h.path.push({ type: 'card', card: deck[Math.floor(Math.random() * deck.length)] });
        continue;
      }
      if (n.type !== 'action') return finish(n);
      if (n.actions.length === 1) { h.path.push({ type: 'action', index: 0 }); continue; }
      if (n.player !== h.hero) {
        renderTable();
        renderActions(null);
        await sleep(h.path.length > h.start ? 450 : 0);
        const row = rowOf(n, n.player, h.combos[n.player]);
        const strat = row ? row.slice(4, 4 + n.actions.length) : rangeStrategy(n);
        h.path.push({ type: 'action', index: Math.max(0, sample(strat)) });
        continue;
      }
      await holding(n.board);
      renderTable();
      const choice = await new Promise((resolve) => { resume = resolve; renderActions(n); });
      if (h !== hand) return;
      const fb = judge(n, choice);
      h.decisions.push(fb);
      h.feedback = fb;
      session.decisions += 1;
      session.lost += fb.loss;
      if (fb.loss >= ERROR) session.errors += 1;
      h.path.push({ type: 'action', index: choice });
      renderFeedback();
      renderSession();
      if (cfg.single && !fixed) return finish(null);
      if (fb.loss >= ERROR || !cfg.auto) {
        renderActions(null, true);
        await new Promise((resolve) => { resume = resolve; });
        if (h !== hand) return;
      }
    }
  }

  function judge(n, choice) {
    const na = n.actions.length;
    const row = rowOf(n, hand.hero, hand.combos[hand.hero]);
    const strat = row ? row.slice(4, 4 + na) : rangeStrategy(n);
    const evs = row ? row.slice(4 + na, 4 + 2 * na) : new Array(na).fill(null);
    const known = evs.filter((e) => e !== null && e !== undefined);
    const best = known.length ? Math.max(...known) : null;
    const evLoss = best !== null && evs[choice] !== null && evs[choice] !== undefined ? Math.max(0, best - evs[choice]) : 0;
    const freq = strat[choice];
    const loss = freq >= MIXED ? 0 : evLoss;
    const verdict = freq >= MAIN ? 'parfait' : freq >= MIXED ? 'mixte' : loss < ERROR ? 'imprecis' : 'erreur';
    const s = situationOf(n);
    const labels = labelsOf(n);
    let top = 0;
    strat.forEach((f, i) => { if (f > strat[top]) top = i; });
    return { key: s.key, street: s.street, labels, colors: colors(n.actions), strat, evs, range: rangeStrategy(n), choice,
      best: top, loss, evLoss, freq, verdict, pot: n.pot, path: hand.path.slice(), board: n.board };
  }

  async function finish(n) {
    const h = hand;
    h.done = true;
    if (n) {
      const winner = n.type === 'terminal_fold' ? 1 - n.history[n.history.length - 2].player : null;
      let info = { winner };
      if (n.type !== 'terminal_fold') {
        info = await api('/api/entraineur/mains', { board: n.board, holes: h.combos.map(split) }).catch(() => ({ winner: null }));
      }
      const put = n.put[h.hero], pot = n.pot;
      const net = info.winner === null || info.winner === undefined ? pot / 2 - put : info.winner === h.hero ? pot - put : -put;
      h.result = { fold: n.type === 'terminal_fold', winner: info.winner, holdings: info.holdings, net, pot };
    }
    session.hands += 1;
    renderTable();
    renderEnd();
    renderSession();
    if (h.decisions.length) {
      const entries = h.decisions.map((d) => ({ family: h.family, spot: h.ident, key: d.key, combo: h.combos[h.hero],
        played: d.labels[d.choice], best: d.labels[d.best], loss: Math.round(d.loss * 1000) / 1000, freq: d.freq }));
      api('/api/entraineur/resultat', { entries }).then((r) => { data.progress = r.progress; }).catch(() => {});
    }
    await new Promise((resolve) => { resume = resolve; });
    if (h === hand) newHand();
  }

  async function holding(board) {
    const key = board.join('');
    if (hand.holdings[key]) return;
    try {
      const info = await api('/api/entraineur/mains', { board, holes: hand.combos.map(split) });
      hand.holdings[key] = info.holdings;
    } catch (e) { hand.holdings[key] = null; }
  }

  // ---------- rendu du jeu ----------
  function showPlay() {
    $('setup').hidden = true;
    $('play').hidden = false;
    $('b-setup').hidden = false;
    renderMeta();
    renderTable();
    $('feedback').textContent = '';
    renderSession();
  }

  function renderMeta() {
    const box = $('meta');
    box.textContent = '';
    if (!hand) return;
    const fam = data && data.families[hand.family];
    const target = targetOf();
    const start = fixed ? 'depuis l\'explorateur' : target.kind === 'situation'
      ? (fam && fam.situations[target.key]) || target.key : target.kind === 'street' ? 'dès la ' + STREET[target.street].toLowerCase() : 'tout le coup';
    box.append(spotName(hand.ident), ' · tu es ', el('b', {}, POS[hand.hero]), ' · ', start);
  }

  function renderTable() {
    const h = hand, n = h.node;
    renderMeta();
    const villain = 1 - h.hero;
    const board = n.board;
    const holdings = h.holdings[board.join('')] || (h.result && h.result.holdings) || null;
    const showVillain = h.done && h.result && !h.result.fold;
    const last = (p) => {
      for (let k = n.history.length - 2; k >= 0; k--) {
        const s = n.history[k];
        if (s.kind === 'card') return null;
        if (s.kind === 'action' && s.player === p && s.chosen !== null) return actLabels(s.actions, s.pot, s.put, s.player)[s.chosen];
      }
      return null;
    };
    const seat = (p, isHero) => {
      const hole = split(h.combos[p]);
      const shown = isHero || showVillain || (h.done && cfg.single);
      const lastAct = last(p);
      return [el('span', { class: 'who' + (isHero ? '' : ' v') }, POS[p] + ' · ' + (isHero ? 'Toi' : 'Lui')),
        el('span', {}, shown ? hole.map(card) : [el('span', { class: 'hidden-card' }), el('span', { class: 'hidden-card' })]),
        isHero && holdings ? el('span', { class: 'holding' }, holdings[p]) : '',
        !isHero && showVillain && holdings ? el('span', { class: 'holding' }, holdings[p]) : '',
        lastAct ? el('span', { class: 'last' }, lastAct) : '',
        el('span', { class: 'stk' }, 'tapis ' + num(n.stacks[p]) + ' bb')];
    };
    const sv = $('seat-v'), sh = $('seat-h');
    sv.textContent = ''; sh.textContent = '';
    sv.append(...seat(villain, false));
    sh.append(...seat(h.hero, true));
    $('board').textContent = '';
    $('board').append(...board.map(card));
    $('pot').textContent = 'Pot ' + num(n.pot) + ' bb';
    renderLine();
  }

  function renderLine() {
    const h = hand, n = h.node, box = $('line');
    box.textContent = '';
    const streets = [{ name: 'Flop', cards: n.board.slice(0, 3), acts: [] }];
    n.history.forEach((s, k) => {
      if (s.kind === 'card' && s.card) streets.push({ name: STREET[streets.length], cards: [s.card], acts: [] });
      else if (s.kind === 'action' && s.chosen !== null && s.chosen !== undefined) {
        const label = actLabels(s.actions, s.pot, s.put, s.player)[s.chosen];
        const mine = s.player === h.hero;
        const d = mine ? h.decisions.find((x) => x.path.length === k) : null;
        streets[streets.length - 1].acts.push(el('span', { class: 'a ' + (mine ? 'H' : 'V') + (k < h.start ? ' pre' : '') },
          POS[s.player] + ' ' + label.toLowerCase(),
          d ? el('span', { class: 'dot', style: 'background:' + verdictColor(d.verdict), title: VERDICT[d.verdict] }) : ''));
      }
    });
    for (const st of streets) {
      const parts = [];
      st.acts.forEach((a, i) => { if (i) parts.push(' · '); parts.push(a); });
      box.append(el('div', { class: 'st' }, el('b', {}, st.name), el('span', {}, st.cards.map(card)), ...parts));
    }
    if (h.start > 0) box.append(el('div', { class: 'note' }, 'En grisé : la ligne tirée selon les fréquences du solveur avant ta première décision.'));
  }

  function renderActions(n, waitContinue) {
    const box = $('actions');
    box.textContent = '';
    if (waitContinue) {
      box.append(el('button', { type: 'button', class: 'go', onclick: () => go() }, 'Continuer'),
        el('span', { class: 'wait' }, 'Espace ou Entrée'));
      return;
    }
    if (!n) { box.append(el('span', { class: 'wait' }, 'À lui…')); return; }
    const labels = labelsOf(n), cols = colors(n.actions);
    labels.forEach((label, i) => box.append(el('button', {
      type: 'button', class: 'act', style: 'background:' + cols[i], onclick: () => go(i), title: 'Touche ' + (i + 1),
    }, el('span', { class: 'k' }, String(i + 1)), label)));
  }

  function go(value) {
    const r = resume;
    if (!r) return;
    resume = null;
    const box = $('actions');  // plus de bouton actif tant que la suite se prépare
    box.textContent = '';
    box.append(el('span', { class: 'wait' }, '…'));
    r(value);
  }

  const VERDICT = { parfait: 'Parfait', mixte: 'Correct (le solveur mélange)', imprecis: 'Petite imprécision', erreur: 'Erreur' };
  const verdictColor = (v) => (v === 'erreur' ? 'var(--loss)' : v === 'imprecis' ? 'var(--g-bet1)' : 'var(--win)');

  function renderFeedback() {
    const d = hand.feedback, box = $('feedback');
    box.textContent = '';
    if (!d) return;
    const fam = data && data.families[hand.family];
    const label = (fam && fam.situations[d.key]) || d.key;
    box.className = 'card v-' + d.verdict;
    box.append(el('div', { class: 'verdict' }, el('h2', {}, VERDICT[d.verdict]),
      d.loss >= 0.005 ? el('span', { class: 'loss' }, '−' + num(d.loss, 2) + ' bb') : el('span', { class: 'muted small' }, 'aucune EV perdue')),
    el('div', { class: 'fb-sit' }, STREET[d.street] + ' · ' + label + ' · pot ' + num(d.pot) + ' bb'));
    const max = Math.max(...d.strat, 0.001);
    const rows = d.labels.map((l, i) => el('tr', { class: i === d.choice ? 'chosen' : '' },
      el('td', {}, el('span', { class: 'sw', style: 'background:' + d.colors[i] }), l),
      el('td', { class: 'n' }, el('span', { class: 'bar', style: 'width:' + Math.round(60 * d.strat[i] / max) + 'px;background:' + d.colors[i] })),
      el('td', { class: 'n' }, pct(d.strat[i])),
      el('td', { class: 'n' }, d.evs[i] === null || d.evs[i] === undefined ? '–' : num(d.evs[i], 2) + ' bb')));
    box.append(el('table', { class: 'fb' },
      el('thead', {}, el('tr', {}, el('td', { class: 'muted small' }, 'Avec ta main'), el('td', {}), el('td', { class: 'n muted small' }, 'Solveur'), el('td', { class: 'n muted small' }, 'EV'))),
      el('tbody', {}, rows)));
    box.append(el('div', { class: 'fb-range' }, 'Toute sa range ici : ',
      d.labels.map((l, i) => (d.range[i] >= 0.005 ? l + ' ' + pct(d.range[i]) : null)).filter(Boolean).join(' · ')));
    if (d.evLoss >= ERROR && d.freq >= MIXED) {
      box.append(el('div', { class: 'fb-range' }, 'Écart d\'EV de ' + num(d.evLoss, 2) + ' bb, mais le solveur joue cette action '
        + pct(d.freq) + ' du temps : à l\'équilibre elle se vaut, l\'écart vient d\'un nœud pas tout à fait convergé.'));
    }
    const href = '/explorateur/' + encodeURIComponent(hand.ident) + '#chemin=' + encodeURIComponent(JSON.stringify(d.path));
    box.append(el('div', { class: 'fb-links' }, el('a', { href, target: '_blank', rel: 'noopener' }, 'Voir dans l\'explorateur ↗')));
  }

  function renderEnd() {
    const h = hand, box = $('actions');
    box.textContent = '';
    const fb = $('feedback');
    if (h.result) {
      const r = h.result;
      const text = r.fold ? (r.winner === h.hero ? 'Il folde : tu remportes le pot.' : 'Tu foldes.')
        : r.winner === null || r.winner === undefined ? 'Abattage : partage.' : r.winner === h.hero ? 'Abattage : tu gagnes.' : 'Abattage : il gagne.';
      box.append(el('div', { class: 'result ' + (r.net > 0 ? 'win' : r.net < 0 ? 'loss' : '') },
        text + ' ' + (r.net >= 0 ? '+' : '−') + num(Math.abs(r.net)) + ' bb'));
    }
    const lost = h.decisions.reduce((a, d) => a + d.loss, 0);
    const recap = el('div', { class: 'recap' }, h.decisions.map((d) => el('div', {},
      el('span', {}, STREET[d.street] + ' : ' + d.labels[d.choice] + (d.choice !== d.best ? ' (solveur : ' + d.labels[d.best] + ')' : '')),
      el('span', { style: 'color:' + verdictColor(d.verdict) }, d.loss >= 0.005 ? '−' + num(d.loss, 2) + ' bb' : VERDICT[d.verdict]))));
    if (!h.decisions.length) recap.append(el('div', {}, el('span', { class: 'muted' }, 'Aucune décision dans cette main.')));
    box.append(el('div', { style: 'flex-basis:100%' },
      el('div', { class: 'muted small' }, 'Cette main : ' + (lost >= 0.005 ? num(lost, 2) + ' bb d\'EV perdue' : 'aucune EV perdue')), recap),
    el('div', { class: 'next' }, el('button', { type: 'button', class: 'go', onclick: () => go() }, 'Main suivante'),
      el('span', { class: 'wait' }, 'Espace ou Entrée')));
    if (!fb.textContent && h.feedback) renderFeedback();
  }

  function renderSession() {
    const box = $('session');
    box.textContent = '';
    if (!session.decisions && !session.hands) return;
    const good = session.decisions ? 1 - session.errors / session.decisions : null;
    const tile = (v, l) => el('div', {}, el('div', { class: 'v' }, v), el('div', { class: 'l' }, l));
    box.append(el('div', { class: 'muted small', style: 'margin-bottom:6px' }, 'Cette séance'),
      el('div', { class: 'sess' }, tile(String(session.hands), 'mains'), tile(String(session.decisions), 'décisions'),
        tile(good === null ? '–' : pct(good), 'justes'),
        tile(session.decisions ? num(session.lost / session.decisions, 2) : '–', 'bb perdus / décision')));
  }

  // ---------- réglages : rendu ----------
  function showSetup() {
    hand = null;
    resume = null;
    $('play').hidden = true;
    $('setup').hidden = false;
    $('b-setup').hidden = true;
    $('meta').textContent = '';
    renderSetup();
    renderProgress();
  }

  function renderSetup() {
    const fams = data.families;
    if (!fams[cfg.family]) cfg.family = 'srp';
    const famBox = $('f-family');
    famBox.textContent = '';
    for (const [id, f] of Object.entries(fams)) {
      famBox.append(el('button', { type: 'button', 'aria-pressed': String(cfg.family === id), disabled: !f.spots.length,
        onclick: () => { cfg.family = id; cfg.flop = '*'; if (cfg.start.startsWith('sit:')) cfg.start = 'root'; renderSetup(); } },
      f.name, el('span', { class: 'n' }, String(f.spots.length))));
    }
    const spots = fams[cfg.family].spots;
    const flop = $('f-flop');
    flop.textContent = '';
    flop.append(el('option', { value: '*' }, 'Au hasard parmi les ' + spots.length + ' flops résolus'));
    const textures = data.textures.filter((t) => spots.some((s) => s.texture === t));
    if (textures.length > 1) {
      flop.append(el('optgroup', { label: 'Une texture, au hasard' }, textures.map((t) =>
        el('option', { value: 'tex:' + t }, t + ' (' + spots.filter((s) => s.texture === t).length + ')'))));
    }
    flop.append(el('optgroup', { label: 'Un flop' }, spots.map((s) =>
      el('option', { value: s.id }, s.board.map(cardText).join(' ') + ' — ' + s.texture))));
    if (![...flop.options].some((o) => o.value === cfg.flop)) cfg.flop = '*';
    flop.value = cfg.flop;
    $('row-per').hidden = !(cfg.flop === '*' || cfg.flop.startsWith('tex:'));
    $('f-per').value = cfg.per;

    // Une situation précise fixe le côté (préfixe BB / BTN de son libellé).
    const sideBox = $('f-side');
    sideBox.textContent = '';
    for (const [id, label] of [['BB', 'BB'], ['BTN', 'BTN'], ['both', 'Les deux']]) {
      sideBox.append(el('button', { type: 'button', 'aria-pressed': String(cfg.side === id),
        onclick: () => { cfg.side = id; renderSetup(); } }, label));
    }

    const start = $('f-start');
    start.textContent = '';
    start.append(el('option', { value: 'root' }, 'Tout le coup, dès le flop'),
      el('option', { value: 'turn' }, 'À la turn (ligne tirée selon le solveur)'),
      el('option', { value: 'river' }, 'À la river (ligne tirée selon le solveur)'));
    const situations = Object.entries(fams[cfg.family].situations);
    for (let st = 0; st < 3; st++) {
      const opts = situations.filter(([key]) => {
        const t = parseKey(key);
        return t.street === st && (cfg.side === 'both' || POS[t.player] === cfg.side) && t.level <= 3;
      }).map(([key, label]) => el('option', { value: 'sit:' + key }, POS[parseKey(key).player] + ' · ' + label));
      if (opts.length) start.append(el('optgroup', { label: 'Une situation — ' + STREET[st] }, opts));
    }
    if (![...start.options].some((o) => o.value === cfg.start)) cfg.start = 'root';
    start.value = cfg.start;
    $('f-single').checked = cfg.single;
    $('f-auto').checked = cfg.auto;
    const none = !spots.length;
    $('b-start').disabled = none || !data.ready;
    $('setup-note').textContent = !data.ready ? 'Installe d\'abord le solveur : python -m analyzer gtopen --installer'
      : none ? 'Aucun flop résolu dans ce type de pot : lance la série dans « Études du solveur ».' : '';
  }

  function renderProgress() {
    const box = $('progress');
    box.textContent = '';
    const p = data.progress;
    box.append(el('h2', {}, 'Tes progrès'));
    if (!p.all.n) {
      box.append(el('p', { class: 'muted' }, 'Pas encore de décision enregistrée. Chaque décision jouée ici est gardée '
        + '(~/.analyzer/entrainement) pour suivre tes progrès situation par situation.'));
      return;
    }
    const tile = (l, v, s) => el('div', { class: 'ptile' }, el('div', { class: 'l' }, l), el('div', { class: 'v' }, v), el('div', { class: 's' }, s));
    const rate = (t) => (t.n ? pct(t.good) : '–');
    box.append(el('div', { class: 'ptiles' },
      tile('Décisions', String(p.all.n), p.recent.n + ' ces 7 derniers jours'),
      tile('Justes', rate(p.all), 'ces 7 jours : ' + rate(p.recent)),
      tile('EV perdue / décision', num(p.all.lost / p.all.n, 2) + ' bb', p.recent.n ? 'ces 7 jours : ' + num(p.recent.lost / p.recent.n, 2) + ' bb' : '')));
    const rows = p.situations.slice(0, 30).map((s) => el('tr', {},
      el('td', {}, s.label, el('span', { class: 'fam' }, data.families[s.family] ? data.families[s.family].name : s.family)),
      el('td', { class: 'n' }, String(s.n)),
      el('td', { class: 'n' + (s.good < 0.7 ? ' bad' : '') }, pct(s.good)),
      el('td', { class: 'n' }, num(s.lost / s.n, 2)),
      el('td', { class: 'n' }, s.recent.n ? s.recent.n + ' · ' + pct(s.recent.good) : '–'),
      el('td', {}, el('button', { type: 'button', class: 'link', onclick: () => drill(s.family, s.key) }, 'S\'entraîner'))));
    box.append(el('div', { class: 'ptable-wrap' }, el('table', { class: 'ptable' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Situation'), el('th', { class: 'n' }, 'Décisions'), el('th', { class: 'n' }, 'Justes'),
        el('th', { class: 'n' }, 'bb perdus / déc.'), el('th', { class: 'n' }, '7 jours'), el('th', {}))),
      el('tbody', {}, rows))));
    box.append(el('p', { class: 'muted small' }, 'Juste : moins de 0,25 bb d\'EV perdue (une action que le solveur joue au moins 10 % du temps '
      + 'avec ta main ne coûte rien). Les situations où tu perds le plus sont en haut. ',
    el('button', { type: 'button', class: 'link', onclick: clearJournal }, 'Effacer l\'historique')));
  }

  function drill(fam, key) {
    if (cfg.family !== fam || !cfg.flop.startsWith('tex:')) cfg.flop = '*';
    cfg.family = fam;
    const side = POS[parseKey(key).player];
    if (cfg.side !== 'both' && cfg.side !== side) cfg.side = side;
    cfg.start = 'sit:' + key;
    cfg.single = true;
    saveCfg();
    renderSetup();
    start();
  }

  async function clearJournal() {
    if (!window.confirm('Effacer tout l\'historique de l\'entraîneur ?')) return;
    data.progress = await api('/api/entraineur/effacer', {});
    renderProgress();
  }

  function start() {
    saveCfg();
    flopHands = Infinity;  // nouveau tirage du flop
    if (cfg.flop !== '*' && !cfg.flop.startsWith('tex:')) flopHands = 0;
    newHand();
  }

  // ---------- événements ----------
  $('f-flop').onchange = (e) => { cfg.flop = e.target.value; renderSetup(); };
  $('f-per').onchange = (e) => { cfg.per = e.target.value; };
  $('f-start').onchange = (e) => {
    cfg.start = e.target.value;
    if (cfg.start.startsWith('sit:')) cfg.single = true;
    renderSetup();
  };
  $('f-single').onchange = (e) => { cfg.single = e.target.checked; };
  $('f-auto').onchange = (e) => { cfg.auto = e.target.checked; };
  $('b-start').onclick = start;
  $('b-setup').onclick = () => {
    if (fixed) { fixed = null; history.replaceState(null, '', location.pathname); }
    showSetup();
  };
  document.addEventListener('keydown', (e) => {
    if (e.target.closest('select, input, textarea')) return;
    if (!resume || $('play').hidden) {
      if ((e.key === 'r' || e.key === 'R') && !$('play').hidden) $('b-setup').click();
      return;
    }
    const n = hand && hand.node;
    const digit = Number(e.key);
    const choosing = hand && !hand.done && n && n.type === 'action' && n.player === hand.hero && $('actions').querySelector('button.act');
    if (choosing && digit >= 1 && digit <= n.actions.length) { e.preventDefault(); go(digit - 1); }
    else if (!choosing && (e.key === ' ' || e.key === 'Enter')) { e.preventDefault(); go(); }
    else if (e.key === 'r' || e.key === 'R') $('b-setup').click();
  });

  // ---------- démarrage ----------
  if (window.self !== window.top) {  // dans l'application : pas de bandeau, et l'onglet suit la page
    document.body.classList.add('embed');
    window.parent.postMessage({ type: 'analyzer-page', path: location.pathname, hash: '' }, location.origin);
  }
  (async () => {
    cfg = loadCfg();
    try {
      data = await api('/api/entraineur');
    } catch (e) {
      status(e.message, true);
      return;
    }
    const q = new URLSearchParams(location.search);
    const spot = q.get('spot');
    if (spot) {
      let path = [];
      try { path = JSON.parse(q.get('chemin') || '[]'); } catch (e) { path = []; }
      fixed = { ident: spot, path: Array.isArray(path) ? path : [] };
      cfg.single = false;
      return start();
    }
    const fam = q.get('famille'), key = q.get('situation');
    if (fam && data.families[fam] && key && data.families[fam].situations[key]) {
      history.replaceState(null, '', location.pathname);
      return drill(fam, key);
    }
    showSetup();
  })();
})();
