// sources.js
// Responsibility: Render the source-selector UI and handle source switching.
// Does NOT: make raw fetch() calls (delegates to api.js), touch camera controls.
// Depends on: api.js (apiSetSource, apiGetControls), controls.js (loadControls).
//             Reads and writes globals: sources, activeId.
// Exports: renderSources, selectSource, cycleSource

/**
 * Re-render the #source-list button group to reflect the current sources / activeId globals.
 * @sideeffects Replaces innerHTML of #source-list. Attaches onclick handlers.
 */
function renderSources() {
  const el = document.getElementById('source-list');
  el.innerHTML = '';
  sources.forEach(s => {
    const btn = document.createElement('button');
    btn.className = 'source-btn' + (s.id === activeId ? ' active' : '');
    btn.innerHTML = `<span class="dot"></span>${s.name}`;
    btn.onclick = () => selectSource(s.id);
    el.appendChild(btn);
  });
}

/**
 * Switch the pipeline to the given source id, update globals, and refresh the UI.
 * @param {number} id - Source id to activate.
 * @sideeffects POST /api/v1/source via apiSetSource. Writes activeId global.
 *              Updates #source-list (via renderSources), #status-source, triggers loadControls.
 */
async function selectSource(id) {
  await apiSetSource(id);
  activeId = id;
  renderSources();
  loadControls();
  const src = sources.find(s => s.id === id);
  document.getElementById('status-source').textContent = src ? src.name : id;
}

/**
 * Advance the active source by dir steps through the sources array (wraps around).
 * @param {number} dir - +1 for next, -1 for previous.
 * @sideeffects Calls selectSource().
 */
function cycleSource(dir) {
  if (!sources.length) return;
  const idx  = sources.findIndex(s => s.id === activeId);
  const next = sources[(idx + dir + sources.length) % sources.length];
  selectSource(next.id);
}
