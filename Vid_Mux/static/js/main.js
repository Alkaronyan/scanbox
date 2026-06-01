// main.js
// Responsibility: Declare shared globals, wire up init calls, and start the
//                status polling loop.
// Does NOT: implement any feature logic (delegated to the other modules).
// Depends on: api.js, stream.js, sources.js, controls.js, snapshot.js, ui.js.
// Exports: sources, activeId, zoomLevel, focusLevel, sliderTimers (globals).

// ── Shared state ──────────────────────────────────────────────────────────
// sources and activeId are populated by fetchStatus() and read by sources.js / ui.js.
let sources      = [];
let activeId     = 0;

// zoomLevel and focusLevel are fake placeholders until PTZ is implemented.
let zoomLevel    = 50;
let focusLevel   = 50;

// Per-control debounce timers used by controls.js onSlider().
let sliderTimers = {};

// ── Fetch status + update sources / status panel ──────────────────────────
/**
 * Poll /api/v1/status, refresh the source list, and update the status panel.
 * Called once on load and then every 3 s.
 * @sideeffects Writes sources, activeId globals. Updates DOM via renderSources.
 *              Updates #status-source text.
 */
async function fetchStatus() {
  const d = await apiGetStatus();
  sources  = d.sources;
  activeId = d.active_source;
  renderSources();
  document.getElementById('status-source').textContent = d.source_name;
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

fetchStatus();
loadControls();
setInterval(fetchStatus, 3000);
