const REFRESH_INTERVAL_MS = 2 * 60 * 1000;

const map = L.map('map', {
  zoomControl: true,
  preferCanvas: true
}).setView([30.15, -89.9], 8);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 10,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

let activeLayer;
let activeLayerVersion;
let activeKind = 'index';
let latestMeta;
let latestVersion;
let refreshInFlight = false;

function colorFromFeature(feature) {
  const title = feature.properties?.title || feature.properties?.name || '';
  const match = String(title).match(/([0-9]+\.?[0-9]*)/g);
  const val = match ? Number(match[match.length - 1]) : 0;
  if (val >= 9) return '#7f0000';
  if (val >= 8) return '#b30000';
  if (val >= 7) return '#d7301f';
  if (val >= 6) return '#ef6548';
  if (val >= 5) return '#fc8d59';
  if (val >= 4) return '#fdbb84';
  if (val >= 3) return '#fdd49e';
  if (val >= 2) return '#fee8c8';
  return '#fff7ec';
}

async function loadJson(path) {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Failed to load ${path}`);
  return response.json();
}

function withVersion(path, version) {
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}v=${encodeURIComponent(version)}`;
}

function metadataVersion(meta) {
  const status = meta.status || 'ok';
  const cycle = meta.cycle?.cycle_time_utc || 'unknown-cycle';
  return `${status}-${cycle}`;
}

function refreshMapSize() {
  // Leaflet needs an explicit size recalculation after CSS/grid layout settles.
  window.requestAnimationFrame(() => {
    map.invalidateSize(true);
  });
}

function setSidebarStatus(message, isError = false) {
  let status = document.getElementById('status-message');
  if (!status) {
    const aside = document.querySelector('aside');
    status = document.createElement('div');
    status.id = 'status-message';
    aside.insertBefore(status, aside.firstChild);
  }
  status.className = isError ? 'status error' : 'status ok';
  status.textContent = message;
}

function updateStaticImageLink(meta, version) {
  const link = document.getElementById('latest-image-link');
  const imagePath = meta.image || 'assets/latest_index.png';
  const cycle = meta.cycle?.cycle_time_utc || 'unknown cycle';
  link.href = withVersion(imagePath, version);
  link.textContent = `Open latest PNG (${cycle})`;
}

async function setLayer(kind, { fitBounds = false } = {}) {
  if (!latestMeta || latestMeta.status === 'error') return;

  const file = kind === 'probability'
    ? latestMeta.probability_contours
    : latestMeta.index_contours;
  const data = await loadJson(withVersion(`data/${file}`, latestVersion));
  const newLayer = L.geoJSON(data, {
    style: feature => ({
      color: '#111827',
      weight: 0.6,
      fillColor: colorFromFeature(feature),
      fillOpacity: 0.55
    }),
    onEachFeature: (feature, layer) => {
      const title = feature.properties?.title || feature.properties?.name || kind;
      layer.bindPopup(`<strong>${title}</strong>`);
    }
  });

  if (activeLayer) activeLayer.remove();
  activeLayer = newLayer.addTo(map);
  activeLayerVersion = latestVersion;

  refreshMapSize();
  if (fitBounds) {
    try {
      const bounds = activeLayer.getBounds();
      if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [12, 12], maxZoom: 9 });
      }
    } catch (_) {}
  }
}

async function refreshGuidance({ initial = false } = {}) {
  if (refreshInFlight) return;
  refreshInFlight = true;

  try {
    const metadata = await loadJson(withVersion('data/latest.json', Date.now()));
    const version = metadataVersion(metadata);
    const changed = version !== latestVersion;

    latestMeta = metadata;
    updateStaticImageLink(metadata, version);

    if (metadata.status === 'error') {
      document.getElementById('subtitle').textContent = `${metadata.product} | RAP generation failed`;
      setSidebarStatus(`RAP generation failed: ${metadata.error_message || 'unknown error'}`, true);
      refreshMapSize();
      latestVersion = version;
      return;
    }

    const cycle = metadata.cycle?.cycle_time_utc || 'unknown cycle';
    document.getElementById('subtitle').textContent = `${metadata.product} | RAP cycle: ${cycle}`;
    setSidebarStatus(`Latest RAP cycle: ${cycle} | Automatically checking for updates`);

    if (initial || changed || !activeLayer || activeLayerVersion !== version) {
      latestVersion = version;
      await setLayer(activeKind, { fitBounds: initial && !activeLayer });
    } else {
      latestVersion = version;
    }
  } catch (err) {
    document.getElementById('subtitle').textContent = `No current data available: ${err.message}`;
    setSidebarStatus(`No current data available: ${err.message}`, true);
    console.error(err);
    refreshMapSize();
  } finally {
    refreshInFlight = false;
  }
}

document.querySelectorAll('input[name="layer"]').forEach(input => {
  input.addEventListener('change', async event => {
    activeKind = event.target.value;
    try {
      await setLayer(activeKind);
    } catch (err) {
      setSidebarStatus(`Could not load ${activeKind} layer: ${err.message}`, true);
      console.error(err);
    }
  });
});

window.addEventListener('resize', refreshMapSize);
window.addEventListener('load', () => {
  refreshMapSize();
  setTimeout(refreshMapSize, 250);
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshGuidance();
});

refreshGuidance({ initial: true });
setInterval(refreshGuidance, REFRESH_INTERVAL_MS);
