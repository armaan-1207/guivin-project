/* dashboard.js — App init, WebSocket, live streams, clock, health */

// ── WebSocket real-time alerts ─────────────────────────────────────────────
let ws = null;
let heartbeat = null;
let wsReconnectDelay = 2000;

function connectWebSocket() {
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');

  ws.onopen = () => {
    console.log('[WS] Connected');
    wsReconnectDelay = 2000;
    const el = document.getElementById('ws-status');
    el.innerHTML = '<i class="fas fa-circle" style="color:#00C853"></i> Live';
    el.title = 'WebSocket connected — real-time alerts active';
    clearInterval(heartbeat);
    heartbeat = setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 20000);
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.event === 'new_alert' && msg.alert) {
        showToast(msg.alert);
        loadAlerts();
        updateAlertCounts([msg.alert]);
        const badge = document.getElementById('alert-badge');
        badge.textContent = parseInt(badge.textContent || '0') + 1;
      }
      if (msg.event === 'alert_updated') loadAlerts();
      if (msg.type === 'sentinel_connected') {
        showToastRaw(`Sentinel: ${msg.cameras_started} workers started; ${msg.cameras_failed || 0} failed. Check feed health for readiness.`);
      }
    } catch (_) {}
  };

  ws.onclose = () => {
    clearInterval(heartbeat);
    document.getElementById('ws-status').innerHTML =
      '<i class="fas fa-circle" style="color:#666"></i> Reconnecting...';
    setTimeout(connectWebSocket, wsReconnectDelay);
    wsReconnectDelay = Math.min(wsReconnectDelay * 1.5, 30000);
  };

  ws.onerror = () => ws.close();
}

// ── Stream management ──────────────────────────────────────────────────────
const activeStreams = {};
const hlsInstances = {}; // hls.js instances keyed by cameraId

async function startStream() {
  const url   = document.getElementById('stream-url-input').value.trim();
  const camId = document.getElementById('stream-cam-id').value.trim();
  if (!/^[A-Za-z0-9_-]+$/.test(camId)) { alert('Use letters, digits, underscores or hyphens for Camera ID'); return; }
  if (!url) { alert('Enter a stream URL'); return; }

  // Detect if user typed an HLS URL directly
  const isHls = url.endsWith('.m3u8') || url.includes('/index.m3u8');

  const result = await apiPost('/api/stream/start', {
    camera_id: camId, stream_url: url, department: 'Default'
  });
  if (result?.started) addStreamCell(camId, null, 'RTSP');
}

/**
 * Add a camera cell to the Live Monitor grid.
 *
 * @param {string} cameraId   — camera ID (used for health polling + MJPEG URL)
 * @param {string|null} streamUrl  — HLS URL (https://...) or null for MJPEG
 * @param {string} protocol   — 'HLS' | 'RTSP' | null
 */
function addStreamCell(cameraId, streamUrl = null, protocol = null) {
  if (activeStreams[cameraId]) {
    clearInterval(activeStreams[cameraId].healthTimer);
    if (hlsInstances[cameraId]) {
      hlsInstances[cameraId].destroy();
      delete hlsInstances[cameraId];
    }
    activeStreams[cameraId].cell.remove();
    delete activeStreams[cameraId];
  }

  const grid  = document.getElementById('stream-grid');
  const noStr = document.getElementById('no-streams');
  noStr.style.display = 'none';

  const useHls = protocol === 'HLS' ||
    (streamUrl && (streamUrl.endsWith('.m3u8') || streamUrl.includes('.m3u8')));

  const cell = document.createElement('div');
  cell.className = 'stream-cell';
  cell.id = `stream-cell-${cameraId}`;

  if (useHls && streamUrl) {
    // ── HLS player (browser-native, uses existing cctv.corp8.cloud session)
    cell.innerHTML = `
      <button class="stream-stop" onclick="removeStreamCell('${cameraId}')">✕ Stop</button>
      <div class="stream-label">${cameraId}</div>
      <div style="position:relative;width:100%;background:#0a1628">
        <video id="hls-video-${cameraId}"
               style="width:100%;aspect-ratio:16/9;display:block;object-fit:cover"
               autoplay muted playsinline controls></video>
        <div id="hls-overlay-${cameraId}"
             style="position:absolute;top:6px;right:8px;font-size:10px;
                    background:rgba(0,180,100,0.85);color:#fff;padding:2px 6px;
                    border-radius:3px;display:none">▶ HLS LIVE</div>
      </div>
      <div style="padding:6px 10px;font-size:11px;display:flex;justify-content:space-between;color:var(--text-muted)">
        <span id="health-${cameraId}"><i class="fas fa-circle" style="color:var(--amber)"></i> CONNECTING</span>
        <span id="fps-${cameraId}">HLS · Check analysis status</span>
      </div>`;
    grid.appendChild(cell);
    activeStreams[cameraId] = { cell, protocol: 'HLS', streamUrl };
    _attachHlsPlayer(cameraId, streamUrl);
  } else {
    // ── MJPEG player (streamed through our backend from RTSP)
    cell.innerHTML = `
      <button class="stream-stop" onclick="removeStreamCell('${cameraId}')">✕ Stop</button>
      <div class="stream-label">${cameraId}</div>
      <img src="/api/stream/${cameraId}"
           alt="${cameraId}"
           onerror="this.alt='Stream unavailable'"
           style="width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#0a1628" />
      <div style="padding:6px 10px;font-size:11px;display:flex;justify-content:space-between;color:var(--text-muted)">
        <span id="health-${cameraId}"><i class="fas fa-circle" style="color:var(--amber)"></i> CONNECTING</span>
        <span id="fps-${cameraId}">Capture · Check analysis status</span>
      </div>`;
    grid.appendChild(cell);
    activeStreams[cameraId] = { cell, protocol: 'RTSP' };
  }

  // Poll backend health status every 5 s
  const healthTimer = setInterval(async () => {
    if (!activeStreams[cameraId]) { clearInterval(healthTimer); return; }
    const health = await apiFetch(`/api/stream/${cameraId}/health`);
    const el = document.getElementById(`health-${cameraId}`);
    if (health && el) {
      const color = health.status === 'OPERATIONAL' ? 'var(--green)' :
                    health.status === 'OFFLINE'      ? 'var(--amber)' :
                    health.status === 'TAMPERED'     ? 'var(--red)'   : 'var(--amber)';
      // For HLS streams, OFFLINE in backend is normal (no RTSP) — override display
      const displayStatus = health.status;
      const displayColor = color;
      el.innerHTML = `<i class="fas fa-circle" style="color:${displayColor}"></i> ${displayStatus}`;
      if (health.reason) el.title = health.reason;
      const ai = document.getElementById('fps-' + cameraId);
      if (ai) ai.textContent = 'AI: ' + (health.ai_status || 'NOT_STARTED');
    }
  }, 5000);
  activeStreams[cameraId].healthTimer = healthTimer;
}

function _attachHlsPlayer(cameraId, hlsUrl) {
  const video   = document.getElementById(`hls-video-${cameraId}`);
  const overlay = document.getElementById(`hls-overlay-${cameraId}`);
  if (!video) return;

  const onPlay = () => { if (overlay) overlay.style.display = 'block'; };
  const onErr  = () => {
    if (overlay) { overlay.textContent = '⚠ Stream error'; overlay.style.background = 'rgba(200,50,50,0.85)'; overlay.style.display = 'block'; }
  };

  if (typeof Hls !== 'undefined' && Hls.isSupported()) {
    const hls = new Hls({
      xhrSetup: (xhr, url) => {
        // Only include credentials if connecting directly to external cctv.corp8.cloud
        if (url && url.includes('cctv.corp8.cloud')) {
          xhr.withCredentials = true;
        }
      },
      liveSyncDurationCount: 3,
      liveMaxLatencyDurationCount: 5,
    });
    hls.loadSource(hlsUrl);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => {}); });
    video.addEventListener('playing', onPlay);
    hls.on(Hls.Events.ERROR, (_, data) => {
      if (data.fatal) { console.warn(`[HLS] Fatal error for ${cameraId}:`, data); onErr(); }
    });
    hlsInstances[cameraId] = hls;
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari native HLS
    video.src = hlsUrl;
    video.addEventListener('loadedmetadata', () => video.play().catch(() => {}));
    video.addEventListener('playing', onPlay);
    video.addEventListener('error', onErr);
  } else {
    onErr();
    console.warn('[HLS] HLS not supported in this browser');
  }
}

async function removeStreamCell(cameraId) {
  const entry = activeStreams[cameraId];
  if (!entry || entry.stopping) return;
  entry.stopping = true;
  const button = entry.cell.querySelector('.stream-stop');
  button.disabled = true;
  button.textContent = 'Stopping…';
  const result = await apiPost(`/api/stream/${cameraId}/stop`, {});
  if (!result) {
    entry.stopping = false; button.disabled = false; button.textContent = 'Retry Stop';
    showToastRaw('Stop request failed. Please retry.'); return;
  }
  if (!result.stopped) {
    const healthLabel = document.getElementById(`health-${cameraId}`);
    if (healthLabel) healthLabel.textContent = 'STOPPING — waiting for the current operation';
    let stopped = false;
    for (let attempt = 0; attempt < 30; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const health = await apiFetch(`/api/stream/${cameraId}/health`);
      if (health?.status === 'NOT_STREAMING') { stopped = true; break; }
    }
    if (!stopped) {
      entry.stopping = false; button.disabled = false; button.textContent = 'Check Stop';
      showToastRaw('Stop requested; the worker is still finishing. Check Stop to confirm.'); return;
    }
  }
  // Destroy hls.js instance if exists
  if (hlsInstances[cameraId]) {
    hlsInstances[cameraId].destroy();
    delete hlsInstances[cameraId];
  }
  const cell = document.getElementById(`stream-cell-${cameraId}`);
  if (cell) cell.remove();
  clearInterval(activeStreams[cameraId]?.healthTimer);
  delete activeStreams[cameraId];
  if (Object.keys(activeStreams).length === 0) {
    document.getElementById('no-streams').style.display = '';
  }
  // Stop backend stream

}

// ── Sentinel connect flow ───────────────────────────────────────────────────
let sentinelConnecting = false;
function openSentinelForm() {
  document.getElementById('sentinel-form').hidden = false;
  document.getElementById('sentinel-email').focus();
}
function closeSentinelForm() {
  if (sentinelConnecting) return;
  document.getElementById('sentinel-password').value = '';
  document.getElementById('sentinel-form').hidden = true;
  document.querySelector('[aria-controls="sentinel-form"]').focus();
}
async function connectSentinelSandbox(event) {
  event.preventDefault();
  const form = document.getElementById('sentinel-form');
  if (sentinelConnecting || !form.reportValidity()) return;
  const status = document.getElementById('sentinel-status');
  const passwordInput = document.getElementById('sentinel-password');
  const payload = {email: document.getElementById('sentinel-email').value.trim(),
    password: passwordInput.value, max_cameras: Number(document.getElementById('sentinel-count').value), use_hls: false};
  sentinelConnecting = true;
  document.getElementById('sentinel-submit').disabled = true;
  document.getElementById('sentinel-cancel').disabled = true;
  form.setAttribute('aria-busy', 'true');
  status.textContent = 'Connecting to Camera Grid…';
  try {
    // Backend establishes its own session; browser portal cookies are not reused.
    const response = await fetch('/api/sentinel/connect', {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const r = await response.json().catch(() => ({}));
    const started = Array.isArray(r.streams_started) ? r.streams_started : [];
    const failed = Array.isArray(r.streams_failed) ? r.streams_failed : [];
    if (!response.ok || (!r.connected && !failed.length)) {
      const messages = {401:'Connection rejected. Check your Camera Grid credentials and GUIVIN login session.',
        403:'Your GUIVIN account is not permitted to connect these cameras.',
        422:'Check the email, password and camera count.',
        502:'Camera Grid catalogue is unavailable. Try again shortly.',
        503:'GUIVIN cannot reach Camera Grid. Check the server network connection and try again.'};
      status.textContent = messages[response.status] || 'Connection failed. Please try again.';
      return;
    }
    const failures = failed.map(s => `${s.camera_id}: ${s.reason === 'PREVIOUS_WORKER_STOPPING'
      ? 'previous worker is still stopping; wait until stopped, then retry'
      : 'worker could not start; check camera health and retry'}`).join('; ');
    status.textContent = `${r.cameras_registered} cameras registered; ${started.length} workers started; ${failed.length} failed. Check feed health for readiness.${failures ? ' ' + failures + '.' : ''}`;
    started.forEach(s => {
      // Remap HLS URL → our local proxy (avoids CORS from cctv.corp8.cloud)
      let streamUrl = s.stream_url;
      if (s.protocol === 'HLS') {
        streamUrl = `/api/proxy/hls/${s.camera_id}/index.m3u8`;
      }
      addStreamCell(s.camera_id, streamUrl, s.protocol);
    });
    loadCameras();
  } catch (_) {
    status.textContent = 'Could not finish connecting. Check the connection and Live Monitor before retrying.';
  } finally {
    passwordInput.value = '';
    payload.password = '';
    sentinelConnecting = false;
    document.getElementById('sentinel-submit').disabled = false;
    document.getElementById('sentinel-cancel').disabled = false;
    form.removeAttribute('aria-busy');
  }
}

// ── Toast helper ───────────────────────────────────────────────────────────
function showToastRaw(message) {
  const c = document.getElementById('toast-container');
  if (!c) return;
  const t = document.createElement('div');
  t.className = 'toast toast-info';
  t.textContent = message;
  c.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ── Health report ──────────────────────────────────────────────────────────
async function loadHealthReport() {
  let h = await apiFetch('/api/health');
  if (!h) return;
  h = displayData(h);
  document.getElementById('health-report').innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
      <div class="dept-stat-row"><span>Status</span><strong style="color:var(--green)">${h.status.toUpperCase()}</strong></div>
      <div class="dept-stat-row"><span>Cameras Registered</span><strong>${h.cameras_registered}</strong></div>
      <div class="dept-stat-row"><span>Total Alerts</span><strong>${h.alerts_total}</strong></div>
      <div class="dept-stat-row"><span>Watchlist Entries</span><strong>${h.watchlist_entries}</strong></div>
      <div class="dept-stat-row"><span>Blockchain Blocks</span><strong>${h.blockchain_blocks}</strong></div>
      <div class="dept-stat-row"><span>Active Streams</span><strong>${h.active_streams}</strong></div>
    </div>`;
}

// ── Clock ──────────────────────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ── App init ───────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  window.currentUser = await apiFetch('/api/auth/me');
  if (!window.currentUser) return;
  const me = window.currentUser;
  document.getElementById('mode-label').textContent = me.mode === 'local-only' ? 'Local-only development · representative watchlists' : `${me.username} · ${me.role} · ${me.department}`;
  const restricted = {judiciary:['cases'], auditor:['alerts','blockchain'], technical_admin:['reports']};
  const allowed = restricted[userRole()];
  if (allowed) {
    document.querySelectorAll('.tab').forEach(tab => { tab.hidden = !allowed.some(name => tab.getAttribute('onclick')?.includes("'" + name + "'")); });
    document.getElementById('ws-status').textContent = 'Read-only access';
    if (userRole() === 'technical_admin') {
      document.querySelectorAll('.report-card').forEach(card => { card.hidden = !card.querySelector('#health-report'); });
      loadHealthReport();
    }
    switchTab(allowed[0]);
  } else {
    connectWebSocket(); loadCameras(); loadAlerts();
    apiFetch('/api/streams/active').then(data => data?.active?.forEach(id => addStreamCell(id)));
    apiFetch('/api/cameras').then(cameras => {
      if (!cameras) return;
      const select = document.getElementById('stream-cam-id');
      cameras.forEach(cam => { const option = document.createElement('option'); option.value = cam.id; option.textContent = cam.name + ' (' + cam.id + ')'; select.appendChild(option); });
    });
  }
  updateClock(); setInterval(updateClock, 1000);
  if (!['judiciary','technical_admin'].includes(userRole())) setInterval(loadAlerts, 10000);
});
