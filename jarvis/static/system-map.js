/** William System Map — live topology + activity overlay */

(() => {
  const LAYOUT = {
    user: { x: 0.12, y: 0.5 },
    helper: { x: 0.28, y: 0.22 },
    openclaw: { x: 0.28, y: 0.78 },
    kiosk: { x: 0.28, y: 0.5 },
    desktop: { x: 0.28, y: 0.38 },
    map: { x: 0.28, y: 0.62 },
    core: { x: 0.5, y: 0.5 },
    ollama: { x: 0.72, y: 0.18 },
    cursor: { x: 0.72, y: 0.38 },
    web: { x: 0.72, y: 0.58 },
    worker: { x: 0.72, y: 0.78 },
    scheduler: { x: 0.5, y: 0.82 },
    memory: { x: 0.5, y: 0.18 },
    screen: { x: 0.88, y: 0.5 },
  };

  const nodesEl = document.getElementById('sm-nodes');
  const edgesEl = document.getElementById('sm-edges');
  const statsEl = document.getElementById('sm-stats');
  const tasksEl = document.getElementById('sm-tasks');
  const feedEl = document.getElementById('sm-feed');
  const capsEl = document.getElementById('sm-caps');
  const tsEl = document.getElementById('sm-ts');
  const liveDot = document.getElementById('sm-live-dot');
  const subtitle = document.getElementById('sm-subtitle');

  let nodeEls = {};
  let pollTimer = null;
  let activitySource = null;
  let lastMap = null;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function formatTime(ts) {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return ts;
    }
  }

  function nodePos(id, wrap) {
    const preset = LAYOUT[id];
    if (!preset) {
      const idx = Object.keys(LAYOUT).indexOf(id);
      return { x: 0.85, y: 0.15 + (idx % 5) * 0.15 };
    }
    return preset;
  }

  function renderNodes(map) {
    const rect = nodesEl.getBoundingClientRect();
    const w = rect.width || 800;
    const h = rect.height || 500;

    map.nodes.forEach((node) => {
      const pos = nodePos(node.id);
      const x = pos.x * w;
      const y = pos.y * h;
      let el = nodeEls[node.id];
      if (!el) {
        el = document.createElement('div');
        el.className = 'sm-node';
        el.dataset.id = node.id;
        nodesEl.appendChild(el);
        nodeEls[node.id] = el;
      }
      el.className = `sm-node ${node.status || 'unknown'}`;
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
      el.innerHTML = `
        <div class="sm-node-head">
          <span class="sm-node-label">${escapeHtml(node.label)}</span>
          <span class="sm-node-cat">${escapeHtml(node.category)}</span>
        </div>
        <div class="sm-node-detail">${escapeHtml(node.detail || '')}</div>
        ${node.port ? `<div class="sm-node-port">:${node.port}</div>` : ''}`;
    });
  }

  function renderEdges(map) {
    const rect = nodesEl.getBoundingClientRect();
    const w = rect.width || 800;
    const h = rect.height || 500;
    edgesEl.setAttribute('viewBox', `0 0 ${w} ${h}`);

    const lines = map.edges.map((edge) => {
      const from = nodePos(edge.source);
      const to = nodePos(edge.target);
      const x1 = from.x * w;
      const y1 = from.y * h;
      const x2 = to.x * w;
      const y2 = to.y * h;
      const mx = (x1 + x2) / 2;
      const my = (y1 + y2) / 2;
      const cls = edge.active ? 'active' : '';
      return `
        <line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="${cls}" data-edge="${edge.source}-${edge.target}" />
        <text x="${mx}" y="${my - 4}" text-anchor="middle">${escapeHtml(edge.label)}</text>`;
    });
    edgesEl.innerHTML = lines.join('');
  }

  function renderStats(map) {
    const live = map.live || {};
    const voice = live.voice || {};
    const sec = live.security || {};
    statsEl.innerHTML = `
      <div class="sm-stat"><b>${escapeHtml(voice.label || '—')}</b><span>Voice</span></div>
      <div class="sm-stat"><b>${live.tasks_running || 0}</b><span>Running</span></div>
      <div class="sm-stat"><b>${live.tasks_queued || 0}</b><span>Queued</span></div>
      <div class="sm-stat"><b>${live.approvals_pending || 0}</b><span>Approvals</span></div>
      <div class="sm-stat"><b>${sec.full_access ? 'ON' : 'off'}</b><span>Full access</span></div>
      <div class="sm-stat"><b>${(live.worker || {}).active_jobs || 0}</b><span>Worker jobs</span></div>`;
  }

  function renderTasks(map) {
    const tasks = (map.live || {}).tasks || [];
    if (!tasks.length) {
      tasksEl.innerHTML = '<p class="sm-empty">No active tasks</p>';
      return;
    }
    tasksEl.innerHTML = tasks
      .slice(0, 8)
      .map(
        (t) =>
          `<div class="sm-task"><b>#${t.id}</b> ${escapeHtml(t.title || t.body || 'task')} · <em>${escapeHtml(t.status)}</em></div>`
      )
      .join('');
  }

  function renderCaps(map) {
    capsEl.innerHTML = (map.capabilities || [])
      .map(
        (g) => `
      <div class="sm-cap-group">
        <h3>${escapeHtml(g.title)}</h3>
        <ul>${(g.items || []).map((i) => `<li>${escapeHtml(i)}</li>`).join('')}</ul>
      </div>`
      )
      .join('');
  }

  function applyMap(map) {
    lastMap = map;
    subtitle.textContent = `${map.agent || 'William Agent'} · updated ${formatTime(map.ts)}`;
    tsEl.textContent = formatTime(map.ts);
    renderNodes(map);
    renderEdges(map);
    renderStats(map);
    renderTasks(map);
    if (!capsEl.dataset.loaded) {
      renderCaps(map);
      capsEl.dataset.loaded = '1';
    }
  }

  function prependFeed(event) {
    const empty = feedEl.querySelector('.sm-empty');
    if (empty) empty.remove();
    const item = document.createElement('div');
    item.className = 'sm-feed-item';
    item.innerHTML = `<b>${escapeHtml(event.title || event.kind || 'event')}</b> ${escapeHtml(event.detail || '')}`;
    feedEl.insertBefore(item, feedEl.firstChild);
    while (feedEl.children.length > 12) feedEl.removeChild(feedEl.lastChild);

    if (lastMap && event.kind) {
      const kindEdge = {
        chat: 'user-core',
        task: 'core-worker',
        screen: 'helper-core',
        agent_step: 'core-ollama',
        integration: 'core-memory',
      };
      const key = kindEdge[event.kind];
      if (key) {
        const line = edgesEl.querySelector(`[data-edge="${key}"]`) ||
          edgesEl.querySelector('.active');
        if (line) {
          line.classList.add('active');
          setTimeout(() => line.classList.remove('active'), 2000);
        }
      }
    }
  }

  async function pollMap() {
    try {
      const res = await fetch('/api/system/map');
      if (!res.ok) throw new Error('map fetch failed');
      applyMap(await res.json());
      liveDot?.classList.remove('off');
    } catch {
      liveDot?.classList.add('off');
      subtitle.textContent = 'Backend offline — start JarvisCore on :8787';
    }
  }

  function connectActivity() {
    if (activitySource) activitySource.close();
    activitySource = new EventSource('/api/activity/stream');
    activitySource.onmessage = (msg) => {
      try {
        prependFeed(JSON.parse(msg.data));
      } catch { /* ignore */ }
    };
    activitySource.onerror = () => {
      activitySource.close();
      setTimeout(connectActivity, 4000);
    };
  }

  function onResize() {
    if (lastMap) {
      renderNodes(lastMap);
      renderEdges(lastMap);
    }
  }

  window.addEventListener('resize', onResize);
  pollMap();
  pollTimer = setInterval(pollMap, 2000);
  connectActivity();
})();
