// snapshot.js
// Responsibility: Capture snapshots, manage the gallery dropdown, and handle downloads/deletes.
// Does NOT: manage sources, controls, stream, or any globals.
// Depends on: api.js (apiTakeSnapshot, apiListSnapshots, apiDeleteSnapshot).
// Exports: takeSnapshot, loadSnapshotGallery, onSnapshotSelected,
//          downloadSelectedSnapshot, deleteSelectedSnapshot

// ── Gallery state ─────────────────────────────────────────────────────────────
let _snapOffset   = 0;
let _snapTotal    = 0;
let _snapFiles    = [];
let _selectedSnap = null;

// ── Gallery load ──────────────────────────────────────────────────────────────

async function loadSnapshotGallery() {
  const d = await apiListSnapshots(0, 5);
  if (d.status !== 'ok') return;
  _snapFiles  = d.files;
  _snapOffset = d.files.length;
  _snapTotal  = d.total;
  renderSnapshotDropdown();
  if (_selectedSnap === null && _snapFiles.length > 0) {
    onSnapshotSelected(_snapFiles[0]);
  }
}

async function loadMoreSnapshots() {
  const d = await apiListSnapshots(_snapOffset, 15);
  if (d.status !== 'ok') return;
  _snapFiles  = _snapFiles.concat(d.files);
  _snapOffset = _snapOffset + d.files.length;
  _snapTotal  = d.total;
  renderSnapshotDropdown();
}

// ── Dropdown rendering ────────────────────────────────────────────────────────

function renderSnapshotDropdown() {
  const sel = document.getElementById('snap-select');
  sel.innerHTML = '';

  if (_snapFiles.length === 0) {
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = 'No snapshots';
    sel.appendChild(empty);
    sel.disabled = true;
    return;
  }

  sel.disabled = false;

  for (const filename of _snapFiles) {
    const opt = document.createElement('option');
    opt.value = filename;
    opt.textContent = filename;
    sel.appendChild(opt);
  }

  if (_snapOffset < _snapTotal) {
    const more = document.createElement('option');
    more.value = '__load_more__';
    more.textContent = 'Search for older pics…';
    sel.appendChild(more);
  }

  sel.value = (_selectedSnap && _snapFiles.includes(_selectedSnap))
    ? _selectedSnap
    : _snapFiles[0];
}

// ── Selection handler ─────────────────────────────────────────────────────────

async function onSnapshotSelected(value) {
  if (value === '__load_more__') {
    await loadMoreSnapshots();
    return;
  }

  _selectedSnap = value;

  const img   = document.getElementById('snapshot-img');
  const label = document.getElementById('snap-label');
  img.src = `/api/v1/snapshot/${value}?t=` + Date.now();
  img.classList.add('visible');
  label.textContent = value;
  document.getElementById('snap-download-btn').disabled = false;
  document.getElementById('snap-delete-btn').disabled   = false;

  // Keep the select in sync when called programmatically
  const sel = document.getElementById('snap-select');
  if (sel.value !== value) sel.value = value;
}

// ── Download ──────────────────────────────────────────────────────────────────

function downloadSelectedSnapshot() {
  if (!_selectedSnap) return;
  const a = document.createElement('a');
  a.href     = `/api/v1/snapshot/${_selectedSnap}`;
  a.download = _selectedSnap;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// ── Delete ────────────────────────────────────────────────────────────────────

async function deleteSelectedSnapshot() {
  if (!_selectedSnap) return;
  const d = await apiDeleteSnapshot(_selectedSnap);
  if (d.status !== 'success') return;

  _snapFiles  = _snapFiles.filter(f => f !== _selectedSnap);
  _snapOffset = Math.max(0, _snapOffset - 1);
  _snapTotal  = Math.max(0, _snapTotal  - 1);
  _selectedSnap = null;

  renderSnapshotDropdown();

  if (_snapFiles.length > 0) {
    onSnapshotSelected(_snapFiles[0]);
  } else {
    const img = document.getElementById('snapshot-img');
    img.classList.remove('visible');
    img.src = '';
    document.getElementById('snap-label').textContent = 'No snapshot yet';
    document.getElementById('snap-download-btn').disabled = true;
    document.getElementById('snap-delete-btn').disabled   = true;
  }
}

// ── Capture ───────────────────────────────────────────────────────────────────

async function takeSnapshot() {
  const btn = document.getElementById('snap-btn');
  btn.classList.add('flash');
  setTimeout(() => btn.classList.remove('flash'), 300);

  const d = await apiTakeSnapshot();
  if (d.status === 'success') {
    _snapFiles.unshift(d.filename);
    _snapOffset += 1;
    _snapTotal  += 1;
    renderSnapshotDropdown();
    onSnapshotSelected(d.filename);
  }
}
