/* journey.js — Cross-camera vehicle journey reconstruction */
let journeyMap = null;
let journeyLayer = null;
let journeyOffset = 0;
let journeyQuery = null;

function initJourneyMap() {
  if (journeyMap) return;
  journeyMap = L.map('journey-map', { center: [23.02, 72.57], zoom: 12 });
  addBasemap(journeyMap);
  journeyLayer = L.layerGroup().addTo(journeyMap);
}

async function searchJourney(offset = 0) {
  const plate = document.getElementById('journey-plate').value.trim();
  if (!plate) { alert('Enter a plate number'); return; }

  initJourneyMap();
  if (offset === 0) journeyQuery = {plate, start:document.getElementById('journey-start').value, end:document.getElementById('journey-end').value};
  const query = new URLSearchParams({limit:50,offset});
  for (const name of ['start','end']) if (journeyQuery[name]) query.set(name, new Date(journeyQuery[name]).toISOString());
  let data;
  try { data = await operation(`/api/journey/${encodeURIComponent(journeyQuery.plate)}?${query}`); }
  catch (e) { document.getElementById('journey-page').textContent = e.message; return; }
  journeyOffset = offset;
  document.getElementById('journey-prev').disabled = offset === 0;
  document.getElementById('journey-next').disabled = !data.has_more;
  document.getElementById('journey-page').textContent = `Page ${Math.floor(offset / 50) + 1} · up to 50 sightings · travel checks apply within this page`;

  journeyLayer.clearLayers();
  document.getElementById('journey-summary').innerHTML = '';

  if (!data.journey?.length) {
    document.getElementById('journey-timeline').innerHTML =
      `<div class="empty-state"><i class="fas fa-search fa-2x"></i><p>No sightings found for <strong>${escapeHTML(plate)}</strong>.<br>Use "Load Demo Data" to seed test journey data.</p></div>`;
    return;
  }

  renderJourneyOnMap(data.journey, data.plate);
  renderJourneyTimeline(data.journey, data.plate);
  renderJourneySummary(data);
}

function renderJourneyOnMap(journey, plate) {
  journey = displayData(journey);
  const latlngs = [];

  journey.forEach((sighting, i) => {
    if (!sighting.lat || !sighting.lon) return;
    const ll = [sighting.lat, sighting.lon];
    latlngs.push(ll);

    // Camera marker
    const isFirst = i === 0;
    const isLast  = i === journey.length - 1;
    const color   = isFirst ? '#00C853' : isLast ? '#FF4444' : '#1E90FF';

    const icon = L.divIcon({
      html: `<div style="
        width:24px;height:24px;border-radius:50%;
        background:${color};border:3px solid white;
        display:flex;align-items:center;justify-content:center;
        font-size:11px;font-weight:700;color:white;
        box-shadow:0 0 12px ${color}88;
      ">${i + 1}</div>`,
      iconSize: [24, 24], iconAnchor: [12, 12], className: '',
    });

    const ts = new Date(sighting.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    L.marker(ll, { icon })
      .addTo(journeyLayer)
      .bindPopup(`
        <div class="popup-title">Camera ${i + 1} of ${journey.length}</div>
        <div class="popup-row"><span class="popup-label">Camera:</span> ${sighting.camera_name || sighting.camera_id}</div>
        <div class="popup-row"><span class="popup-label">Dept:</span> ${sighting.department}</div>
        <div class="popup-row"><span class="popup-label">Time:</span> <strong>${ts}</strong></div>
        <div class="popup-row"><span class="popup-label">Plate:</span> <span class="plate-badge">${sighting.plate_number}</span></div>
        <div class="popup-row"><span class="popup-label">Conf:</span> ${(sighting.plate_confidence * 100).toFixed(1)}%</div>
        <div class="popup-row"><span class="popup-label">Address:</span> ${sighting.address || '—'}</div>
      `);
  });

  // Draw route polyline
  if (latlngs.length > 1) {
    L.polyline(latlngs, {
      color: '#1E90FF',
      weight: 3,
      opacity: 0.8,
      dashArray: '8, 6',
    }).addTo(journeyLayer);

    // Add animated arrow every segment
    latlngs.forEach((ll, i) => {
      if (i < latlngs.length - 1) {
        const mid = [(ll[0] + latlngs[i+1][0])/2, (ll[1] + latlngs[i+1][1])/2];
        L.marker(mid, {
          icon: L.divIcon({
            html: '→',
            className: '',
            iconSize: [16, 16],
            style: 'color:#1E90FF;font-size:14px;font-weight:bold',
          }),
        }).addTo(journeyLayer);
      }
    });
  }

  // Fit map to journey
  if (latlngs.length > 0) {
    journeyMap.fitBounds(L.latLngBounds(latlngs), { padding: [40, 40] });
  }
}

function renderJourneyTimeline(journey, plate) {
  journey = displayData(journey); plate = escapeHTML(plate);
  const el = document.getElementById('journey-timeline');
  el.innerHTML = `<div style="padding:10px 12px;font-size:12px;color:var(--text-muted);font-weight:600;border-bottom:1px solid var(--border)">
    Journey: <span class="plate-badge">${plate}</span> · ${journey.length} sightings
  </div>` + journey.map((s, i) => {
    const ts = new Date(s.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const isFirst = i === 0;
    const isLast  = i === journey.length - 1;
    const dotColor = isFirst ? '#00C853' : isLast ? '#FF4444' : '#1E90FF';

    return `
      <div class="journey-event">
        <div class="journey-dot" style="background:${dotColor}"></div>
        <div>
          <div class="journey-time">${ts}</div>
          <div class="journey-cam">${s.camera_name || s.camera_id}</div>
          <div class="journey-sub">${s.department} · ${s.district || ''}</div>
          <div style="font-size:11px;color:var(--text-muted)">${s.address || ''}</div>
          <div style="margin-top:4px">
            <span class="plate-badge">${s.plate_number}</span>
            <span style="font-size:11px;color:var(--text-muted);margin-left:6px">Conf: ${(s.plate_confidence*100).toFixed(1)}%</span>
          </div>
          <div style="font-size:12px;margin-top:6px;color:var(--text-secondary)">Travel check: ${s.correlation?.status?.replace(/_/g, ' ') || 'UNASSESSED'} · identity unconfirmed</div>
          ${s.alert_id ? `<div style="font-size:11px;color:var(--accent-blue);margin-top:2px"><i class="fas fa-bell"></i> Alert: ${s.alert_id}</div>` : ''}
        </div>
      </div>`;
  }).join('');
}

function renderJourneySummary(data) {
  data = displayData(data);
  if (!data.journey?.length) return;
  const first = data.journey[0];
  const last  = data.journey[data.journey.length - 1];
  const start = new Date(first.timestamp);
  const end   = new Date(last.timestamp);
  const dur   = Math.round((end - start) / 60000);

  document.getElementById('journey-summary').innerHTML = `
    <div class="panel" style="padding:12px"><p>Observed camera sequence; lines are not verified road routes. Travel checks do not confirm identity.</p>
      <div style="display:flex;gap:24px;flex-wrap:wrap">
        <div style="text-align:center"><div style="font-size:28px;font-weight:800;color:var(--accent-blue)">${data.total_sightings}</div><div style="font-size:11px;color:var(--text-muted)">Camera Sightings</div></div>
        <div style="text-align:center"><div style="font-size:28px;font-weight:800;color:var(--accent-blue)">${dur}</div><div style="font-size:11px;color:var(--text-muted)">Minutes Total</div></div>
        <div><div style="font-size:12px;color:var(--text-muted)">First seen</div><div style="font-size:13px;font-weight:600">${first.camera_name || first.camera_id} · ${start.toLocaleTimeString('en-IN')}</div></div>
        <div><div style="font-size:12px;color:var(--text-muted)">Last seen</div><div style="font-size:13px;font-weight:600">${last.camera_name || last.camera_id} · ${end.toLocaleTimeString('en-IN')}</div></div>
      </div>
    </div>`;
}

async function seedDemoJourney() {
  const r = await apiPost('/api/journey/seed', {});
  if (r?.seeded) {
    alert(`Demo journey seeded for plate ${r.plate} across ${r.cameras.length} cameras.\nClick "Reconstruct Journey" now.`);
    document.getElementById('journey-plate').value = r.plate;
  }
}
