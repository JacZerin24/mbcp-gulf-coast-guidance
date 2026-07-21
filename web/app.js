const map = L.map('map', {
  zoomControl: true,
  preferCanvas: true
}).setView([30.15, -89.9], 8);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 10,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

let activeLayer;
let latestMeta;

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

async function setLayer(kind) {
  if (activeLayer) activeLayer.remove();
  const file = kind === 'probability' ? latestMeta.probability_contours : latestMeta.index_contours;
  const data = await loadJson(`data/${file}`);
  activeLayer = L.geoJSON(data, {
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
  }).addTo(map);

  refreshMapSize();
  try {
    const bounds = activeLayer.getBounds();
    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [12, 12], maxZoom: 9 });
    }
  } catch (_) {}
}

async function main() {
  refreshMapSize();
  try {
    latestMeta = await loadJson('data/latest.json');
    const cycle = latestMeta.cycle?.cycle_time_utc || 'unknown cycle';

    if (latestMeta.status === 'error') {
      document.getElementById('subtitle').textContent = `${latestMeta.product} | RAP generation failed`;
      setSidebarStatus(`RAP generation failed: ${latestMeta.error_message || 'unknown error'}`, true);
      refreshMapSize();
      return;
    }

    document.getElementById('subtitle').textContent = `${latestMeta.product} | RAP cycle: ${cycle}`;
    setSidebarStatus(`Latest RAP cycle: ${cycle}`);
    await setLayer('index');
  } catch (err) {
    document.getElementById('subtitle').textContent = `No current data available: ${err.message}`;
    setSidebarStatus(`No current data available: ${err.message}`, true);
    console.error(err);
    refreshMapSize();
  }
}

document.querySelectorAll('input[name="layer"]').forEach(input => {
  input.addEventListener('change', event => setLayer(event.target.value));
});

window.addEventListener('resize', refreshMapSize);
window.addEventListener('load', () => {
  refreshMapSize();
  setTimeout(refreshMapSize, 250);
});

main();
