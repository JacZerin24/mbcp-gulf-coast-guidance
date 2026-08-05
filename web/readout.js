(() => {
  const toggle = document.getElementById('readout-toggle');
  const stateText = document.getElementById('readout-state');
  if (!toggle || !stateText || typeof map === 'undefined') return;

  const mapContainer = map.getContainer();
  const tooltip = document.createElement('div');
  tooltip.id = 'map-hover-readout';
  tooltip.className = 'hover-readout-tooltip is-hidden';
  tooltip.setAttribute('role', 'status');
  tooltip.setAttribute('aria-live', 'polite');
  mapContainer.appendChild(tooltip);

  const HOVER_SAMPLE_DELAY_MS = 70;
  let enabled = false;
  let cachedGrid = null;
  let cachedVersion = null;
  let latestMouseEvent = null;
  let latestLatLng = null;
  let sampleTimer = null;
  let requestSequence = 0;

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
      throw new Error('Hover-readout data are not available for this deployment yet.');
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
      throw new Error('The hover-readout grid is empty.');
    }

    cachedGrid = grid;
    cachedVersion = version;
    return { metadata, grid };
  }

  function nearestGridPoint(location, points) {
    const latitudeRadians = location.lat * Math.PI / 180;
    const longitudeScale = Math.cos(latitudeRadians);
    let nearest = null;
    let bestScore = Number.POSITIVE_INFINITY;

    for (const point of points) {
      const latitudeDifference = point[0] - location.lat;
      let longitudeDifference = point[1] - location.lng;
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

  function productDetails(metadata) {
    if (typeof rapProduct === 'function') return rapProduct(metadata);
    const forecastHour = Number(metadata?.cycle?.forecast_hour ?? 0);
    return {
      valid: metadata?.cycle?.valid_time_utc || 'unknown',
      cycle: metadata?.cycle?.cycle_time_utc || 'unknown',
      fhrText: `f${String(forecastHour).padStart(2, '0')}`
    };
  }

  function activeLayerKind() {
    return typeof activeKind !== 'undefined' && activeKind === 'probability'
      ? 'probability'
      : 'index';
  }

  function positionTooltip(mouseEvent) {
    if (!mouseEvent || tooltip.classList.contains('is-hidden')) return;

    const containerBounds = mapContainer.getBoundingClientRect();
    const pointerX = mouseEvent.clientX - containerBounds.left;
    const pointerY = mouseEvent.clientY - containerBounds.top;
    const offset = 16;
    const margin = 8;
    const width = tooltip.offsetWidth;
    const height = tooltip.offsetHeight;

    let left = pointerX + offset;
    let top = pointerY + offset;
    if (left + width + margin > containerBounds.width) {
      left = pointerX - width - offset;
    }
    if (top + height + margin > containerBounds.height) {
      top = pointerY - height - offset;
    }

    tooltip.style.left = `${Math.max(margin, left)}px`;
    tooltip.style.top = `${Math.max(margin, top)}px`;
  }

  function showTooltip(html, mouseEvent, modifier = '') {
    tooltip.className = `hover-readout-tooltip ${modifier}`.trim();
    tooltip.innerHTML = html;
    positionTooltip(mouseEvent);
  }

  function hideTooltip() {
    tooltip.className = 'hover-readout-tooltip is-hidden';
    tooltip.innerHTML = '';
  }

  function showLoading(mouseEvent) {
    showTooltip(
      '<div class="hover-readout-message">Loading RAP readout…</div>',
      mouseEvent,
      'is-loading'
    );
  }

  function showError(message, mouseEvent) {
    showTooltip(
      `<div class="hover-readout-message hover-readout-error">${message}</div>`,
      mouseEvent,
      'is-error'
    );
  }

  function showOutsideCoverage(distance, maxDistance, mouseEvent) {
    showTooltip(
      `<div class="hover-readout-title">Outside readout coverage</div>
       <div class="hover-readout-message">Nearest valid RAP point: ${distance.toFixed(1)} km away<br>Limit: ${maxDistance.toFixed(0)} km</div>`,
      mouseEvent,
      'is-outside'
    );
  }

  function showValues(metadata, point, distance, mouseEvent) {
    const product = productDetails(metadata);
    const indexValue = formatValue(point[2], 1);
    const probabilityValue = `${formatValue(point[3], 1)}%`;
    const active = activeLayerKind();

    showTooltip(
      `<div class="hover-readout-title">Nearest RAP grid point</div>
       <div class="hover-readout-values">
         <div class="hover-readout-value ${active === 'index' ? 'is-active' : ''}">
           <span>0–10 Index</span>
           <strong>${indexValue}</strong>
         </div>
         <div class="hover-readout-value ${active === 'probability' ? 'is-active' : ''}">
           <span>Conditional probability</span>
           <strong>${probabilityValue}</strong>
         </div>
       </div>
       <div class="hover-readout-meta">
         ${formatLatitude(point[0])}, ${formatLongitude(point[1])} • ${distance.toFixed(1)} km<br>
         Valid ${product.valid} • RAP ${product.cycle} ${product.fhrText}<br>
         <span>Unsmoothed nearest-gridpoint values</span>
       </div>`,
      mouseEvent
    );
  }

  async function updateHoverSample() {
    if (!enabled || !latestLatLng || !latestMouseEvent) return;
    const sequence = ++requestSequence;

    try {
      const { metadata, grid } = await ensureReadoutGrid();
      if (sequence !== requestSequence || !enabled || !latestLatLng) return;

      const point = nearestGridPoint(latestLatLng, grid.points);
      if (!point) throw new Error('No valid RAP grid point could be found.');

      const distance = distanceKm(latestLatLng, { lat: point[0], lng: point[1] });
      const maxDistance = Number(
        metadata?.readout?.max_distance_km ?? grid.max_distance_km ?? 40
      );

      if (distance > maxDistance) {
        showOutsideCoverage(distance, maxDistance, latestMouseEvent);
      } else {
        showValues(metadata, point, distance, latestMouseEvent);
      }
    } catch (error) {
      console.error(error);
      if (sequence === requestSequence && enabled) {
        showError(error.message || 'Could not load the RAP readout.', latestMouseEvent);
        stateText.textContent = error.message || 'Hover-readout data could not be loaded.';
        stateText.classList.add('readout-state-error');
      }
    }
  }

  function scheduleSample() {
    if (sampleTimer) return;
    sampleTimer = window.setTimeout(() => {
      sampleTimer = null;
      updateHoverSample();
    }, HOVER_SAMPLE_DELAY_MS);
  }

  toggle.addEventListener('change', async event => {
    enabled = event.target.checked;
    mapContainer.classList.toggle('readout-active', enabled);
    requestSequence += 1;

    if (!enabled) {
      latestLatLng = null;
      latestMouseEvent = null;
      hideTooltip();
      stateText.textContent = 'Enable the option and move the mouse across the map to sample unsmoothed RAP values.';
      stateText.classList.remove('readout-state-error');
      return;
    }

    stateText.textContent = 'Loading hover-readout data…';
    stateText.classList.remove('readout-state-error');
    try {
      await ensureReadoutGrid();
      stateText.textContent = 'Hover over the map to sample the nearest RAP grid point.';
    } catch (error) {
      console.error(error);
      stateText.textContent = error.message || 'Hover-readout data could not be loaded.';
      stateText.classList.add('readout-state-error');
    }
  });

  map.on('mousemove', event => {
    if (!enabled) return;
    latestLatLng = event.latlng;
    latestMouseEvent = event.originalEvent;

    if (tooltip.classList.contains('is-hidden')) {
      showLoading(latestMouseEvent);
    } else {
      positionTooltip(latestMouseEvent);
    }
    scheduleSample();
  });

  mapContainer.addEventListener('mouseleave', () => {
    latestLatLng = null;
    latestMouseEvent = null;
    requestSequence += 1;
    hideTooltip();
  });

  map.on('movestart zoomstart', hideTooltip);

  document.querySelectorAll('input[name="layer"]').forEach(input => {
    input.addEventListener('change', () => {
      if (enabled && latestLatLng && latestMouseEvent) scheduleSample();
    });
  });

  setInterval(async () => {
    if (!enabled) return;
    try {
      const metadata = await currentMetadata();
      if (readoutVersion(metadata) !== cachedVersion) {
        cachedGrid = null;
        if (latestLatLng && latestMouseEvent) {
          showLoading(latestMouseEvent);
          updateHoverSample();
        }
      }
    } catch (error) {
      console.error(error);
    }
  }, 125000);
})();
