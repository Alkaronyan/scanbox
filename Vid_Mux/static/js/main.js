// main.js
// Responsibility: Declare shared globals, wire up init calls, and start the
//                status polling loop.
// Does NOT: implement any feature logic (delegated to the other modules).
// Depends on: api.js, stream.js, sources.js, controls.js, snapshot.js, ui.js.
// Exports: sources, activeId, zoomLevel, focusLevel, sliderTimers (globals).

// ── Shared state ──────────────────────────────────────────────────────────
// sources and activeId are populated by fetchStatus() and read by sources.js / ui.js.
let sources        = [];
let activeId       = 0;
let runningSourceIds = [];  // populated by fetchStatus(), read by sources.js

// zoomLevel and focusLevel are fake placeholders until PTZ is implemented.
let zoomLevel    = 50;
let focusLevel   = 50;

// Per-control debounce timers used by controls.js onSlider().
let sliderTimers = {};

// Debug mode flag — toggled from the F1 modal.
let debugMode = false;

// Sources currently being restarted (show "REINITIALIZING…" watermark).
let reInitializingSourceIds = new Set();

// ── Debug mode ────────────────────────────────────────────────────────────
function applyDebugMode(on) {
  debugMode = on;
  document.body.classList.toggle('debug-mode', on);
  renderSources();
}

// ── Live stream watermark ─────────────────────────────────────────────────
function updateStreamWatermark() {
  const watermark = document.getElementById('stream-watermark');
  const text = document.getElementById('watermark-text');
  if (!watermark) return;
  if (reInitializingSourceIds.has(activeId)) {
    watermark.className = 'stream-watermark visible reinit';
    text.textContent = 'REINITIALIZING…';
  } else if (sources.length > 0 && !runningSourceIds.includes(activeId)) {
    watermark.className = 'stream-watermark visible';
    text.textContent = 'CAMERA STOPPED';
  } else {
    watermark.className = 'stream-watermark';
    reInitializingSourceIds.delete(activeId);
  }
}

// ── Fetch status + update sources / status panel ──────────────────────────
/**
 * Poll /api/v1/status, refresh the source list, and update the status panel.
 * Called once on load and then every 3 s.
 * @sideeffects Writes sources, activeId globals. Updates DOM via renderSources.
 *              Updates #status-source text and stream watermark.
 */
async function fetchStatus() {
  const d = await apiGetStatus();
  sources          = d.sources;
  activeId         = d.active_source;
  runningSourceIds = d.running_sources || [];
  if (runningSourceIds.includes(activeId)) reInitializingSourceIds.delete(activeId);
  renderSources();
  const isRunning = runningSourceIds.includes(activeId);
  document.getElementById('status-source').textContent =
    isRunning ? d.source_name : `${d.source_name} · Stopped`;
  updateStreamWatermark();
}

// ── Init ──────────────────────────────────────────────────────────────────
// Initialise fake PTZ bars to match the starting global values.
setZoom(0);
setFocus(0);

initStreamWatchdog();

document.getElementById('snap-btn').onclick = takeSnapshot;

// Show the last saved snapshot if one already exists on the server.
apiGetLastSnapshot().then(r => {
  if (r.ok) {
    const img = document.getElementById('snapshot-img');
    img.src = '/api/v1/snapshot/last?t=' + Date.now();
    img.classList.add('visible');
    document.getElementById('snap-label').textContent = 'Last saved snapshot';
  }
}).catch(() => {});

loadSnapshotGallery();

async function sendHeartbeat() {
  const d = await apiHeartbeat();
  if (d && d.cameras_starting) {
    reInitializingSourceIds.add(activeId);
    updateStreamWatermark();
  }
}

sendHeartbeat();
setInterval(sendHeartbeat, 10000);

fetchStatus();
loadControls();
setInterval(fetchStatus, 3000);
