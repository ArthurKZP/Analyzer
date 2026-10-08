(function () {
  'use strict';

  // Fenêtre de discussion avec le coach (analyzer/app/coach_chat.py) : la page qui l'accueille appelle
  // AnalyzerCoach.mount(élément, { context, suggestions }) ; context() rend ce que l'élève regarde
  // ({ spot, path, main }) ou null.
  const STORE = 'analyzer-coach-conversation';

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

  // Mise en forme légère des réponses : titres, listes, gras, italique, code, tableaux simples.
  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>');

  function markdown(text) {
    const out = [];
    let list = null, para = [], table = null;
    const flush = () => {
      if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; }
      if (list) { out.push('<' + list.tag + '>' + list.items.map((i) => '<li>' + inline(i) + '</li>').join('') + '</' + list.tag + '>'); list = null; }
      if (table) {
        const rows = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
        const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim()));
        out.push('<table>' + rows.map((r, k) => '<tr>' + cells(r).map((c) => (k ? '<td>' : '<th>') + c + (k ? '</td>' : '</th>')).join('') + '</tr>').join('') + '</table>');
        table = null;
      }
    };
    for (const line of text.split('\n')) {
      const h = line.match(/^#{1,4}\s+(.*)/), ul = line.match(/^\s*[-*•]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/);
      if (/^\s*\|.*\|\s*$/.test(line)) {
        if (!table) { flush(); table = []; }
        table.push(line);
      } else if (h) { flush(); out.push('<h4>' + inline(h[1]) + '</h4>'); }
      else if (ul || ol) {
        const tag = ul ? 'ul' : 'ol';
        if (!list || list.tag !== tag) { flush(); list = { tag, items: [] }; }
        list.items.push((ul || ol)[1]);
      } else if (!line.trim()) flush();
      else {
        if (list || table) flush();
        para.push(line);
      }
    }
    flush();
    return out.join('');
  }

  async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Erreur ' + res.status);
    return data;
  }

  function mount(root, options) {
    options = options || {};
    let conv = null, timer = null, status = null;
    try { conv = sessionStorage.getItem(STORE); } catch (e) { conv = null; }
    const log = el('div', { class: 'cc-log', 'aria-live': 'polite' });
    const steps = el('div', { class: 'cc-steps' });
    const input = el('textarea', { rows: '2', placeholder: 'Pose ta question au coach…', 'aria-label': 'Question au coach' });
    const send = el('button', { type: 'submit', class: 'cc-send' }, 'Envoyer');
    const foot = el('div', { class: 'cc-foot' });
    // Sans crédit API : la même question (avec ce que tu regardes) se pose dans Claude, où Merlin est branché par MCP.
    const note = el('span', { class: 'cc-hint', 'aria-live': 'polite' });
    const alt = el('div', { class: 'cc-alt' }, el('button', {
      type: 'button', class: 'cc-new', title: 'À coller dans l\'application Claude ou Claude Code, où le coach est branché sur ton abonnement (MCP)',
      onclick: () => copyForClaude(),
    }, 'Copier pour Claude (abonnement)'), note);
    let lastQuestion = '';
    const form = el('form', { class: 'cc-form', onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, input, send);
    const chips = el('div', { class: 'cc-chips' }, (options.suggestions || []).map((q) => el('button', {
      type: 'button', class: 'cc-chip', onclick: () => ask(q),
    }, q)));
    root.classList.add('cc');
    root.append(log, steps, chips, form, alt, foot);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input.value); } });

    function render(view) {
      log.textContent = '';
      steps.textContent = '';
      foot.textContent = '';
      for (const m of (view ? view.messages : [])) {
        if (m.role === 'user') lastQuestion = m.text;
        if (m.role === 'user') log.append(el('div', { class: 'cc-msg cc-user' }, m.text));
        else {
          const body = el('div', { class: 'cc-msg cc-coach' });
          if (m.steps && m.steps.length) body.append(el('div', { class: 'cc-used' }, 'Consulté : ' + m.steps.join(' · ')));
          const text = el('div', { class: 'cc-text' });
          text.innerHTML = markdown(m.text);  // texte échappé avant la mise en forme
          body.append(text);
          log.append(body);
        }
      }
      const running = view && view.state === 'running';
      if (running) {
        steps.append(el('div', { class: 'cc-thinking' }, el('span', { class: 'cc-dot' }), 'Le coach réfléchit…'));
        for (const s of view.steps) steps.append(el('div', { class: 'cc-step' }, 'Le coach ' + s));
      }
      if (view && view.error) steps.append(el('div', { class: 'cc-error' }, view.error));
      chips.hidden = !!(view && view.messages.length);
      send.disabled = running;
      input.disabled = running;
      if (view && view.messages.length) {
        foot.append(el('span', {}, 'Coût estimé de la discussion : ' + (view.cost < 0.01 ? 'moins d\'un centime'
          : view.cost.toLocaleString('fr-FR', { maximumFractionDigits: 2 }) + ' $')),
        el('button', { type: 'button', class: 'cc-new', onclick: reset }, 'Nouvelle discussion'));
      }
      log.scrollTop = log.scrollHeight;
    }

    async function copyText(text) {
      try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* repli ci-dessous */ }
      const area = el('textarea', { style: 'position:fixed;opacity:0', 'aria-hidden': 'true' });
      area.value = text;
      document.body.append(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      area.remove();
      return ok;
    }

    async function copyForClaude(asked) {
      const text = (asked || input.value).trim() || lastQuestion;
      if (!text) { note.textContent = 'Écris d\'abord ta question.'; return; }
      let context = null;
      try { context = options.context ? options.context() : null; } catch (e) { context = null; }
      try {
        const out = await api('/api/coach/texte', { text, context });
        if (await copyText(out.text)) {
          note.textContent = 'Copié : colle-la dans Claude (application ou Claude Code), où Merlin est branché.';
        } else {
          note.textContent = '';
          note.append('Copie impossible : sélectionne le texte ci-dessous.', el('textarea', { class: 'cc-manual', readonly: true, rows: '3' }, out.text));
        }
      } catch (e) {
        note.textContent = e.message;
      }
    }

    function reset() {
      conv = null;
      try { sessionStorage.removeItem(STORE); } catch (e) { /* rien à oublier */ }
      render(null);
    }

    async function refresh() {
      clearTimeout(timer);
      if (!conv) return;
      try {
        const view = await api('/api/coach/' + encodeURIComponent(conv));
        render(view);
        if (view.state === 'running') timer = setTimeout(refresh, 1200);
      } catch (e) {
        reset();  // conversation oubliée (application relancée)
      }
    }

    async function ask(text) {
      text = (text || '').trim();
      if (status && !status.sdk) { copyForClaude(text); return; }  // pas d'API ici : la question part dans Claude
      if (!text || send.disabled) return;
      input.value = '';
      let context = null;
      try { context = options.context ? options.context() : null; } catch (e) { context = null; }
      try {
        const view = await api('/api/coach/message', { conversation: conv, text, context });
        conv = view.id;
        try { sessionStorage.setItem(STORE, conv); } catch (e) { /* conversation non retenue */ }
        render(view);
        timer = setTimeout(refresh, 800);
      } catch (e) {
        steps.textContent = '';
        steps.append(el('div', { class: 'cc-error' }, e.message));
      }
    }

    api('/api/coach').then((s) => {
      status = s;
      if (!s.sdk) {  // sans module ni clé : la question se copie pour Claude (abonnement, coach branché par MCP)
        send.hidden = true;
        alt.querySelector('button').textContent = 'Copier ma question pour Claude';
        log.append(el('div', { class: 'cc-setup' }, s.setup));
        return;
      }
      if (!s.key) foot.append(el('span', { class: 'cc-hint' }, 'Clé API non trouvée dans ANTHROPIC_API_KEY : le coach essaiera un profil « ant auth login ».'));
      if (conv) refresh();
    }).catch(() => {});
    return { ask, reset, status: () => status };
  }

  window.AnalyzerCoach = { mount, markdown };
})();
