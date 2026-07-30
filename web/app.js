const REFRESH_INTERVAL_MS = 2 * 60 * 1000;

const DEFAULT_LEGENDS = {
  index: [
    ['1–2', '#c5e9fb'], ['2–3', '#82cbed'], ['3–4', '#45a9cf'],
    ['4–5', '#3ba081'], ['5–6', '#88bf43'], ['6–7', '#dfc83b'],
    ['7–8', '#ef913d'], ['8–9', '#da4d4e'], ['9–10', '#8d2c64']
  ],
  probability: [
    ['5–10%', '#fff0a8'], ['10–20%', '#fed878'], ['20–30%', '#fdb641'],
    ['30–40%', '#f68c24'], ['40–50%', '#df6212'], ['50–60%', '#ba3e08'],
    ['60–70%', '#8e2c0a'], ['70–80%', '#6f2520'], ['80–90%', '#501835'],
    ['90–100%', '#35103d']
  ]
};

const map = L.map('map', {
  zoomControl: true,
  preferCanvas: true,
  minZoom: 5,
  maxZoom: 12
}).setView([30.15, -89.9], 8);

L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  subdomains: 'abcd',
  maxZoom: 12,
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
}).addTo(map);

L.control.scale({ imperial: true, metric: false, position: 'bottomleft' }).addTo(map);

let activeLayer;
let activeLayerType;
let activeLayerVersion;
let activeKind = 'index';
let latestMeta;
let latestVersion;
let refreshInFlight = false;
let overlayOpacity = 0.74;

const legendControl = L.control({ position: 'bottomright' });
legendControl.onAdd = function () {
  const div = L.DomUtil.create('div', 'map-legend');
  L.DomEvent.disableClickPropagation(div);
  return div;
};
legendControl.addTo(map);

function withVersion(path, version) {
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}v=${encodeURIComponent(version)}`;
}

function metadataVersion(meta) {
  const status = meta.status || 'ok';
  const cycle = meta.cycle?.cycle_time_utc || 'unknown-cycle';
  const displayVersion = meta.display_version || 1;
  return `${status}-${cycle}-display-${displayVersion}`;
}

async function loadJson(path) {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Failed to load ${path}`);
  return response.json();
}

function refreshMapSize() {
  window.requestAnimationFrame(() => map.invalidateSize(true));
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

function updateStaticImageLinks(meta, version) {
  const images = meta.images || { index: meta.image || 'assets/latest_index.png' };
  const cycle = meta.cycle?.cycle_time_utc || 'unknown cycle';

  const indexLink = document.getElementById('latest-index-link');
  indexLink.href = withVersion(images.index || 'assets/latest_index.png', version);
  indexLink.textContent = `Index PNG (${cycle})`;

  const probabilityLink = document.getElementById('latest-probability-link');
  probabilityLink.href = withVersion(images.probability || 'assets/latest_probability.png', version);
  probabilityLink.textContent = `Probability PNG (${cycle})`;
}

function getLayerMetadata(kind) {
  return latestMeta?.layers?.[kind] || null;
}

function featureValue(feature) {
  const title = feature.properties?.title || feature.properties?.name || '';
  const values = String(title).match(/([0-9]+\.?[0-9]*)/g)?.map(Number) || [];
  if (values.length >= 2) return (values[0] + values[1]) / 2;
  return values[0] || 0;
}

function fallbackColor(kind, value) {
  const entries = DEFAULT_LEGENDS[kind];
  const limits = kind === 'probability'
    ? [5, 10, 20, 30, 40, 50, 60, 70, 80, 90]
    : [1, 2, 3, 4, 5, 6, 7, 8, 9];
  let index = 0;
  limits.forEach((limit, i) => {
    if (value >= limit) index = i;
  });
  return entries[Math.min(index, entries.length - 1)][1];
}

function legendEntries(kind) {
  const metadataEntries = getLayerMetadata(kind)?.legend;
  if (metadataEntries?.length) {
    return metadataEntries.map(entry => ({
      label: `${entry.label}${kind === 'probability' ? '%' : ''}`,
      color: entry.color
    }));
  }
  return DEFAULT_LEGENDS[kind].map(([label, color]) => ({ label, color }));
}

function updateLegend(kind) {
  const container = legendControl.getContainer();
  const title = kind === 'probability' ? 'Conditional probability' : '0–10 index';
  const entries = legendEntries(kind);
  container.innerHTML = `
    <div class="legend-title">${title}</div>
    <div class="legend-items">
      ${entries.map(entry => `
        <div class="legend-row">
          <span class="legend-swatch" style="background:${entry.color}"></span>
          <span>${entry.label}</span>
        </div>
      `).join('')}
    </div>
    <div class="legend-note">Values below the display threshold are transparent.</div>
  `;
}

function updateLayerDescription(kind) {
  const description = document.getElementById('layer-description');
  if (kind === 'probability') {
    description.textContent = 'Conditional probability of a damaging-wind report given convection, based on the refined Gulf Coast model.';
  } else {
    description.textContent = 'A simplified 0–10 presentation of conditional damaging-wind favorability.';
  }
}

async function buildRasterLayer(kind, layerMeta) {
  const imageUrl = withVersion(layerMeta.image, latestVersion);
  return L.imageOverlay(imageUrl, layerMeta.bounds, {
    opacity: overlayOpacity,
    interactive: false,
    crossOrigin: false,
    className: 'guidance-raster'
  });
}

async function buildVectorFallback(kind) {
  const file = kind === 'probability'
    ? latestMeta.probability_contours
    : latestMeta.index_contours;
  const data = await loadJson(withVersion(`data/${file}`, latestVersion));
  return L.geoJSON(data, {
    style: feature => ({
      color: '#334155',
      weight: 0.35,
      opacity: 0.5,
      fillColor: fallbackColor(kind, featureValue(feature)),
      fillOpacity: Math.min(overlayOpacity, 0.5)
    }),
    onEachFeature: (feature, layer) => {
      const title = feature.properties?.title || feature.properties?.name || kind;
      layer.bindPopup(`<strong>${title}</strong>`);
    }
  });
}

async function setLayer(kind, { fitBounds = false } = {}) {
  if (!latestMeta || latestMeta.status === 'error') return;

  const layerMeta = getLayerMetadata(kind);
  const newLayer = layerMeta?.image && layerMeta?.bounds
    ? await buildRasterLayer(kind, layerMeta)
    : await buildVectorFallback(kind);

  if (activeLayer) activeLayer.remove();
  activeLayer = newLayer.addTo(map);
  activeLayerType = layerMeta?.image ? 'raster' : 'vector';
  activeLayerVersion = latestVersion;

  updateLegend(kind);
  updateLayerDescription(kind);
  refreshMapSize();

  if (fitBounds) {
    const bounds = layerMeta?.bounds ? L.latLngBounds(layerMeta.bounds) : activeLayer.getBounds?.();
    if (bounds?.isValid()) {
      map.fitBounds(bounds, { padding: [16, 16], maxZoom: 8 });
    }
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
    updateStaticImageLinks(metadata, version);

    if (metadata.status === 'error') {
      document.getElementById('subtitle').textContent = `${metadata.product} | RAP generation failed`;
      setSidebarStatus(`RAP generation failed: ${metadata.error_message || 'unknown error'}`, true);
      latestVersion = version;
      refreshMapSize();
      return;
    }

    const cycle = metadata.cycle?.cycle_time_utc || 'unknown cycle';
    document.getElementById('subtitle').textContent = `${metadata.product} | RAP cycle: ${cycle}`;
    setSidebarStatus(`Latest RAP cycle: ${cycle} | Checking every 2 minutes for a new deployment`);

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

const opacitySlider = document.getElementById('overlay-opacity');
const opacityValue = document.getElementById('opacity-value');
opacitySlider.addEventListener('input', event => {
  overlayOpacity = Number(event.target.value) / 100;
  opacityValue.textContent = `${event.target.value}%`;
  if (!activeLayer) return;
  if (activeLayerType === 'raster' && activeLayer.setOpacity) {
    activeLayer.setOpacity(overlayOpacity);
  } else if (activeLayer.setStyle) {
    activeLayer.setStyle({ fillOpacity: Math.min(overlayOpacity, 0.5) });
  }
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
