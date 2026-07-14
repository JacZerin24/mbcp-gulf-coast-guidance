const map = L.map('map').setView([30.15, -89.9], 8);

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
  try { map.fitBounds(activeLayer.getBounds(), { padding: [10, 10] }); } catch (_) {}
}

async function main() {
  try {
    latestMeta = await loadJson('data/latest.json');
    const cycle = latestMeta.cycle?.cycle_time_utc || 'unknown cycle';
    document.getElementById('subtitle').textContent = `${latestMeta.product} | RAP cycle: ${cycle}`;
    await setLayer('index');
  } catch (err) {
    document.getElementById('subtitle').textContent = `No current data available: ${err.message}`;
    console.error(err);
  }
}

document.querySelectorAll('input[name="layer"]').forEach(input => {
  input.addEventListener('change', event => setLayer(event.target.value));
});

main();
