/* Replayer : revoir une main jouée action par action (heads-up ou table à plusieurs, finie au préflop ou à
   l'abattage). Adresse : /rejouer/<main>?api=/api/eleves/<élève> (l'espace de la main ; /api par défaut).
   ?stop=<n> : la main s'arrête avant sa n-ième action (l'entraîneur : la décision à prendre), sans le résultat ni
   les cartes des autres ; ?embed=1 : sans bandeau, dans une autre page. */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const SUITS = { s: '♠', h: '♥', d: '♦', c: '♣' };
  const STREETS = ['preflop', 'flop', 'turn', 'river'];
  const NAMES = { preflop: 'Préflop', flop: 'Flop', turn: 'Turn', river: 'River' };
  const BOARD = { preflop: 0, flop: 3, turn: 4, river: 5 };
  const POSTS = ['post_sb', 'post_bb', 'post_ante'];

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
  const card = (c) => el('span', { class: 'pc s' + c[1] }, c[0] + SUITS[c[1]]);
  const fmt = (x) => (Math.round(x * 100) / 100).toLocaleString('fr-FR', { maximumFractionDigits: 2 });
  const signed = (x) => (x > 0 ? '+' : x < 0 ? '−' : '') + fmt(Math.abs(x));
  const bb = (x) => fmt(x) + ' bb';

  const params = new URLSearchParams(location.search);
  const SPACE = /^\/api(\/(eleves|references)\/[A-Za-z0-9_-]+)?$/;
  const api = SPACE.test(params.get('api') || '') ? params.get('api') : '/api';
  const stop = /^\d+$/.test(params.get('stop') || '') ? Number(params.get('stop')) : null;
  const embed = params.get('embed') === '1' || window.self !== window.top && stop !== null;
  if (embed) document.body.classList.add('embed');
  const id = decodeURIComponent(location.pathname.split('/').filter(Boolean)[1] || '');

  let hand = null, steps = [], at = 0, timer = null;

  function label(a) {
    switch (a.k) {
      case 'fold': return 'se couche';
      case 'check': return 'check';
      case 'call': return 'paie ' + bb(a.a) + (a.ai ? ' (tapis)' : '');
      case 'bet': return 'mise ' + bb(a.a) + (a.pot ? ' (' + Math.round(100 * a.a / a.pot) + ' % du pot)' : '')
        + (a.ai ? ' (tapis)' : '');
      case 'raise': return 'relance à ' + bb(a.to) + (a.ai ? ' (tapis)' : '');
      case 'post_sb': return 'petite blinde ' + bb(a.a);
      case 'post_bb': return 'grosse blinde ' + bb(a.a);
      default: return 'mise morte ' + bb(a.a);
    }
  }
  const short = (a) => ({ fold: 'Couché', check: 'Check', call: 'Paie', bet: 'Mise', raise: 'Relance' }[a.k] || '');

  // Les étapes : les mises forcées du départ, puis une par action ; une street sans action (tapis payé) a la sienne,
  // et la fin (gains, cartes montrées) la dernière. Avec stop, la main s'arrête avant cette action.
  function build() {
    const out = [{ type: 'start', upto: 0 }];
    let i = 0;
    while (i < hand.actions.length && POSTS.includes(hand.actions[i].k)) i++;
    out[0].upto = i;
    let street = 'preflop';
    const last = stop !== null ? Math.min(stop, hand.actions.length) : hand.actions.length;
    for (; i < last; i++) {
      const a = hand.actions[i];
      while (STREETS.indexOf(a.s) > STREETS.indexOf(street) + 1) {  // une street passée sans action
        street = STREETS[STREETS.indexOf(street) + 1];
        out.push({ type: 'street', street, upto: i });
      }
      street = a.s;
      out.push({ type: 'action', i, upto: i + 1, street });
    }
    if (stop !== null) {
      const a = hand.actions[last];
      if (a && a.s !== street) out.push({ type: 'street', street: a.s, upto: last });
      return out;
    }
    const reached = Object.entries(BOARD).filter(([, n]) => n && n <= hand.board.length).map(([s]) => s);
    for (const s of reached) {
      if (STREETS.indexOf(s) > STREETS.indexOf(street)) out.push({ type: 'street', street: s, upto: last });
    }
    out.push({ type: 'end', upto: last });
    return out;
  }

  // L'état de la table après les actions [0, upto) : tapis, mises de la street, pot, couchés, dernière action.
  function stateAt(k) {
    const step = steps[k];
    const n = hand.players.length;
    const s = { stack: hand.players.map((p) => p.stack), bet: Array(n).fill(0), out: Array(n).fill(false),
      last: Array(n).fill(null), pot: 0, street: 'preflop', turn: null };
    for (let i = 0; i < step.upto; i++) {
      const a = hand.actions[i];
      if (a.s !== s.street) {  // nouvelle street : les mises vont au pot
        s.pot += s.bet.reduce((x, y) => x + y, 0);
        s.bet.fill(0);
        s.last.fill(null);
        s.street = a.s;
      }
      s.stack[a.p] -= a.a;
      if (a.k === 'post_ante') s.pot += a.a;
      else s.bet[a.p] += a.a;
      if (a.k === 'fold') s.out[a.p] = true;
      if (!POSTS.includes(a.k)) s.last[a.p] = a;
    }
    if (step.type === 'street' || step.type === 'end') {
      s.pot += s.bet.reduce((x, y) => x + y, 0);
      s.bet.fill(0);
      s.last.fill(null);
      if (step.type === 'street') s.street = step.street;
    }
    if (step.type === 'end') {
      for (const [p, v] of Object.entries(hand.ret)) { s.stack[p] += v; s.pot -= v; }
    }
    const next = hand.actions[step.upto];
    if (step.type !== 'end' && next) s.turn = next.p;
    s.board = step.type === 'end' ? hand.board : hand.board.slice(0, BOARD[s.street]);
    return s;
  }

  function seatXY(k, n, heroAt, radius) {
    const angle = Math.PI / 2 + 2 * Math.PI * (k - heroAt) / n;
    return [50 + radius[0] * Math.cos(angle), 50 + radius[1] * Math.sin(angle)];
  }

  function render() {
    const s = stateAt(at);
    const step = steps[at];
    const end = step.type === 'end';
    const reveal = $('f-cards').checked || end;
    const felt = $('felt');
    felt.querySelectorAll('.rp-seat, .rp-bet').forEach((x) => x.remove());
    const n = hand.players.length;
    const heroAt = Math.max(0, hand.players.findIndex((p) => p.hero));
    hand.players.forEach((p, k) => {
      const [x, y] = seatXY(k, n, heroAt, [41, 39]);
      const known = p.cards.length === 2 && (p.hero || (reveal && stop === null));
      const cards = s.out[k] && !p.hero ? '' : known ? p.cards.map(card)
        : [el('span', { class: 'back' }), el('span', { class: 'back' })];
      const last = s.last[k];
      const result = end ? el('div', { class: 'res ' + (p.net > 0 ? 'pos' : p.net < 0 ? 'neg' : '') },
        signed(p.net) + ' bb') : null;
      felt.append(el('div', { class: 'rp-seat' + (p.hero ? ' hero' : '') + (s.out[k] ? ' out' : '')
          + (s.turn === k ? ' turn' : ''), style: `left:${x}%;top:${y}%` },
        el('div', { class: 'nm', title: p.name }, p.name, el('span', { class: 'ps' }, p.pos),
          p.button ? el('span', { class: 'btn', title: 'Bouton' }, 'B') : null),
        el('div', { class: 'cards' }, cards),
        el('div', { class: 'stk' }, bb(Math.max(0, s.stack[k]))),
        last ? el('div', { class: 'act' }, short(last)) : null, result));
      if (s.bet[k] > 0) {
        const [bx, by] = seatXY(k, n, heroAt, [25, 22]);
        felt.append(el('div', { class: 'rp-bet', style: `left:${bx}%;top:${by}%` }, bb(s.bet[k])));
      }
    });
    const board = $('board');
    board.textContent = '';
    s.board.forEach((c) => board.append(card(c)));
    if (hand.board.length) for (let k = s.board.length; k < 5; k++) board.append(el('span', { class: 'slot' }));
    const live = s.bet.reduce((x, y) => x + y, 0);
    $('pot').textContent = 'Pot ' + bb(s.pot + live) + (live && s.pot ? ' (dont ' + bb(live) + ' de mises)' : '');
    $('step').textContent = (at + 1) + ' / ' + steps.length;
    $('b-first').disabled = $('b-prev').disabled = at === 0;
    $('b-next').disabled = $('b-last').disabled = at === steps.length - 1;
    $('b-play').textContent = timer ? 'Pause' : 'Lecture';
    $('b-play').disabled = at === steps.length - 1 && !timer;
    $('now').textContent = '';
    $('now').append(describe(step, s));
    renderLog();
  }

  function describe(step, s) {
    if (step.type === 'start') {
      const hero = hand.players.find((p) => p.hero);
      return el('span', {}, 'Blindes posées' + (hero && hero.cards.length ? ' ; tes cartes : ' : '.'),
        hero && hero.cards.length ? el('b', {}, hero.cards.map((c) => c[0] + SUITS[c[1]]).join(' ')) : '');
    }
    if (step.type === 'street') return el('span', {}, el('b', {}, NAMES[step.street]), ' · pot ' + bb(s.pot));
    if (step.type === 'end') {
      const won = Object.entries(hand.win).map(([p, v]) => hand.players[p].name + ' récupère ' + bb(v));
      return el('span', {}, el('b', {}, 'Fin de la main'), ' — ' + (won.join(', ') || 'pas de gain lu')
        + (hand.rake ? ' (rake ' + bb(hand.rake) + ')' : ''));
    }
    const a = hand.actions[step.i];
    const p = hand.players[a.p];
    return el('span', {}, el('b', {}, p.name), ' (' + p.pos + ') ' + label(a));
  }

  function renderLog() {
    const log = $('log');
    log.textContent = '';
    let street = null, list = null;
    steps.forEach((step, k) => {
      const st = step.type === 'action' ? hand.actions[step.i].s : step.street;
      if ((step.type === 'action' || step.type === 'street') && st !== street) {
        street = st;
        const s = stateAt(k);
        const cards = hand.board.slice(BOARD[st] === 3 ? 0 : BOARD[st] - 1, BOARD[st]);
        log.append(el('h3', {}, NAMES[st], cards.map(card),
          st !== 'preflop' ? el('span', { class: 'pot' }, 'pot ' + bb(s.pot + s.bet.reduce((x, y) => x + y, 0))) : ''));
        list = el('ol', {});
        log.append(list);
      }
      if (step.type === 'start') {
        log.append(el('h3', {}, NAMES.preflop));
        street = 'preflop';
        list = el('ol', {});
        log.append(list);
        hand.actions.slice(0, step.upto).forEach((a) => list.append(item(k, el('span', {},
          el('span', { class: 'who' + (hand.players[a.p].hero ? ' hero' : '') }, hand.players[a.p].pos),
          ' ' + label(a)))));
        return;
      }
      if (step.type === 'action') {
        const a = hand.actions[step.i];
        const p = hand.players[a.p];
        list.append(item(k, el('span', {}, el('span', { class: 'who' + (p.hero ? ' hero' : '') }, p.pos + ' ' + p.name),
          ' ' + label(a))));
      }
    });
    if (steps[steps.length - 1].type === 'end') {
      const box = el('div', { class: 'end' }, el('b', {}, 'Résultat'));
      hand.players.forEach((p, k) => {
        if (!p.net && !hand.win[k]) return;
        const cards = p.cards.length && (p.hero || at === steps.length - 1 || $('f-cards').checked)
          ? ' ' + p.cards.map((c) => c[0] + SUITS[c[1]]).join(' ') : '';
        box.append(el('div', {}, (p.hero ? 'Toi (' + p.name + ')' : p.name) + cards + (p.shown ? ' — ' + p.shown : '')
          + ' : ', el('span', { class: p.net > 0 ? 'pos' : p.net < 0 ? 'neg' : '' }, signed(p.net) + ' bb'),
          p.ev !== null && p.ev !== undefined && Math.abs(p.ev - p.net) > 0.01
            ? ' (EV all-in ' + signed(p.ev) + ' bb)' : ''));
      });
      if (at === steps.length - 1) log.append(box);
    }
  }

  function item(k, content) {
    return el('li', {}, el('button', { type: 'button', class: k === at ? 'now' : k > at ? 'future' : '',
      onclick: () => go(k) }, content));
  }

  function go(k) {
    at = Math.max(0, Math.min(steps.length - 1, k));
    if (at === steps.length - 1) pause();
    render();
  }
  function pause() { if (timer) { clearInterval(timer); timer = null; } }
  function play() {
    if (timer) { pause(); render(); return; }
    if (at === steps.length - 1) at = 0;
    timer = setInterval(() => go(at + 1), 1100);
    go(at + 1);
  }

  function header() {
    $('meta').textContent = [hand.stakes, hand.site, hand.format === 'HU' ? 'heads-up' : hand.format, hand.date,
      'main ' + hand.id].join(' · ');
    const links = $('links');
    if (hand.explorer && stop === null) {
      links.append(el('a', { href: '/explorateur/' + encodeURIComponent(hand.id), target: '_blank', rel: 'noopener' },
        'Ouvrir au solveur ↗'));
    }
  }

  async function load() {
    try {
      const res = await fetch(api + '/rejouer/' + encodeURIComponent(id));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Main introuvable.');
      hand = data;
    } catch (err) {
      $('status').textContent = err.message || String(err);
      return;
    }
    steps = build();
    at = stop !== null ? steps.length - 1 : 0;
    header();
    $('main').hidden = false;
    $('b-first').addEventListener('click', () => { pause(); go(0); });
    $('b-prev').addEventListener('click', () => { pause(); go(at - 1); });
    $('b-next').addEventListener('click', () => { pause(); go(at + 1); });
    $('b-last').addEventListener('click', () => { pause(); go(steps.length - 1); });
    $('b-play').addEventListener('click', play);
    $('f-cards').addEventListener('change', render);
    if (stop !== null) $('f-cards').closest('label').hidden = true;
    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input, select, textarea')) return;
      if (e.key === 'ArrowRight') { pause(); go(at + 1); } else if (e.key === 'ArrowLeft') { pause(); go(at - 1); }
      else if (e.key === 'Home') { pause(); go(0); } else if (e.key === 'End') { pause(); go(steps.length - 1); }
      else if (e.key === ' ') { e.preventDefault(); play(); } else return;
    });
    render();
  }

  load();
})();
