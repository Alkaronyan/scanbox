// controls.js
// Responsibility: Build and manage the Camera Config (V4L2) control widgets.
// Does NOT: handle source switching, snapshots, PTZ placeholder, or stream.
// Depends on: api.js (apiGetControls, apiSetControl).
//             Reads global: sliderTimers.
// Exports: renderControls, loadControls, resetControls,
//          onSlider, setMenuCtrl, setBoolCtrl, updateToggleGroup

// Defaults map populated by renderControls and consumed by resetControls.
let _cameraDefaults = {};

/**
 * Debounce a slider change: update the value label immediately, then send
 * the V4L2 command 300 ms after the user stops dragging.
 * @param {string} name  - V4L2 control name (matches slider id suffix).
 * @param {string} value - Current slider value as a string.
 * @sideeffects Updates #val-<name> text. Calls apiSetControl after debounce delay.
 *              Reads/writes sliderTimers global.
 */
function onSlider(name, value) {
  const lbl = document.getElementById('val-' + name);
  if (lbl) lbl.textContent = value;
  clearTimeout(sliderTimers[name]);
  sliderTimers[name] = setTimeout(() => apiSetControl(name, parseInt(value)), 300);
}

/**
 * Apply an optimistic toggle-group highlight, then persist the menu control value.
 * Reloads all controls afterwards to reflect any inactive-state changes.
 * @param {string} name  - V4L2 control name.
 * @param {number} value - Selected option integer value.
 * @sideeffects DOM toggle via updateToggleGroup. POST via apiSetControl. Reloads controls.
 */
async function setMenuCtrl(name, value) {
  updateToggleGroup(name, value);
  await apiSetControl(name, value);
  await loadControls();
}

/**
 * Apply an optimistic toggle-group highlight, then persist the boolean control value.
 * Reloads all controls afterwards to reflect any inactive-state changes.
 * @param {string} name  - V4L2 control name.
 * @param {number} value - 0 (off) or 1 (on).
 * @sideeffects DOM toggle via updateToggleGroup. POST via apiSetControl. Reloads controls.
 */
async function setBoolCtrl(name, value) {
  updateToggleGroup(name, value);
  await apiSetControl(name, value);
  await loadControls();
}

/**
 * Highlight the button in a toggle-group that matches the given value.
 * @param {string} name  - V4L2 control name (group id is 'ctrl-' + name).
 * @param {number} value - Value of the button to mark active.
 * @sideeffects Toggles .active class on .toggle-opt children of #ctrl-<name>.
 */
function updateToggleGroup(name, value) {
  const group = document.getElementById('ctrl-' + name);
  if (!group) return;
  group.querySelectorAll('.toggle-opt').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.value) === parseInt(value));
  });
}

/**
 * Rebuild the entire #camera-config-body section from V4L2 control definitions.
 * Supports slider (int), toggle (bool / binary int), and menu widget types.
 * @param {Array}  definitions - Control definition objects from the API.
 * @param {Object} values      - Map of control name → current integer value.
 * @sideeffects Replaces innerHTML of #camera-config-body. Populates _cameraDefaults.
 */
function renderControls(definitions, values) {
  const body = document.getElementById('camera-config-body');
  body.innerHTML = '';
  _cameraDefaults = {};

  if (!definitions || definitions.length === 0) {
    body.innerHTML = '<p class="hint">No controls available for this source.</p>';
    return;
  }

  definitions.forEach(def => {
    const val      = values[def.name] ?? def.default;
    const inactive = def.inactive;

    _cameraDefaults[def.name] = def.default;

    const isBinary = (def.type === 'int' && (def.max - def.min) === 1 && def.step === 1);
    const isToggle = (def.type === 'bool' || isBinary);
    const isMenu   = (def.type === 'menu');
    const isSlider = (def.type === 'int' && !isBinary);

    const row = document.createElement('div');
    row.className = 'config-row' + (inactive ? ' disabled' : '');
    row.id = 'row-' + def.name;

    const lbl = document.createElement('label');
    lbl.className = 'config-label' + (inactive ? ' dim' : '');
    if (isSlider) {
      lbl.innerHTML = `${def.label} <span class="hint-inline" id="val-${def.name}">${val ?? '—'}</span>`;
    } else {
      lbl.textContent = def.label;
    }
    row.appendChild(lbl);

    if (isSlider) {
      const input = document.createElement('input');
      input.type      = 'range';
      input.className = 'config-slider';
      input.id        = 'slider-' + def.name;
      input.min       = def.min;
      input.max       = def.max;
      input.step      = def.step;
      input.value     = val ?? def.default;
      input.disabled  = inactive;
      const ctrlName  = def.name;
      input.oninput   = function() { onSlider(ctrlName, this.value); };
      row.appendChild(input);

    } else if (isMenu) {
      const grp = document.createElement('div');
      grp.className = 'toggle-group';
      grp.id = 'ctrl-' + def.name;
      Object.entries(def.options).forEach(([optKey, optLabel]) => {
        const optVal   = parseInt(optKey);
        const btn      = document.createElement('button');
        btn.className  = 'toggle-opt' + (optVal === val ? ' active' : '');
        btn.dataset.value = optVal;
        btn.textContent   = optLabel;
        btn.disabled      = inactive;
        const ctrlName    = def.name;
        btn.onclick = function() { setMenuCtrl(ctrlName, optVal); };
        grp.appendChild(btn);
      });
      row.appendChild(grp);

    } else if (isToggle) {
      const grp = document.createElement('div');
      grp.className = 'toggle-group';
      grp.id = 'ctrl-' + def.name;
      [['Off', 0], ['On', 1]].forEach(([label, btnVal]) => {
        const btn      = document.createElement('button');
        btn.className  = 'toggle-opt' + (btnVal === val ? ' active' : '');
        btn.dataset.value = btnVal;
        btn.textContent   = label;
        btn.disabled      = inactive;
        const ctrlName    = def.name;
        btn.onclick = function() { setBoolCtrl(ctrlName, btnVal); };
        grp.appendChild(btn);
      });
      row.appendChild(grp);
    }

    body.appendChild(row);
  });

  const div = document.createElement('div');
  div.className = 'config-divider';
  body.appendChild(div);

  const resetBtn = document.createElement('button');
  resetBtn.className   = 'reset-btn';
  resetBtn.textContent = '↺ Reset to defaults';
  resetBtn.onclick     = resetControls;
  body.appendChild(resetBtn);
}

/**
 * Fetch current V4L2 control state from the API and re-render the Camera Config section.
 * @sideeffects GET /api/v1/camera/controls via apiGetControls. Updates DOM via renderControls.
 */
async function loadControls() {
  try {
    const d = await apiGetControls();
    if (d.status !== 'ok') return;
    renderControls(d.definitions, d.controls);
  } catch (e) {
    console.error('loadControls failed', e);
  }
}

/**
 * Send all controls back to their factory defaults, then reload the UI.
 * @sideeffects Multiple POST /api/v1/camera/control calls via apiSetControl.
 *              Re-renders Camera Config via loadControls.
 */
async function resetControls() {
  for (const [name, value] of Object.entries(_cameraDefaults)) {
    await apiSetControl(name, value);
  }
  await loadControls();
}
