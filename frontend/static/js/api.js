/* api.js — Central API helpers */
const API = '';
let mapConfiguration;
async function addBasemap(map) {
  const control = L.control({position:'bottomleft'});
  const label = document.createElement('div'); label.className = 'map-notice';
  label.textContent = 'Loading basemap…'; control.onAdd = () => label; control.addTo(map);
  mapConfiguration ||= apiFetch('/api/map-config');
  const config = await mapConfiguration;
  if (!config?.tile_url) { label.textContent = 'Basemap not configured · geographic overlays only'; return; }
  const tiles = L.tileLayer(config.tile_url,{attribution:escapeHTML(config.attribution),maxZoom:19,
      referrerPolicy:'strict-origin-when-cross-origin'});
  tiles.on('tileerror',() => { map.removeLayer(tiles); label.textContent = 'Basemap unavailable · geographic overlays only'; });
  tiles.on('load',() => { if (map.hasLayer(tiles)) label.textContent = 'Camera coverage is approximate'; });
  tiles.addTo(map);
}

async function apiFetch(path, opts = {}) {
  try {
    const r = await fetch(API + path, opts);
    if (r.status === 401) { location.href = '/login'; return null; }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch (e) {
    console.error('[API]', path, e);
    return null;
  }
}

async function apiPost(path, body) {
  return apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector(`#tab-${name}`).classList.add('active');
  document.querySelectorAll('.tab').forEach(t => {
    if (t.textContent.toLowerCase().includes(name.replace('-', ' '))) t.classList.add('active');
  });
  // Trigger tab-specific actions
  if (name === 'registry') loadCameras();
  if (name === 'alerts') loadAlerts();
  if (name === 'blockchain') loadLedger();
  if (name === 'aci') loadACI();
  if (name === 'cases') loadCases();
}

function showToast(alert) {
  alert = displayData(alert);
  const container = document.getElementById('toast-container');
  const div = document.createElement('div');
  div.className = `toast ${alert.severity || 'LOW'}`;
  const icon = alert.severity === 'HIGH' ? '🚨' : alert.severity === 'MEDIUM' ? '⚠️' : 'ℹ️';
  const plate = alert.plate_number ? `<div class="plate-badge">${alert.plate_number}</div>` : '';
  div.innerHTML = `
    <div class="toast-icon">${icon}</div>
    <div>
      <div class="toast-title">${alert.alert_type?.replace('_', ' ')} — Risk ${alert.risk_score}/100</div>
      <div class="toast-body">${alert.camera_id} ${plate}</div>
    </div>`;
  container.appendChild(div);
  setTimeout(() => div.remove(), 8000);
}

function riskColor(score) {
  if (score >= 70) return '#FF4444';
  if (score >= 40) return '#FFB800';
  return '#1E90FF';
}


function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function displayData(value) {
  if (typeof value === 'string') return escapeHTML(value);
  if (Array.isArray(value)) return value.map(displayData);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,displayData(v)]));
  return value;
}
