// Run with node tests/test_sentinel_ui.cjs; no browser or external dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../frontend/static/js/dashboard.js'), 'utf8');

async function check(result, statusCode, expected, previews) {
  const nodes = {};
  const document = {getElementById(id) {
    return nodes[id] ||= {value: id === 'sentinel-count' ? '2' : 'fixture',
      textContent: '', reportValidity: () => true, setAttribute() {}, removeAttribute() {}};
  }};
  const cells = [];
  const context = vm.createContext({document, window: {addEventListener() {}},
    fetch: async () => ({ok: statusCode === 200, status: statusCode, json: async () => result})});
  vm.runInContext(source, context);
  context.addStreamCell = id => cells.push(id);
  context.loadCameras = () => {};
  await context.connectSentinelSandbox({preventDefault() {}});
  assert.match(nodes['sentinel-status'].textContent, expected);
  assert.deepEqual(cells, previews);
  assert.equal(nodes['sentinel-password'].value, '');
  assert.equal(nodes['sentinel-submit'].disabled, false);
  assert.equal(nodes['sentinel-cancel'].disabled, false);
}

(async () => {
  await check({connected:true, cameras_registered:2,
    streams_started:[{camera_id:'CAM1', protocol:'RTSP'}],
    streams_failed:[{camera_id:'CAM2', reason:'PREVIOUS_WORKER_STOPPING'}]}, 200,
    /1 workers started; 1 failed.*CAM2: previous worker is still stopping.*retry/, ['CAM1']);
  await check({connected:false, cameras_registered:1, streams_started:[],
    streams_failed:[{camera_id:'CAM1', reason:'WORKER_START_FAILED'}]}, 200,
    /0 workers started; 1 failed.*CAM1: worker could not start/, []);
  await check({connected:true, cameras_registered:1,
    streams_started:[{camera_id:'CAM1', protocol:'RTSP'}], streams_failed:[]}, 200,
    /1 workers started; 0 failed/, ['CAM1']);
  await check({}, 401, /Connection rejected/, []);
  console.log('4 Sentinel connection UI scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
