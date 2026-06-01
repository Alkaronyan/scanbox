// stream.js
// Responsibility: Keep the MJPEG <img> element connected by auto-reconnecting on error.
// Does NOT: handle video switching, snapshots, or any API calls.
// Depends on: nothing (reads DOM on init).
// Exports: initStreamWatchdog

/**
 * Attach onerror / onabort handlers to #live-stream so a dropped MJPEG
 * connection is automatically re-established with a cache-busted URL.
 * Must be called once after the DOM is ready.
 * @sideeffects Sets img.onerror, img.onabort on #live-stream.
 */
function initStreamWatchdog() {
  const img = document.getElementById('live-stream');
  if (!img) return;

  let reconnectTimer = null;

  function scheduleReconnect(delayMs) {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      // Cache-bust forces the browser to open a fresh HTTP connection.
      img.src = '/stream?' + Date.now();
    }, delayMs);
  }

  img.onerror = () => scheduleReconnect(1000);
  img.onabort = () => scheduleReconnect(1000);
}
