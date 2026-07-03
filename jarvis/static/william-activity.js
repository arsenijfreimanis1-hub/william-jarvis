/** Live SSE activity feed for William Agent UI */

const WilliamActivity = (() => {
  const ICONS = {
    screen: '🧭',
    task: '⚡',
    chat: '💬',
    integration: '🔌',
    system: '●',
    interaction: '💬',
    agent_step: '🤖',
  };

  let source = null;
  let feedEl = null;
  let dotEl = null;
  const maxCards = 80;

  function iconFor(kind) {
    return ICONS[kind] || '●';
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function formatEngine(name) {
    if (!name) return '';
    if (name === 'ollama') return 'willy';
    return name;
  }

  function engineLabel(event) {
    return event.engine ? ` · ${formatEngine(event.engine)}` : '';
  }

  function formatTime(ts) {
    if (!ts) return '';
    try {
      return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return '';
    }
  }

  function ensureLightbox() {
    let lb = document.getElementById('w-shot-lightbox');
    if (lb) return lb;
    lb = document.createElement('div');
    lb.id = 'w-shot-lightbox';
    lb.className = 'w-shot-lightbox';
    lb.innerHTML = '<img alt="Screen capture full size" />';
    lb.addEventListener('click', () => lb.classList.remove('open'));
    document.body.appendChild(lb);
    return lb;
  }

  function openLightbox(src) {
    const lb = ensureLightbox();
    const img = lb.querySelector('img');
    if (img) img.src = src;
    lb.classList.add('open');
  }

  function prependCard(event) {
    if (!feedEl) return;
    const empty = feedEl.querySelector('.w-act-empty');
    if (empty) empty.remove();

    const card = document.createElement('article');
    const status = event.status || 'done';
    card.className = `w-act-card ${event.kind || 'system'} ${status === 'running' ? 'running' : ''} ${status === 'error' ? 'error' : ''}`;

    let shotHtml = '';
    if (event.image_url) {
      shotHtml = `<div class="w-act-shot"><img src="${escapeHtml(event.image_url)}" alt="Screen capture" loading="lazy" /></div>`;
    }

    const engine = engineLabel(event);
    card.innerHTML = `
      <div class="w-act-top">
        <div class="w-act-icon">${iconFor(event.kind)}</div>
        <div class="w-act-body">
          <div class="w-act-title">${escapeHtml(event.title || event.kind || 'Activity')}</div>
          ${event.detail ? `<div class="w-act-detail">${escapeHtml(event.detail)}</div>` : ''}
          <div class="w-act-meta">${formatTime(event.ts)}${escapeHtml(engine)}</div>
          ${shotHtml}
        </div>
      </div>`;

    feedEl.insertBefore(card, feedEl.firstChild);
    while (feedEl.children.length > maxCards) {
      feedEl.removeChild(feedEl.lastChild);
    }
  }

  async function loadRecent() {
    try {
      const res = await fetch('/api/activity/recent?limit=25');
      if (!res.ok) return;
      const data = await res.json();
      const events = (data.events || []).slice().reverse();
      for (const ev of events) {
        prependCard({
          kind: ev.event_type,
          title: ev.intent || ev.event_type,
          detail: ev.assistant_reply || ev.user_message || '',
          status: ev.task_status || 'done',
          engine: ev.engine,
          ts: ev.created_at,
        });
      }
    } catch (_) { /* offline */ }
  }

  function connect() {
    if (source) source.close();
    source = new EventSource('/api/activity/stream');
    if (dotEl) dotEl.classList.remove('off');

    source.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        prependCard(event);
      } catch (_) { /* ignore */ }
    };

    source.onerror = () => {
      if (dotEl) dotEl.classList.add('off');
      source.close();
      setTimeout(connect, 3000);
    };
  }

  function init({ feed, liveDot, toggleBtn, rail, backdrop }) {
    feedEl = feed;
    dotEl = liveDot;

    feed.addEventListener('click', (e) => {
      const img = e.target.closest('.w-act-shot img');
      if (img?.src) openLightbox(img.src);
    });

    loadRecent().then(connect);

    if (toggleBtn && rail) {
      const open = () => {
        rail.classList.add('open');
        backdrop?.classList.add('open');
      };
      const close = () => {
        rail.classList.remove('open');
        backdrop?.classList.remove('open');
      };
      toggleBtn.addEventListener('click', () => {
        rail.classList.contains('open') ? close() : open();
      });
      backdrop?.addEventListener('click', close);
    }
  }

  function emitThinking(label) {
    prependCard({ kind: 'agent_step', title: label || 'Thinking…', status: 'running', engine: 'willy' });
  }

  return { init, prependCard, emitThinking };
})();

if (typeof window !== 'undefined') {
  window.WilliamActivity = WilliamActivity;
}

/** WKWebView-safe task group toggles for admin panel */
function toggleTaskGroup(btn) {
  const group = btn.closest('.task-group');
  if (group) group.classList.toggle('open');
}

if (typeof window !== 'undefined') {
  window.toggleTaskGroup = toggleTaskGroup;
}
