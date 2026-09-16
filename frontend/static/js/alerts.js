/* alerts.js — Alert feed, ANPR demo trigger, alert detail panel */
let activeAlerts = [];

async function loadAlerts() {
  const sev = document.getElementById('alert-filter')?.value || '';
  const url = '/api/alerts?limit=100' + (sev ? `&severity=${sev}` : '');
  const data = await apiFetch(url);
  if (!data) return;
  activeAlerts = data;
  renderAlertList(data);
  updateAlertCounts(data);
}

function updateAlertCounts(alerts) {
  const high = alerts.filter(a => a.severity === 'HIGH').length;
  const med  = alerts.filter(a => a.severity === 'MEDIUM').length;
  document.getElementById('alert-high-count').textContent = high;
  document.getElementById('alert-med-count').textContent  = med;
  document.getElementById('alert-badge').textContent = alerts.filter(a => a.status === 'NEW').length;
}

function renderAlertList(alerts) {
  const el = document.getElementById('alert-list');
  if (!alerts.length) {
    el.innerHTML = '<div class="empty-state"><i class="fas fa-shield-check fa-2x"></i><p>No alerts. System monitoring normally.</p></div>';
    return;
  }
  el.innerHTML = alerts.map(a => renderAlertCard(a)).join('');
}

function renderAlertCard(a) {
  const ts = new Date(a.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const plate = a.plate_number ? `<span class="plate-badge">${a.plate_number}</span>` : '';
  const chainBadge = a.block_id ? `<span class="chain-badge"><i class="fas fa-link"></i> Block #${a.block_id}</span>` : '';
  const reasons = a.reason_codes?.map(r =>
    `<li>[+${r.delta || ''}] ${r.factor}: ${r.reason?.substring(0,60)}...</li>`
  ).join('') || '';

  return `
    <div class="alert-card ${a.severity}" onclick="showAlertDetail('${a.id}')">
      <div class="alert-top">
        <span class="alert-id">${a.id}</span>
        <span class="sev-badge ${a.severity}">${a.severity}</span>
      </div>
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">
        ${a.alert_type?.replace(/_/g,' ')} · ${a.camera_id} · ${ts}
      </div>
      ${plate}
      <div class="risk-bar"><div class="risk-fill" style="width:${a.risk_score}%;background:${riskColor(a.risk_score)}"></div></div>
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">Risk Score: <strong style="color:${riskColor(a.risk_score)}">${a.risk_score}/100</strong></div>
      <ul class="reason-list">${reasons}</ul>
      ${chainBadge}
      <div style="font-size:11px;color:var(--text-muted);margin-top:4px">Status: <strong>${a.status}</strong></div>
    </div>`;
}

async function showAlertDetail(alertId) {
  const alert = activeAlerts.find(a => a.id === alertId);
  if (!alert) return;

  const panel = document.getElementById('alert-detail-panel');
  const content = document.getElementById('alert-detail-content');
  panel.style.display = 'block';

  const reasons = alert.reason_codes?.map(r =>
    `<div style="display:flex;justify-content:space-between;padding:5px 8px;background:var(--bg-secondary);border-radius:4px;margin:3px 0">
      <span style="font-size:12px">${r.factor}</span>
      <strong style="color:${riskColor(alert.risk_score)};font-size:12px">+${r.delta}</strong>
    </div>
    <div style="font-size:11px;color:var(--text-muted);padding:0 8px 4px">${r.reason}</div>`
  ).join('') || '';

  content.innerHTML = `
    <div style="padding:12px;display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between">
        <div class="alert-id">${alert.id}</div>
        <div class="sev-badge ${alert.severity}">${alert.severity}</div>
      </div>
      <div style="font-size:12px;color:var(--text-muted)">${alert.alert_type?.replace(/_/g,' ')} · ${alert.camera_id}</div>
      ${alert.plate_number ? `<div><span class="plate-badge">${alert.plate_number}</span></div>` : ''}
      
      <div style="background:var(--bg-card);border-radius:6px;padding:8px">
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px">Explainability Breakdown</div>
        ${reasons}
        <div style="display:flex;justify-content:space-between;border-top:1px solid var(--border);padding-top:6px;margin-top:6px">
          <strong>Total Risk Score</strong>
          <strong style="color:${riskColor(alert.risk_score)};font-size:16px">${alert.risk_score}/100</strong>
        </div>
      </div>

      ${alert.block_id ? `
      <div style="background:rgba(0,200,83,.05);border:1px solid var(--green);border-radius:6px;padding:8px">
        <div style="color:var(--green);font-weight:600;font-size:12px;margin-bottom:4px"><i class="fas fa-link"></i> Blockchain Evidence</div>
        <div style="font-size:11px;font-family:monospace;color:var(--text-secondary)">Block: #${alert.block_id}</div>
        <div style="font-size:11px;font-family:monospace;color:var(--text-secondary);word-break:break-all">Hash: ${alert.clip_hash || '—'}</div>
        <div style="font-size:11px;font-family:monospace;color:var(--text-secondary)">TX: ${alert.blockchain_tx || '—'}</div>
        <button class="btn-sm btn-primary" style="margin-top:8px" onclick="verifyFromAlertId('${alert.id}')">
          <i class="fas fa-shield-check"></i> Verify on Blockchain
        </button>
      </div>` : ''}

      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="acknowledgeAlert('${alert.id}','ACKNOWLEDGED')">Acknowledge</button>
        <button class="btn btn-accent"  onclick="acknowledgeAlert('${alert.id}','VERIFIED')">Mark Verified</button>
        <button class="btn" style="background:var(--bg-secondary);color:var(--text-muted);border:1px solid var(--border)" onclick="acknowledgeAlert('${alert.id}','FALSE_ALARM')">False Alarm</button>
      </div>
    </div>`;
}

async function acknowledgeAlert(alertId, action) {
  const result = await apiPost(`/api/alerts/${alertId}/acknowledge`, {
    operator: 'Operator-1',
    action: action
  });
  if (result?.success) {
    showToast({ severity: 'LOW', alert_type: action, camera_id: alertId, risk_score: 0, plate_number: '' });
    loadAlerts();
  }
}

async function triggerANPRDemo() {
  const cameraId  = document.getElementById('demo-cam').value;
  const plate     = document.getElementById('demo-plate').value.trim();
  const confidence= parseFloat(document.getElementById('demo-conf').value);
  const dwell     = parseFloat(document.getElementById('demo-dwell').value);

  if (!plate) { alert('Enter a plate number'); return; }

  const result = await apiPost('/api/alerts/demo/anpr', {
    camera_id: cameraId,
    plate_number: plate,
    confidence: confidence,
    object_class: 'car',
    dwell_seconds: dwell,
  });

  if (result?.suppressed) {
    alert('Alert suppressed (duplicate within 30-second dedup window). Wait 30s and try again.');
    return;
  }
  if (result?.alert) {
    loadAlerts();
    setTimeout(() => showAlertDetail(result.alert.id), 400);
  }
}

function verifyFromAlertId(alertId) {
  document.querySelector('.tab[onclick*="blockchain"]')?.click();
  switchTab('blockchain');
  document.getElementById('verify-alert-id').value = alertId;
  setTimeout(() => verifyEvidence(), 300);
}
