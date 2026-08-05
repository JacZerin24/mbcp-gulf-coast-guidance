(() => {
  const toggle = document.getElementById('readout-toggle');
  const card = document.getElementById('readout-card');
  const clearButton = document.getElementById('clear-readout');
  if (!toggle || !card || !clearButton || typeof map === 'undefined') return;

  let enabled = false;
  let cachedGrid = null;
  let cachedVersion = null;
  let sampledLayers = null;
  let lastClickedLocation = null;
  let requestInFlight = false;

  function readoutVersion(metadata) {
    const cycle = metadata?.cycle?.cycle_time_utc || 'unknown-cycle';
    const valid = metadata?.cycle?.valid_time_utc || cycle;
    const forecastHourValue = Number(metadata?.cycle?.forecast_hour ?? 0);
    const forecastHour = Number.isFinite(forecastHourValue)
      ? Math.max(0, Math.trunc(forecastHourValue))
      : 0;
    return `${cycle}-${valid}-f${forecastHour}`;
  }

  async function fetchJson(path) {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Failed to load ${path}`);
    return response.json();
  }

  async function currentMetadata() {
    if (typeof latestMeta !== 'undefined' && latestMeta) return latestMeta;
    return fetchJson(`data/latest.json?t=${Date.now()}`);
  }

  async function ensureReadoutGrid() {
    const metadata = await currentMetadata();
    const configuration = metadata?.readout;
    if (!configuration?.file) {
      throw new Error('Point-readout data are not available for this deployment yet.');
    }

    const version = readoutVersion(metadata);
    if (cachedGrid && cachedVersion === version) {
      return { metadata, grid: cachedGrid };
    }

    const separator = configuration.file.includes('?') ? '&' : '?';
    const grid = await fetchJson(
      `data/${configuration.file}${separator}v=${encodeURIComponent(version)}`
    );
    if (!Array.isArray(grid.points) || grid.points.length === 0) {
      throw new Error('The point-readout grid is empty.');
    }

    cachedGrid = grid;
    cachedVersion = version;
    return { metadata, grid };
  }

  function nearestGridPoint(clicked, points) {
    const latitudeRadians = clicked.lat * Math.PI / 180;
    const longitudeScale = Math.cos(latitudeRadians);
    let nearest = null;
    let bestScore = Number.POSITIVE_INFINITY;

    for (const point of points) {
      const latitudeDifference = point[0] - clicked.lat;
      let longitudeDifference = point[1] - clicked.lng;
      if (longitudeDifference > 180) longitudeDifference -= 360;
      if (longitudeDifference < -180) longitudeDifference += 360;
      const score = latitudeDifference * latitudeDifference +
        (longitudeDifference * longitudeScale) ** 2;
      if (score < bestScore) {
        bestScore = score;
        nearest = point;
      }
    }
    return nearest;
  }

  function distanceKm(first, second) {
    const earthRadiusKm = 6371.0088;
    const radians = Math.PI / 180;
    const firstLatitude = first.lat * radians;
    const secondLatitude = second.lat * radians;
    const latitudeDifference = (second.lat - first.lat) * radians;
    const longitudeDifference = (second.lng - first.lng) * radians;
    const haversine = Math.sin(latitudeDifference / 2) ** 2 +
      Math.cos(firstLatitude) * Math.cos(secondLatitude) *
      Math.sin(longitudeDifference / 2) ** 2;
    return 2 * earthRadiusKm * Math.asin(Math.sqrt(haversine));
  }

  function formatLatitude(value) {
    return `${Math.abs(value).toFixed(3)}°${value >= 0 ? 'N' : 'S'}`;
  }

  function formatLongitude(value) {
    return `${Math.abs(value).toFixed(3)}°${value >= 0 ? 'E' : 'W'}`;
  }

  function formatValue(value, digits) {
    return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : 'N/A';
  }

  function clearSampledLayers() {
    if (sampledLayers) {
      sampledLayers.remove();
      sampledLayers = null;
    }
  }

  function drawSample(clicked, point, distance) {
    clearSampledLayers();
    const sampled = L.latLng(point[0], point[1]);
    const layers = [
      L.circleMarker(clicked, {
        radius: 4,
        color: '#0f172a',
        weight: 2,
        fillColor: '#ffffff',
        fillOpacity: 1
      }),
      L.circleMarker(sampled, {
        radius: 7,
        color: '#0f172a',
        weight: 2,
        fillColor: '#38bdf8',
        fillOpacity: 0.95
      }).bindTooltip('Nearest RAP grid point', { direction: 'top' })
    ];

    if (distance > 0.25) {
      layers.push(L.polyline([clicked, sampled], {
        color: '#0f172a',
        weight: 1.5,
        opacity: 0.7,
        dashArray: '5 5'
      }));
    }

    sampledLayers = L.layerGroup(layers).addTo(map);
  }

  function productLabel(metadata) {
    const product = typeof rapProduct === 'function'
      ? rapProduct(metadata)
      : {
          valid: metadata?.cycle?.valid_time_utc || 'unknown',
          cycle: metadata?.cycle?.cycle_time_utc || 'unknown',
          fhrText: `f${String(metadata?.cycle?.forecast_hour ?? 0).padStart(2, '0')}`
        };
    return `Valid ${product.valid} | RAP ${product.cycle} ${product.fhrText}`;
  }

  function renderPlaceholder(message = 'Enable point readout, then click anywhere inside the guidance domain.') {
    card.innerHTML = `<p class="readout-placeholder">${message}</p>`;
    clearButton.disabled = true;
  }

  function renderLoading() {
    card.innerHTML = '<p class="readout-placeholder">Loading nearest-gridpoint values…</p>';
  }

  function renderError(message) {
    card.innerHTML = `<p class="readout-error">${message}</p>`;
    clearButton.disabled = !lastClickedLocation;
  }

  function renderOutsideDomain(clicked, point, distance, maxDistance) {
    card.innerHTML = `
      <div class="readout-heading">Outside readout coverage</div>
      <p class="readout-message">
        The nearest valid RAP grid point is ${distance.toFixed(1)} km away,
        beyond the ${maxDistance.toFixed(0)} km readout limit.
      </p>
      <div class="readout-meta">
        Clicked: ${formatLatitude(clicked.lat)}, ${formatLongitude(clicked.lng)}<br>
        Nearest grid: ${formatLatitude(point[0])}, ${formatLongitude(point[1])}
      </div>
    `;
    clearButton.disabled = false;
  }

  function renderValues(metadata, grid, clicked, point, distance) {
    const indexValue = point[2];
    const probabilityValue = point[3];
    card.innerHTML = `
      <div class="readout-heading">Nearest RAP grid point</div>
      <div class="readout-values">
        <div class="readout-value">
          <span>0–10 Index</span>
          <strong>${formatValue(indexValue, 1)}</strong>
        </div>
        <div class="readout-value">
          <span>Conditional probability</span>
          <strong>${formatValue(probabilityValue, 1)}%</strong>
        </div>
      </div>
      <div class="readout-meta">
        <strong>${productLabel(metadata)}</strong><br>
        Grid point: ${formatLatitude(point[0])}, ${formatLongitude(point[1])}<br>
        Clicked: ${formatLatitude(clicked.lat)}, ${formatLongitude(clicked.lng)}<br>
        Distance to sampled point: ${distance.toFixed(1)} km
      </div>
      <p class="readout-note">
        ${grid.values || 'Unsmoothed model output'}; ${grid.sampling || 'nearest grid point'}.
      </p>
    `;
    clearButton.disabled = false;
  }

  async function sampleLocation(clicked, { preserveLoading = false } = {}) {
    if (requestInFlight) return;
    requestInFlight = true;
    if (!preserveLoading) renderLoading();

    try {
      const { metadata, grid } = await ensureReadoutGrid();
      const point = nearestGridPoint(clicked, grid.points);
      if (!point) throw new Error('No valid RAP grid point could be found.');

      const sampled = { lat: point[0], lng: point[1] };
      const distance = distanceKm(clicked, sampled);
      const maxDistance = Number(
        metadata?.readout?.max_distance_km ?? grid.max_distance_km ?? 40
      );
      drawSample(clicked, point, distance);

      if (distance > maxDistance) {
        renderOutsideDomain(clicked, point, distance, maxDistance);
      } else {
        renderValues(metadata, grid, clicked, point, distance);
      }
    } catch (error) {
      console.error(error);
      renderError(error.message || 'Could not load the point readout.');
    } finally {
      requestInFlight = false;
    }
  }

  function clearReadout() {
    lastClickedLocation = null;
    clearSampledLayers();
    renderPlaceholder(
      enabled
        ? 'Point readout is enabled. Click the map for unsmoothed nearest-gridpoint values.'
        : undefined
    );
  }

  toggle.addEventListener('change', async event => {
    enabled = event.target.checked;
    map.getContainer().classList.toggle('readout-active', enabled);
    if (!enabled) {
      clearReadout();
      return;
    }

    renderLoading();
    try {
      await ensureReadoutGrid();
      renderPlaceholder(
        'Point readout is enabled. Click the map for unsmoothed nearest-gridpoint values.'
      );
    } catch (error) {
      renderError(error.message || 'Point-readout data could not be loaded.');
    }
  });

  clearButton.addEventListener('click', clearReadout);

  map.on('click', event => {
    if (!enabled) return;
    lastClickedLocation = event.latlng;
    sampleLocation(event.latlng);
  });

  setInterval(async () => {
    if (!enabled || !lastClickedLocation || requestInFlight) return;
    try {
      const metadata = await currentMetadata();
      if (readoutVersion(metadata) !== cachedVersion) {
        cachedGrid = null;
        await sampleLocation(lastClickedLocation, { preserveLoading: true });
      }
    } catch (error) {
      console.error(error);
    }
  }, 125000);

  renderPlaceholder();
})();
