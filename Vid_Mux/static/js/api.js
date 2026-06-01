// api.js
// Responsibility: Thin wrappers around all fetch() calls to the Vid_Mux REST API.
// Does NOT: touch the DOM, read or write any global variables.
// Depends on: nothing.
// Exports: apiGetStatus, apiSetSource, apiGetControls, apiSetControl,
//          apiTakeSnapshot, apiGetLastSnapshot

/**
 * Fetch current pipeline status (active source + full source list).
 * @returns {Promise<{status:string, active_source:number, source_name:string, sources:Array}>}
 * @sideeffects GET /api/v1/status
 */
async function apiGetStatus() {
  const r = await fetch('/api/v1/status');
  return r.json();
}

/**
 * Switch the active video source.
 * @param {number} sourceId - ID of the source to activate.
 * @returns {Promise<{status:string, active_source:number, source_name:string}>}
 * @sideeffects POST /api/v1/source
 */
async function apiSetSource(sourceId) {
  const r = await fetch('/api/v1/source', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_id: sourceId }),
  });
  return r.json();
}

/**
 * Retrieve V4L2 control definitions and current values for the active source.
 * @returns {Promise<{status:string, definitions:Array, controls:Object}>}
 * @sideeffects GET /api/v1/camera/controls
 */
async function apiGetControls() {
  const r = await fetch('/api/v1/camera/controls');
  return r.json();
}

/**
 * Set a single V4L2 control value on the active physical camera.
 * @param {string} name  - V4L2 control name (e.g. 'brightness').
 * @param {number} value - Integer value to set.
 * @returns {Promise<{status:string, control:string, value:number}>}
 * @sideeffects POST /api/v1/camera/control
 */
async function apiSetControl(name, value) {
  try {
    const r = await fetch('/api/v1/camera/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ control: name, value }),
    });
    return r.json();
  } catch (e) {
    console.error('apiSetControl failed', e);
  }
}

/**
 * Capture a JPEG snapshot from the current frame and save it server-side.
 * @returns {Promise<{status:string, file_path:string, filename:string}>}
 * @sideeffects POST /api/v1/snapshot
 */
async function apiTakeSnapshot() {
  const r = await fetch('/api/v1/snapshot', { method: 'POST' });
  return r.json();
}

/**
 * Check whether at least one snapshot exists on the server.
 * @returns {Promise<Response>} Raw Response; caller checks r.ok.
 * @sideeffects GET /api/v1/snapshot/last
 */
async function apiGetLastSnapshot() {
  return fetch('/api/v1/snapshot/last');
}
