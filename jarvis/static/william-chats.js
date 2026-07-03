/**
 * William Agent — chat history sidebar (new chat, list, preview, delete).
 */

const WilliamChats = (() => {
  const STORAGE_KEY = 'william_last_session_web';
  const SOURCE = 'web';

  let listEl = null;
  let searchEl = null;
  let sessions = [];
  let activeId = null;
  let onSelect = null;
  let onNew = null;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatWhen(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z');
      const now = new Date();
      const sameDay = d.toDateString() === now.toDateString();
      if (sameDay) {
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
      const diff = (now - d) / 86400000;
      if (diff < 7) {
        return d.toLocaleDateString([], { weekday: 'short' });
      }
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    } catch {
      return '';
    }
  }

  function persistActive(id) {
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id);
      else localStorage.removeItem(STORAGE_KEY);
    } catch { /* private mode */ }
  }

  function loadPersisted() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  }

  async function api(url, opts) {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.error || 'Request failed');
    }
    return data;
  }

  function filteredSessions(query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter(
      (s) =>
        (s.title || '').toLowerCase().includes(q) ||
        (s.preview || '').toLowerCase().includes(q)
    );
  }

  function renderList(query) {
    if (!listEl) return;
    const items = filteredSessions(query);
    if (!items.length) {
      listEl.innerHTML = `<p class="w-chat-empty">${query ? 'No chats match your search' : 'No chats yet — start a new one'}</p>`;
      return;
    }

    listEl.innerHTML = items
      .map((s) => {
        const active = s.id === activeId ? ' active' : '';
        return `
        <div class="w-chat-item${active}" data-id="${escapeHtml(s.id)}" role="button" tabindex="0">
          <div class="w-chat-item-main">
            <div class="w-chat-title">${escapeHtml(s.title || 'New chat')}</div>
            <div class="w-chat-preview">${escapeHtml(s.preview || '')}</div>
          </div>
          <div class="w-chat-item-side">
            <span class="w-chat-when">${escapeHtml(formatWhen(s.last_active_at))}</span>
            <button type="button" class="w-chat-delete" data-delete="${escapeHtml(s.id)}" aria-label="Delete chat" title="Delete">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
            </button>
          </div>
        </div>`;
      })
      .join('');
  }

  async function refresh() {
    const data = await api(`/api/sessions?source=${SOURCE}&limit=60`);
    sessions = data.sessions || [];
    renderList(searchEl?.value || '');
    return sessions;
  }

  async function createNew() {
    const data = await api('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: SOURCE }),
    });
    const session = data.session;
    sessions = [session, ...sessions.filter((s) => s.id !== session.id)];
    setActive(session.id, { notify: false });
    renderList(searchEl?.value || '');
    if (onNew) onNew(session);
    return session;
  }

  function setActive(id, { notify = true } = {}) {
    activeId = id;
    persistActive(id);
    renderList(searchEl?.value || '');
    if (notify && onSelect && id) onSelect(id);
  }

  async function select(id) {
    if (!id || id === activeId) return;
    setActive(id);
  }

  async function deleteChat(id) {
    const item = sessions.find((s) => s.id === id);
    const label = item?.title || 'this chat';
    if (!confirm(`Delete "${label}"? This cannot be undone.`)) return;

    await api(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
    sessions = sessions.filter((s) => s.id !== id);

    if (activeId === id) {
      const next = sessions[0];
      if (next) {
        setActive(next.id);
        if (onSelect) onSelect(next.id);
      } else {
        activeId = null;
        persistActive(null);
        renderList(searchEl?.value || '');
        if (onNew) onNew(null);
      }
    } else {
      renderList(searchEl?.value || '');
    }
  }

  async function resolveInitialSession() {
    await refresh();
    const persisted = loadPersisted();
    if (persisted && sessions.some((s) => s.id === persisted)) {
      return persisted;
    }
    return sessions[0]?.id || null;
  }

  function init({
    list,
    search,
    newBtn,
    onSelect: onSelectCb,
    onNew: onNewCb,
  }) {
    listEl = list;
    searchEl = search;
    onSelect = onSelectCb;
    onNew = onNewCb;

    newBtn?.addEventListener('click', () => createNew().catch(() => {}));

    searchEl?.addEventListener('input', () => renderList(searchEl.value));

    listEl?.addEventListener('click', (e) => {
      const del = e.target.closest('[data-delete]');
      if (del) {
        e.stopPropagation();
        deleteChat(del.dataset.delete).catch(() => {});
        return;
      }
      const item = e.target.closest('.w-chat-item');
      if (item?.dataset.id) select(item.dataset.id).catch(() => {});
    });

    listEl?.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const item = e.target.closest('.w-chat-item');
      if (item?.dataset.id) {
        e.preventDefault();
        select(item.dataset.id).catch(() => {});
      }
    });
  }

  return {
    init,
    refresh,
    createNew,
    setActive,
    select,
    deleteChat,
    resolveInitialSession,
    getActiveId: () => activeId,
    persistActive,
  };
})();

if (typeof window !== 'undefined') {
  window.WilliamChats = WilliamChats;
}
