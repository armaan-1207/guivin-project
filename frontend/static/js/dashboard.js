/* dashboard.js — App init, WebSocket, live streams, clock, health */

// ── WebSocket real-time alerts ─────────────────────────────────────────────
let ws = null;
let wsReconnectDelay = 2000;

function connectWebSocket() {
  ws = new WebSocket('ws://localhost:8000/ws');

  ws.onopen = () => {
    console.log('[WS] Connected');
    wsReconnectDelay = 2000;
    const el = document.getElementById('ws-status');
    el.innerHTML = '<i class="fas fa-circle" style="color:#00C853"></i> Live';
    el.title = 'WebSocket connected — real-time alerts active';
    setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 20000);
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
        showToastRaw(`✅ Sentinel connected — ${msg.cameras_started} cameras live (${msg.protocol})`);
      }
    } catch (_) {}
  };

  ws.onclose = () => {
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
  const camId = document.getElementById('stream-cam-id').value.trim() || `DEMO-${Date.now()}`;
  if (!url) { alert('Enter a stream URL'); return; }

  // Detect if user typed an HLS URL directly
  const isHls = url.endsWith('.m3u8') || url.includes('/index.m3u8');
  if (isHls) {
    addStreamCell(camId, url, 'HLS');
    return;
  }

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
  if (activeStreams[cameraId]) return; // already added

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
        <span id="fps-${cameraId}">HLS · AI Active</span>
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
        <span id="health-${cameraId}"><i class="fas fa-circle" style="color:var(--green)"></i> OPERATIONAL</span>
        <span id="fps-${cameraId}">RTSP · AI Active</span>
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
      const displayStatus = (useHls && health.status === 'OFFLINE') ? 'HLS LIVE' : health.status;
      const displayColor  = (useHls && health.status === 'OFFLINE') ? 'var(--green)' : color;
      el.innerHTML = `<i class="fas fa-circle" style="color:${displayColor}"></i> ${displayStatus}`;
      if (health.reason) el.title = health.reason;
    }
  }, 5000);
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
    hls.on(Hls.Events.MEDIA_ATTACHED, onPlay);
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

function removeStreamCell(cameraId) {
  // Destroy hls.js instance if exists
  if (hlsInstances[cameraId]) {
    hlsInstances[cameraId].destroy();
    delete hlsInstances[cameraId];
  }
  const cell = document.getElementById(`stream-cell-${cameraId}`);
  if (cell) cell.remove();
  delete activeStreams[cameraId];
  if (Object.keys(activeStreams).length === 0) {
    document.getElementById('no-streams').style.display = '';
  }
  // Stop backend stream
  apiFetch(`/api/stream/${cameraId}/health`).catch(() => {});
}

// ── Sentinel connect flow ───────────────────────────────────────────────────
async function connectSentinelSandbox() {
  const email = prompt('Enter your cctv.corp8.cloud email:');
  if (!email) return;
  const password = prompt('Enter your Sentinel access password (e.g. XXXX-XXXX-XXXX):');
  if (!password) return;
  const maxCams = parseInt(prompt('How many cameras? (1–30, recommended: 5):', '5') || '5');

  // Always use HLS — port 8554 is usually blocked on restricted networks.
  // The browser already has the cctv.corp8.cloud session cookie → HLS works.
  const useHls = true;

  const params = new URLSearchParams({ email, password, max_cameras: maxCams, use_hls: useHls });
  showToastRaw('⏳ Connecting to Sentinel sandbox...');

  const r = await apiPost(`/api/sentinel/connect?${params}`, {});
  if (r?.connected) {
    r.streams_started.forEach(s => {
      // Remap HLS URL → our local proxy (avoids CORS from cctv.corp8.cloud)
      let streamUrl = s.stream_url;
      if (s.protocol === 'HLS') {
        streamUrl = `/api/proxy/hls/${s.camera_id}/index.m3u8`;
      }
      addStreamCell(s.camera_id, streamUrl, s.protocol);
    });
    loadCameras();
    showToastRaw(`✅ ${r.cameras_fetched} Sentinel cameras live via proxied HLS`);
  } else {
    alert('❌ Connection failed — check email/password at cctv.corp8.cloud and try again.');
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
  const h = await apiFetch('/api/health');
  if (!h) return;
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
window.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
  loadCameras();
  loadAlerts();
  updateClock();
  setInterval(updateClock, 1000);
  setInterval(loadAlerts, 10000);
});
