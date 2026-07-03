/**
 * William Agent — shared UI utilities (Mobbin-inspired interaction patterns).
 */

const WilliamUI = (() => {
  const POLL_FAST_MS = 800;
  const POLL_SLOW_MS = 3000;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatEngine(name) {
    if (!name) return '';
    return name === 'ollama' ? 'willy' : name;
  }

  function formatTime(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  }

  function autoGrow(textarea, max = 160) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, max) + 'px';
  }

  async function fetchJson(url, opts) {
    const res = await fetch(url, opts);
    return res.json();
  }

  function chip(label, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'w-chip';
    btn.textContent = label;
    if (onClick) btn.addEventListener('click', onClick);
    return btn;
  }

  function createMessage({ role, content, meta, time }) {
    const wrap = document.createElement('div');
    wrap.className = `w-msg-row ${role}`;

    const avatar = document.createElement('div');
    avatar.className = 'w-msg-avatar';
    avatar.textContent = role === 'user' ? 'Y' : 'W';

    const bubble = document.createElement('div');
    bubble.className = `w-msg ${role}`;
    bubble.innerHTML = escapeHtml(content);

    if (meta || time) {
      const foot = document.createElement('div');
      foot.className = 'w-msg-meta';
      foot.textContent = [meta, time].filter(Boolean).join(' · ');
      bubble.appendChild(foot);
    }

    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    return wrap;
  }

  function setTyping(el, on, label = 'William is thinking…') {
    if (!el) return;
    el.classList.toggle('visible', on);
    el.setAttribute('aria-hidden', on ? 'false' : 'true');
    const labelEl = el.querySelector('.w-typing-label');
    if (labelEl) labelEl.textContent = label;
  }

  class HealthPoller {
    constructor({ onHealth, fastWhen }) {
      this.onHealth = onHealth;
      this.fastWhen = fastWhen || (() => false);
      this._timer = null;
      this._fast = false;
    }

    async tick() {
      try {
        const health = await fetchJson('/api/health');
        this.onHealth(health);
        const voice = health.voice_ui;
        const helper = health.macos_helper || {};
        const wantFast = this.fastWhen(voice, helper);
        if (wantFast !== this._fast) {
          this._fast = wantFast;
          this.schedule();
        }
      } catch (_) { /* offline */ }
    }

    schedule() {
      if (this._timer) clearInterval(this._timer);
      const ms = this._fast ? POLL_FAST_MS : POLL_SLOW_MS;
      this._timer = setInterval(() => this.tick(), ms);
    }

    start() {
      this.tick();
      this.schedule();
    }

    stop() {
      if (this._timer) clearInterval(this._timer);
    }
  }

  return {
    escapeHtml,
    formatEngine,
    formatTime,
    autoGrow,
    fetchJson,
    chip,
    createMessage,
    setTyping,
    HealthPoller,
    POLL_FAST_MS,
    POLL_SLOW_MS,
  };
})();

if (typeof window !== 'undefined') {
  window.WilliamUI = WilliamUI;
}
