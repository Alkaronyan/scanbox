// ui.js
// Responsibility: Section collapse, modal, fake PTZ zoom/focus bars, and all
//                keyboard / mouse-wheel event bindings.
// Does NOT: make API calls or manage video sources / camera controls directly.
// Depends on: sources.js (cycleSource), snapshot.js (takeSnapshot).
//             Reads/writes globals: zoomLevel, focusLevel.
// Exports: toggleSection, openModal, closeModal, setZoom, setFocus

/**
 * Toggle a collapsible card section open or closed.
 * @param {HTMLElement} btn - The .section-toggle button element.
 * @sideeffects Toggles .collapsed on btn.nextElementSibling.
 *              Updates .toggle-icon text (▾ / ▸).
 */
function toggleSection(btn) {
  const body = btn.nextElementSibling;
  const icon = btn.querySelector('.toggle-icon');
  const collapsed = body.classList.toggle('collapsed');
  icon.textContent = collapsed ? '▸' : '▾';
}

/**
 * Show the keyboard-shortcuts modal overlay.
 * @sideeffects Removes .hidden from #modal-overlay.
 */
function openModal() {
  document.getElementById('modal-overlay').classList.remove('hidden');
}

/**
 * Hide the keyboard-shortcuts modal overlay.
 * @sideeffects Adds .hidden to #modal-overlay.
 */
function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
}

/**
 * Adjust the fake zoom bar by delta percent (placeholder — no real V4L2 zoom yet).
 * @param {number} delta - Percent to add (positive = zoom in, negative = zoom out).
 * @sideeffects Writes zoomLevel global. Updates #zoom-fill width style.
 */
function setZoom(delta) {
  zoomLevel = Math.max(0, Math.min(100, zoomLevel + delta));
  const el = document.getElementById('zoom-fill');
  if (el) el.style.width = zoomLevel + '%';
}

/**
 * Adjust the fake focus bar by delta percent (placeholder — no real V4L2 focus yet).
 * @param {number} delta - Percent to add (positive = more focus, negative = less).
 * @sideeffects Writes focusLevel global. Updates #focus-fill width style.
 */
function setFocus(delta) {
  focusLevel = Math.max(0, Math.min(100, focusLevel + delta));
  const el = document.getElementById('focus-fill');
  if (el) el.style.width = focusLevel + '%';
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  switch (e.code) {
    case 'F1':
      e.preventDefault();
      document.getElementById('modal-overlay').classList.toggle('hidden');
      break;
    case 'Space':      e.preventDefault(); takeSnapshot();    break;
    case 'Tab':        e.preventDefault(); cycleSource(1);    break;
    case 'ArrowRight':                     cycleSource(1);    break;
    case 'ArrowLeft':                      cycleSource(-1);   break;
    case 'KeyQ':                           setFocus(-5);      break;
    case 'KeyE':                           setFocus(5);       break;
  }
});

// ── Ctrl+Scroll → fake zoom ───────────────────────────────────────────────
document.addEventListener('wheel', e => {
  if (!e.ctrlKey) return;
  e.preventDefault();
  setZoom(e.deltaY < 0 ? 5 : -5);
}, { passive: false });
