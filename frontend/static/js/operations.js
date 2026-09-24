/* Operator workflows. Text nodes and bound listeners avoid interpolated handlers. */
function userRole() { const r = window.currentUser?.role; return ({admin:'scrb_admin',operator:'field_operator'})[r] || r; }
function canManageCases() { return ['scrb_admin','department_head','sector_supervisor'].includes(userRole()); }
function node(tag, text, parent, cls) {
  const el = document.createElement(tag); if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls; if (parent) parent.appendChild(el); return el;
}
function action(parent, text, callback) {
  const button = node('button', text, parent, 'btn-sm btn-primary'); button.type = 'button';
  button.addEventListener('click', async () => { button.disabled = true; try { await callback(); } finally { button.disabled = false; } }); return button;
}
async function operation(path, method = 'GET', body) {
  const r = await fetch(path, {method, headers: {'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sign in required'); }
  const data = await r.json();
  if (!r.ok) {
    const detail = data.detail;
    let text = typeof detail === 'string' ? detail : detail?.reason || 'Request could not be completed.';
    if (detail?.required_hours) text += ` ${detail.covered_hours || 0} of ${detail.required_hours} required hours have sufficient observations. Keep live analysis running before trying again.`;
    if (Array.isArray(detail)) text = detail.map(item => `${item.loc?.slice(1).join('.') || 'Input'}: ${item.msg}`).join(' ');
    throw new Error(text);
  }
  return data;
}
function message(id, text) { document.getElementById(id).textContent = text; }
async function mountClipControls(container, alertId) {
  container.replaceChildren(); container.style.padding = '12px';
  node('h3', 'Captured clip', container);
  const status = node('p', 'Loading clip status…', container);
  let data;
  try { data = await operation(`/api/evidence/${encodeURIComponent(alertId)}/clip`); }
  catch (e) { status.textContent = e.message; return; }
  status.textContent = `${data.status}${data.reason ? ' — ' + data.reason : ''}`;
  if (data.download_url) {
    node('p', `${Number(data.pre_seconds).toFixed(1)}s before / ${Number(data.post_seconds).toFixed(1)}s after · sampled video`, container);
    const player = node('div', undefined, container);
    action(container, 'Play / replay clip', () => {
      player.replaceChildren(); const img = node('img', undefined, player); img.alt = 'Captured clip preview';
      img.style.maxWidth = '100%'; img.src = `/api/evidence/${encodeURIComponent(alertId)}/clip/preview?t=${Date.now()}`;
      img.onerror = () => { player.textContent = 'Preview unavailable. Download the original clip below.'; };
    });
    action(container, 'Stop preview', () => player.replaceChildren());
    const link = node('a', 'Download AVI', container, 'btn-sm btn-accent'); link.href = data.download_url;
    action(container, 'Verify clip integrity', async () => {
      try { const result = await operation(`/api/evidence/${encodeURIComponent(alertId)}/clip/verify`);
        status.textContent = `${result.verified ? 'Integrity verified' : 'Not verified'} — ${result.reason}. This checks stored bytes, not scene authenticity or completeness.`;
      } catch (e) { status.textContent = e.message; }
    });
  }
  action(container, 'Refresh clip status', () => mountClipControls(container, alertId));
}
async function loadACI() {
  try {
    const cameras = await operation('/api/cameras'); const select = document.getElementById('aci-camera'); const old = select.value;
    select.replaceChildren(); cameras.forEach(c => { const option = node('option', `${c.name} (${c.id})`, select); option.value = c.id; });
    if (cameras.some(c => c.id === old)) select.value = old;
    document.getElementById('aci-build').disabled = !canManageCases(); await loadACIProfiles();
  } catch (e) { message('aci-message', e.message); }
}
async function loadACIProfiles() {
  const camera = document.getElementById('aci-camera').value; const container = document.getElementById('aci-profiles'); container.replaceChildren();
  if (!camera) { message('aci-message', 'No accessible cameras.'); return; }
  try {
    const rows = await operation(`/api/aci/${encodeURIComponent(camera)}/profiles`);
    message('aci-message', rows.length ? `${rows.length} stored profiles. Approval changes risk evaluation.` : 'No learned profiles yet. Seeded rules may still apply.');
    rows.forEach(row => {
      const card = node('div', undefined, container, 'panel'); card.style.padding = '12px';
      node('h3', row.status, card); node('p', row.id, card);
      node('p', `${row.window_start} to ${row.window_end}`, card);
      node('p', `${row.features.samples} samples · ${row.features.covered_hours} covered hours · average dwell ${Number(row.features.avg_dwell_seconds).toFixed(1)}s · night occupancy ${(row.features.night_occupancy_rate * 100).toFixed(1)}%`, card);
      node('p', row.features.estimator, card);
      if (canManageCases() && row.status !== 'ACTIVE') action(card, row.status === 'SUPERSEDED' ? 'Reactivate this version' : 'Approve baseline', async () => {
        try { await operation(`/api/aci/${encodeURIComponent(camera)}/profiles/${encodeURIComponent(row.id)}/approve`, 'POST'); await loadACIProfiles(); }
        catch (e) { message('aci-message', e.message); }
      });
    });
  } catch (e) { message('aci-message', e.message); }
}
async function buildACIProfile() {
  const camera = document.getElementById('aci-camera').value;
  try { await operation(`/api/aci/${encodeURIComponent(camera)}/profiles`, 'POST'); await loadACIProfiles(); }
  catch (e) { message('aci-message', e.message); }
}
async function loadCases() {
  document.getElementById('case-create').hidden = !canManageCases();
  await loadSupervisorReviews();
  try {
    const rows = await operation('/api/cases'); const list = document.getElementById('case-list'); list.replaceChildren();
    message('case-message', rows.length ? `${rows.length} accessible cases` : 'No accessible cases.');
    rows.forEach(row => action(list, `${row.title} · ${row.status}`, () => openCase(row.id)));
  } catch (e) { message('case-message', e.message); }
}
async function createCaseFromForm(event) {
  event.preventDefault();
  try {
    const row = await operation('/api/cases', 'POST', {title:document.getElementById('case-title').value, alert_id:document.getElementById('case-alert').value.trim()});
    await loadCases(); await openCase(row.id); document.getElementById('case-create').reset();
  } catch (e) { message('case-message', e.message); }
}
function input(parent, title, value = '') {
  const label = node('label', title + ' ', parent); const field = node('input', undefined, label, 'input-field'); field.value = value; return field;
}
async function openCase(id) {
  const box = document.getElementById('case-detail'); box.replaceChildren();
  try {
    const row = await operation(`/api/cases/${encodeURIComponent(id)}`);
    node('h3', row.title, box); node('p', `${row.id} · ${row.department} · ${row.status}`, box);
    node('p', `Assigned unit: ${row.assigned_unit || 'Unassigned'}`, box);
    if (canManageCases()) {
      const label = node('label', 'Status ', box); const status = node('select', undefined, label, 'input-field');
      ['OPEN','ESCALATED','CLOSED'].forEach(v => { const o = node('option', v, status); o.value = v; }); status.value = row.status;
      const unit = input(box, 'Assigned unit', row.assigned_unit); unit.maxLength = 160;
      const note = input(box, 'Change note'); note.maxLength = 1000;
      action(box, 'Save case changes', async () => {
        try { await operation(`/api/cases/${id}`, 'PATCH', {status:status.value, assigned_unit:unit.value, note:note.value}); await loadCases(); await openCase(id); }
        catch (e) { message('case-message', e.message); }
      });
      const alert = input(box, 'Additional alert ID'); alert.maxLength = 80;
      action(box, 'Link alert', async () => {
        try { await operation(`/api/cases/${id}/alerts`, 'POST', {alert_id:alert.value.trim()}); await openCase(id); }
        catch (e) { message('case-message', e.message); }
      });
    }
    if (['scrb_admin','department_head'].includes(userRole())) {
      const supervisor = input(box, 'Sector supervisor account');
      const assigned = node('p', 'Loading assigned supervisors…', box);
      const refresh = async () => { assigned.textContent = 'Assigned supervisors: ' + ((await operation(`/api/cases/${id}/assignments`)).join(', ') || 'None'); };
      await refresh();
      for (const [label, enabled] of [['Assign supervisor',true],['Remove assignment',false]]) action(box, label, async () => {
        try { await operation(`/api/cases/${id}/assignments`, 'POST', {username:supervisor.value.trim(),enabled}); await refresh(); }
        catch (e) { message('case-message',e.message); }
      });
    }
    if (userRole() === 'scrb_admin') {
      const account = input(box, 'Judiciary account');
      for (const [label, enabled] of [['Grant case access', true], ['Revoke case access', false]]) action(box, label, async () => {
        try { await operation(`/api/cases/${id}/access`, 'POST', {username:account.value.trim(),enabled}); message('case-message', enabled ? 'Case access granted.' : 'Case access revoked.'); }
        catch (e) { message('case-message', e.message); }
      });
    }
    for (const alert of row.alerts) {
      const card = node('div', undefined, box, 'panel'); node('h4', `${alert.id} · ${alert.origin}`, card);
      if (alert.evidence_url) { const link = node('a', 'View captured frame', card); link.href = alert.evidence_url; link.target = '_blank'; link.rel = 'noopener'; }
      const clip = node('div', undefined, card); await mountClipControls(clip, alert.id);
    }
  } catch (e) { message('case-message', e.message); }
}

async function loadSupervisorReviews() {
  const box = document.getElementById('supervisor-reviews'); box.replaceChildren(); box.hidden = !canManageCases();
  if (box.hidden) return;
  node('h3','Supervisor review queue',box);
  try {
    const rows = (await operation('/api/reviews')).filter(row => row.status === 'PENDING');
    node('p',`${rows.length} pending reviews of high-severity alert dismissals`,box);
    for (const row of rows) {
      const card = node('div',undefined,box,'panel'); card.style.padding = '12px';
      node('p',`${row.alert_id} · ${row.action} by ${row.actor} · ${row.camera_id}`,card);
      if (row.actor === window.currentUser.username) { node('p','Another supervisor must review your action.',card); continue; }
      const note = input(card,'Review note'); note.maxLength = 1000;
      for (const [label, decision] of [['Confirm dismissal','CONFIRMED'],['Reopen alert','REOPEN']]) action(card,label,async () => {
        try { await operation(`/api/reviews/${row.id}`,'POST',{decision,note:note.value}); await loadSupervisorReviews(); await loadAlerts(); }
        catch (e) { message('case-message',e.message); }
      });
    }
  } catch(e) { node('p',e.message,box); }
}
