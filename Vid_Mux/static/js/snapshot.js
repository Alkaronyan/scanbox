// snapshot.js
// Responsibility: Capture a snapshot and display the result in the UI.
// Does NOT: manage sources, controls, stream, or any globals.
// Depends on: api.js (apiTakeSnapshot).
// Exports: takeSnapshot

/**
 * POST a snapshot request, animate the capture button, and show the result image.
 * @sideeffects POST /api/v1/snapshot via apiTakeSnapshot.
 *              Adds/removes .flash on #snap-btn.
 *              Sets src and .visible on #snapshot-img. Updates #snap-label text.
 */
async function takeSnapshot() {
  const btn = document.getElementById('snap-btn');
  btn.classList.add('flash');
  setTimeout(() => btn.classList.remove('flash'), 300);

  const d = await apiTakeSnapshot();
  if (d.status === 'success') {
    const img = document.getElementById('snapshot-img');
    img.src = '/api/v1/snapshot/last?t=' + Date.now();
    img.classList.add('visible');
    document.getElementById('snap-label').textContent = d.filename;
  }
}
