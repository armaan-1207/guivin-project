/* blockchain.js — Blockchain ledger and evidence verification */

async function loadLedger() {
  let data = await apiFetch('/api/blockchain/ledger?limit=30');
  if (!data) return;
  data = displayData(data);
  const el = document.getElementById('ledger-list');

  if (!data.length) {
    el.innerHTML = '<div class="empty-state"><i class="fas fa-cube fa-2x"></i><p>No blocks yet. Trigger an alert to anchor evidence.</p></div>';
    return;
  }

  el.innerHTML = data.map(b => `
    <div class="block-row">
      <div class="block-num">#${b.block_number}</div>
      <div style="flex:1">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
          <span style="font-size:12px;font-weight:600;color:var(--accent-blue)">${b.alert_id}</span>
          <span style="font-size:11px;color:var(--text-muted)">${new Date(b.anchored_at).toLocaleTimeString('en-IN')}</span>
        </div>
        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:2px">
          Camera: ${b.camera_id} · Dept: ${b.department} · Risk: ${b.risk_score}
        </div>
        <div style="font-size:11px;color:var(--text-muted)">TX: <code>${b.tx_id}</code></div>
        <div style="font-size:10px;margin-top:4px">
          <span style="color:var(--text-muted)">Hash: </span>
          <code class="block-hash">${b.clip_hash}</code>
        </div>
        <div style="font-size:10px;color:var(--text-muted);margin-top:2px">
          Prev: <code>${b.previous_hash.substring(0, 32)}...</code>
        </div>
        <div style="margin-top:6px">
          <span class="tag" style="font-size:10px">Channel: ${b.channel}</span>
          <span class="tag" style="font-size:10px;margin-left:4px">${b.org}</span>
          <button class="btn-sm btn-primary" style="margin-left:8px;font-size:10px" onclick="verifyBlockById('${b.alert_id}')">
            <i class="fas fa-shield-check"></i> Verify
          </button>
        </div>
      </div>
    </div>`).join('');
}

async function verifyEvidence() {
  const alertId = document.getElementById('verify-alert-id').value.trim();
  if (!alertId) { alert('Enter an Alert ID'); return; }

  let result = await apiPost('/api/blockchain/verify', { alert_id: alertId });
  const el = document.getElementById('verify-result');
  if (!result) { el.innerHTML = '<div class="verify-fail">Failed to connect to blockchain service.</div>'; return; }

  result = displayData(result);
  if (result.verified) {
    el.innerHTML = `
      <div class="verify-ok">
        <div style="font-size:16px;font-weight:700;margin-bottom:8px"><i class="fas fa-circle-check"></i> EVIDENCE BYTES VERIFIED ✓</div>
        <div class="verify-row">Chain Status: <span style="font-weight:700">${result.chain_status}</span></div>
        <div class="verify-row">Alert ID: <span>${result.alert_id}</span></div>
        <div class="verify-row">Block: <span>#${result.block_number}</span></div>
        <div class="verify-row">Transaction: <span>${result.tx_id}</span></div>
        <div class="verify-row">Anchored At: <span>${new Date(result.anchored_at).toLocaleString('en-IN')}</span></div>
        <div class="verify-row">Stored Hash: <span>${result.stored_hash}</span></div>
        <div class="verify-row">Current Hash: <span>${result.current_hash}</span></div>
        <div class="verify-row">Channel: <span>${result.channel}</span></div>
        <div class="verify-row">Organisation: <span>${result.org}</span></div>
        <div style="margin-top:10px;font-size:11px;color:rgba(0,200,83,.8)">
          ${result.trust_scope}
          Verified at ${new Date(result.verification_time).toLocaleTimeString('en-IN')}.
        </div>
      </div>`;
  } else {
    el.innerHTML = `
      <div class="verify-fail">
        <div style="font-size:16px;font-weight:700;margin-bottom:8px"><i class="fas fa-circle-xmark"></i> VERIFICATION FAILED ✗</div>
        <div class="verify-row">Chain Status: <span style="font-weight:700">${result.chain_status || 'UNKNOWN'}</span></div>
        <div class="verify-row">Reason: <span>${result.reason || 'Hash mismatch — possible tampering'}</span></div>
        ${result.stored_hash ? `<div class="verify-row">Stored Hash: <span>${result.stored_hash}</span></div>` : ''}
        ${result.current_hash ? `<div class="verify-row">Current Hash: <span>${result.current_hash}</span></div>` : ''}
      </div>`;
  }
}

function verifyBlockById(alertId) {
  document.getElementById('verify-alert-id').value = alertId;
  verifyEvidence();
  // Scroll to verify result
  document.getElementById('verify-result').scrollIntoView({ behavior: 'smooth' });
}
