/* gis-map.js — Leaflet GIS Registry Map */
let gisMap = null;
let allCameras = [];
let markers = [];
let coverageLayer = null;

const DEPT_COLORS = {
  'Home Department':       '#1E90FF',
  'RTO Gujarat':           '#00C853',
  'Food & Civil Supplies': '#FFB800',
  'Default':               '#8b5cf6',
};

function deptColor(dept) {
  return DEPT_COLORS[dept] || DEPT_COLORS['Default'];
}

function initGISMap() {
  if (gisMap) return;
  gisMap = L.map('gis-map', { center: [22.96, 72.60], zoom: 9 });

  addBasemap(gisMap);
  coverageLayer = L.layerGroup().addTo(gisMap);
}

async function loadCameras() {
  initGISMap();
  const geoData = await apiFetch('/api/cameras/geojson');
  const stats   = await apiFetch('/api/cameras/stats');
  if (!geoData) return;

  allCameras = geoData.features.map(f => ({ ...f.properties, lat: f.geometry?.coordinates[1] ?? null, lon: f.geometry?.coordinates[0] ?? null }));

  // Update counts
  document.getElementById('cam-count').textContent = geoData.metadata?.total || allCameras.length;
  document.getElementById('cam-inventory-count').textContent = `${geoData.metadata?.online || 0} online / ${allCameras.length} total`;

  filterCameras();
  if (stats) renderDeptStats(stats, allCameras.length);
}

function renderMarkers(cameras) {
  coverageLayer.clearLayers();
  markers.forEach(m => gisMap.removeLayer(m));
  markers = [];
  cameras.forEach(cam => {
    if (!Number.isFinite(cam.lat) || !Number.isFinite(cam.lon)) return;
    const color = deptColor(cam.department);
    if (document.getElementById('gis-coverage').checked && Number(cam.coverage_radius_m) > 0) {
      L.circle([cam.lat,cam.lon],{radius:Number(cam.coverage_radius_m),color,fillOpacity:.08,weight:1})
        .bindTooltip('Declared radius only; terrain and field of view are not verified').addTo(coverageLayer);
    }
    const size  = cam.health === 'OFFLINE' ? 10 : (cam.anpr ? 14 : 12);

    const icon = L.divIcon({
      html: `<div style="
        width:${size}px;height:${size}px;border-radius:50%;
        background:${cam.health === 'OFFLINE' ? '#FF4444' : color};
        border:2px solid rgba(255,255,255,.4);
        box-shadow:0 0 8px ${color}88;
        ${cam.anpr ? 'outline: 2px solid rgba(255,255,255,.3);outline-offset:2px;' : ''}
      "></div>`,
      iconSize: [size, size],
      iconAnchor: [size/2, size/2],
      className: '',
    });

    const marker = L.marker([cam.lat, cam.lon], { icon })
      .addTo(gisMap)
      .bindPopup(buildPopup(cam));

    marker.on('click', () => highlightCameraInList(cam.id));
    markers.push(marker);
  });
}

function buildPopup(cam) {
  cam = displayData(cam);
  const healthClass = cam.health === 'OPERATIONAL' ? 'health-ok' : cam.health === 'DEGRADED' ? 'health-deg' : 'health-off';
  const anprBadge   = cam.anpr ? '<span class="tag" style="background:#0a2040;border-color:#1E90FF;color:#1E90FF">ANPR</span>' : '';
  const nightBadge  = cam.night ? '<span class="tag" style="margin-left:4px">Night</span>' : '';
  return `
    <div style="min-width:200px">
      <div class="popup-title">${cam.name}</div>
      <div class="popup-row"><span class="popup-label">ID:</span> <code style="font-size:10px">${cam.id}</code></div>
      <div class="popup-row"><span class="popup-label">Dept:</span> ${cam.department}</div>
      <div class="popup-row"><span class="popup-label">District:</span> ${cam.district}</div>
      <div class="popup-row"><span class="popup-label">Protocol:</span> ${cam.protocol}</div>
      <div class="popup-row"><span class="popup-label">Resolution:</span> ${cam.resolution}</div>
      <div class="popup-row"><span class="popup-label">Health:</span> <span class="${healthClass}">${cam.health}</span></div>
      <div style="margin-top:6px">${anprBadge}${nightBadge}</div>
      <div style="margin-top:8px;font-size:10px;color:#4a7aaa;word-break:break-all">🔒 ${cam.onboarded}</div>
    </div>`;
}

function renderCameraList(cameras) {
  cameras = displayData(cameras);
  const el = document.getElementById('camera-list');
  el.innerHTML = cameras.map(cam => `
    <div class="camera-item" ${cam.location_known === false ? '' : `onclick="focusCamera('${cam.id}','${cam.lat}','${cam.lon}')"`}>
      <div class="cam-dot ${cam.health === 'OPERATIONAL' ? 'online' : cam.health === 'DEGRADED' ? 'degraded' : 'offline'}"></div>
      <div>
        <div class="cam-name">${cam.name}</div>
        <div class="cam-sub">${cam.department} · ${cam.location_known === false ? 'Location unverified' : cam.district}</div>
      </div>
    </div>`).join('');
}

function renderDeptStats(stats, total) {
  stats = displayData(stats);
  const el = document.getElementById('dept-stats');
  el.innerHTML = stats.map(s => {
    const pct = total > 0 ? (s.total / total * 100) : 0;
    const color = deptColor(s.department);
    return `
      <div class="dept-stat-row">
        <div style="min-width:130px;font-size:11px">${s.department}</div>
        <div class="dept-bar-wrap"><div class="dept-bar" style="width:${pct}%;background:${color}"></div></div>
        <div style="min-width:40px;text-align:right;font-size:11px;color:${color}">${s.total}</div>
      </div>`;
  }).join('');
}

function focusCamera(id, lat, lon) {
  gisMap.flyTo([lat, lon], 15, { duration: 1.2 });
  const m = markers.find(mk => mk.options?.title === id);
}

function filterCameras() {
  const dept = document.getElementById('dept-filter').value;
  const health = document.getElementById('gis-health').value;
  const filtered = allCameras.filter(c => (!dept || c.department === dept) && (!health || c.health === health));
  renderMarkers(filtered);
  renderCameraList(filtered);
}

function highlightCameraInList(id) {}

async function onboardCamera() {
  const data = {
    id: document.getElementById('ob-id').value.trim(),
    name: document.getElementById('ob-name').value.trim(),
    department: document.getElementById('ob-dept').value,
    district: document.getElementById('ob-district').value.trim(),
    lat: parseFloat(document.getElementById('ob-lat').value),
    lon: parseFloat(document.getElementById('ob-lon').value),
    address: document.getElementById('ob-address').value.trim(),
    stream_url: document.getElementById('ob-url').value.trim(),
    sub_type: 'Surveillance',
  };
  if (!data.id || !data.name || !data.district || isNaN(data.lat)) {
    alert('Please fill in ID, Name, District and Lat/Lon'); return;
  }
  const result = await apiPost('/api/cameras', data);
  if (result) {
    alert(`Camera ${data.id} onboarded successfully`);
    loadCameras();
  }
}
