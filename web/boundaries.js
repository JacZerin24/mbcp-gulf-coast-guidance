(() => {
  const cwaToggle = document.getElementById('cwa-toggle');
  const countyToggle = document.getElementById('county-toggle');
  const status = document.getElementById('boundary-status');
  if (!cwaToggle || !countyToggle || !status || typeof map === 'undefined') return;

  const DEFAULT_BOUNDS = [[28.0, -91.8], [31.6, -88.0]];
  const CWA_ENDPOINT = 'https://mapservices.weather.noaa.gov/static/rest/services/nws_reference_maps/nws_reference_map/FeatureServer/1/query';
  const STATE_ENDPOINT = 'https://tigerweb.geo.census.gov/arcgis/rest/services/Generalized_TAB2020/State_County/MapServer/9/query';
  const COUNTY_ENDPOINT = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query';

  map.createPane('stateBoundaryPane');
  map.getPane('stateBoundaryPane').style.zIndex = 420;
  map.getPane('stateBoundaryPane').style.pointerEvents = 'none';
  map.createPane('countyBoundaryPane');
  map.getPane('countyBoundaryPane').style.zIndex = 430;
  map.getPane('countyBoundaryPane').style.pointerEvents = 'none';
  map.createPane('cwaBoundaryPane');
  map.getPane('cwaBoundaryPane').style.zIndex = 440;
  map.getPane('cwaBoundaryPane').style.pointerEvents = 'none';

  let stateLayer = null;
  let cwaLayer = null;
  let countyLayer = null;
  let stateData = null;
  let cwaData = null;
  let countyData = null;
  let stateLoading = false;
  let cwaLoading = false;
  let countyLoading = false;

  function setStatus(message, isError = false) {
    status.textContent = message;
    status.classList.toggle('boundary-error', isError);
  }

  function currentDomainBounds() {
    const bounds = typeof latestMeta !== 'undefined'
      ? latestMeta?.layers?.index?.bounds || latestMeta?.layers?.probability?.bounds
      : null;
    return Array.isArray(bounds) && bounds.length === 2 ? bounds : DEFAULT_BOUNDS;
  }

  async function fetchGeoJson(url) {
    const response = await fetch(url, { cache: 'force-cache' });
    if (!response.ok) throw new Error(`Boundary service returned HTTP ${response.status}`);
    const data = await response.json();
    if (!data || !Array.isArray(data.features)) {
      throw new Error('Boundary service did not return GeoJSON features');
    }
    return data;
  }

  function stateUrl() {
    const params = new URLSearchParams({
      where: '1=1',
      outFields: 'NAME,GEOID',
      returnGeometry: 'true',
      outSR: '4326',
      f: 'geojson'
    });
    return `${STATE_ENDPOINT}?${params.toString()}`;
  }

  function cwaUrl() {
    const params = new URLSearchParams({
      where: "cwa='LIX'",
      outFields: 'cwa',
      returnGeometry: 'true',
      outSR: '4326',
      f: 'geojson'
    });
    return `${CWA_ENDPOINT}?${params.toString()}`;
  }

  function countyUrl() {
    const [[south, west], [north, east]] = currentDomainBounds();
    const params = new URLSearchParams({
      where: '1=1',
      outFields: 'NAME,STATE,GEOID',
      geometry: `${west},${south},${east},${north}`,
      geometryType: 'esriGeometryEnvelope',
      inSR: '4326',
      spatialRel: 'esriSpatialRelIntersects',
      returnGeometry: 'true',
      outSR: '4326',
      f: 'geojson'
    });
    return `${COUNTY_ENDPOINT}?${params.toString()}`;
  }

  function buildStateLayer(data) {
    return L.geoJSON(data, {
      pane: 'stateBoundaryPane',
      interactive: false,
      style: {
        color: '#475569',
        weight: 1.5,
        opacity: 0.9,
        fillOpacity: 0
      }
    });
  }

  function buildCwaLayer(data) {
    return L.geoJSON(data, {
      pane: 'cwaBoundaryPane',
      interactive: false,
      style: {
        color: '#7c3aed',
        weight: 2.4,
        opacity: 0.95,
        fillOpacity: 0
      }
    });
  }

  function buildCountyLayer(data) {
    return L.geoJSON(data, {
      pane: 'countyBoundaryPane',
      interactive: false,
      style: {
        color: '#334155',
        weight: 0.8,
        opacity: 0.72,
        fillOpacity: 0
      }
    });
  }

  async function enableStates() {
    if (stateLayer) {
      stateLayer.addTo(map);
      return;
    }
    if (stateLoading) return;
    stateLoading = true;
    try {
      stateData = stateData || await fetchGeoJson(stateUrl());
      stateLayer = buildStateLayer(stateData).addTo(map);
      map.attributionControl.addAttribution('State boundaries: U.S. Census Bureau TIGERweb');
      setStatus('State outlines enabled by default. Optional boundary layers load when enabled.');
    } catch (error) {
      console.error(error);
      setStatus(`Could not load state outlines: ${error.message}`, true);
    } finally {
      stateLoading = false;
    }
  }

  async function enableCwa() {
    if (cwaLayer) {
      cwaLayer.addTo(map);
      setStatus('LIX CWA outline enabled.');
      return;
    }
    if (cwaLoading) return;
    cwaLoading = true;
    setStatus('Loading official LIX CWA boundary…');
    try {
      cwaData = cwaData || await fetchGeoJson(cwaUrl());
      cwaLayer = buildCwaLayer(cwaData).addTo(map);
      setStatus('LIX CWA outline enabled. Source: NOAA/NWS reference map service.');
    } catch (error) {
      console.error(error);
      cwaToggle.checked = false;
      setStatus(`Could not load LIX CWA boundary: ${error.message}`, true);
    } finally {
      cwaLoading = false;
    }
  }

  async function enableCounties() {
    if (countyLayer) {
      countyLayer.addTo(map);
      setStatus('County/parish outlines enabled.');
      return;
    }
    if (countyLoading) return;
    countyLoading = true;
    setStatus('Loading county/parish boundaries…');
    try {
      countyData = countyData || await fetchGeoJson(countyUrl());
      countyLayer = buildCountyLayer(countyData).addTo(map);
      setStatus('County/parish outlines enabled. Source: U.S. Census TIGERweb.');
    } catch (error) {
      console.error(error);
      countyToggle.checked = false;
      setStatus(`Could not load county/parish boundaries: ${error.message}`, true);
    } finally {
      countyLoading = false;
    }
  }

  cwaToggle.addEventListener('change', event => {
    if (event.target.checked) {
      enableCwa();
    } else if (cwaLayer) {
      cwaLayer.remove();
      setStatus(countyToggle.checked
        ? 'County/parish outlines enabled.'
        : 'State outlines enabled by default.');
    }
  });

  countyToggle.addEventListener('change', event => {
    if (event.target.checked) {
      enableCounties();
    } else if (countyLayer) {
      countyLayer.remove();
      setStatus(cwaToggle.checked
        ? 'LIX CWA outline enabled.'
        : 'State outlines enabled by default.');
    }
  });

  enableStates();
})();
