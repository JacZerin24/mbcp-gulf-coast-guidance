(() => {
  const referenceButton = document.getElementById('reference-button');
  const helpButton = document.getElementById('help-button');
  const referenceModal = document.getElementById('reference-modal');
  const helpModal = document.getElementById('help-modal');
  const referenceContent = document.getElementById('reference-content');
  const helpContent = document.getElementById('help-content');
  if (!referenceButton || !helpButton || !referenceModal || !helpModal) return;

  let referenceLoaded = false;

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function unitLabel(units) {
    const labels = {
      'degC': '°C',
      'J kg-1': 'J kg⁻¹',
      'degC km-1': '°C km⁻¹',
      'K': 'K'
    };
    return labels[units] || units || '';
  }

  function compactUtc(value) {
    if (typeof window.compactUtc === 'function') return window.compactUtc(value);
    const match = String(value || '').match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):/);
    return match ? `${match[1]} ${match[2]}Z` : String(value || 'unknown');
  }

  async function fetchJson(path) {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Failed to load ${path}`);
    return response.json();
  }

  async function latestMetadata() {
    if (typeof latestMeta !== 'undefined' && latestMeta) return latestMeta;
    return fetchJson(`data/latest.json?t=${Date.now()}`);
  }

  async function referenceData() {
    const metadata = await latestMetadata();
    if (!metadata?.readout?.file) {
      throw new Error('Model reference data have not been generated for this deployment yet.');
    }
    const grid = await fetchJson(`data/${metadata.readout.file}?v=${Date.now()}`);
    return { metadata, grid };
  }

  function openModal(modal) {
    modal.classList.remove('is-hidden');
    document.body.classList.add('modal-open');
    modal.querySelector('.modal-close')?.focus();
  }

  function closeModal(modal) {
    modal.classList.add('is-hidden');
    if (referenceModal.classList.contains('is-hidden') && helpModal.classList.contains('is-hidden')) {
      document.body.classList.remove('modal-open');
    }
  }

  function modelTable(model) {
    return model.variables.map(variable => `
      <tr>
        <td><strong>${escapeHtml(variable.description)}</strong><small>${escapeHtml(variable.key)}</small></td>
        <td>${Number(variable.mean).toFixed(variable.units === 'J kg-1' ? 0 : 2)} ${escapeHtml(unitLabel(variable.units))}</td>
        <td>${Number(variable.std).toFixed(variable.units === 'J kg-1' ? 0 : 2)} ${escapeHtml(unitLabel(variable.units))}</td>
        <td class="coefficient-number ${Number(variable.coefficient) >= 0 ? 'positive' : 'negative'}">${Number(variable.coefficient) >= 0 ? '+' : ''}${Number(variable.coefficient).toFixed(3)}</td>
      </tr>`).join('');
  }

  function coefficientChart(model) {
    const max = Math.max(...model.variables.map(variable => Math.abs(Number(variable.coefficient))), 0.01);
    return model.variables
      .slice()
      .sort((a, b) => Math.abs(Number(b.coefficient)) - Math.abs(Number(a.coefficient)))
      .map(variable => {
        const coefficient = Number(variable.coefficient);
        const width = Math.max(3, Math.abs(coefficient) / max * 100);
        const direction = coefficient >= 0 ? 'positive' : 'negative';
        return `
          <div class="reference-bar-row">
            <div class="reference-bar-label"><span>${escapeHtml(variable.description)}</span><strong class="${direction}">${coefficient >= 0 ? '+' : ''}${coefficient.toFixed(3)}</strong></div>
            <div class="reference-bar-track"><span class="reference-bar ${direction}" style="width:${width.toFixed(1)}%"></span></div>
          </div>`;
      }).join('');
  }

  function renderReference(metadata, grid) {
    const model = grid.model;
    if (!model?.variables?.length) {
      throw new Error('This readout file does not contain model-reference metadata.');
    }
    const cycle = metadata.cycle || {};
    const valid = compactUtc(cycle.valid_time_utc || cycle.cycle_time_utc);
    const cycleTime = compactUtc(cycle.cycle_time_utc);
    const forecastHour = Number(cycle.forecast_hour || 0);
    const images = metadata.images || {};
    const cacheKey = encodeURIComponent(`${cycle.cycle_time_utc || ''}-${cycle.valid_time_utc || ''}-${forecastHour}`);

    referenceContent.innerHTML = `
      <section class="reference-hero">
        <div>
          <div class="modal-kicker">${escapeHtml(model.version)}</div>
          <h3>${escapeHtml(model.name)}</h3>
          <p>${escapeHtml(model.description)}</p>
        </div>
        <div class="reference-stat-grid">
          <div><span>Predictors</span><strong>${model.variables.length}</strong></div>
          <div><span>Intercept</span><strong>${Number(model.intercept).toFixed(3)}</strong></div>
          <div><span>Current valid</span><strong>${escapeHtml(valid)}</strong></div>
          <div><span>RAP source</span><strong>${escapeHtml(cycleTime)} f${String(forecastHour).padStart(2, '0')}</strong></div>
        </div>
      </section>

      <section class="modal-section">
        <h3>How the composite is calculated</h3>
        <p>Each environmental predictor is standardized using the training mean and standard deviation, multiplied by its fitted logistic coefficient, and added to the model intercept.</p>
        <div class="formula-card">
          <code>zᵢ = (xᵢ − meanᵢ) / stdᵢ</code>
          <code>logit = intercept + Σ(coefficientᵢ × zᵢ)</code>
          <code>probability = 1 / (1 + exp(−logit))</code>
          <code>0–10 index = round(probability × 10)</code>
        </div>
        <p class="modal-note">A positive coefficient means a positive standardized anomaly raises the fitted logit, while a negative coefficient lowers it. Coefficient signs and driver bars describe the fitted statistical model and should not be interpreted as standalone physical causality.</p>
      </section>

      <section class="modal-section">
        <h3>Standardized coefficient profile</h3>
        <p>Because the predictors are standardized before fitting, coefficient magnitudes can be compared as a first-order view of how strongly a one-standard-deviation change affects the model logit.</p>
        <div class="reference-chart">${coefficientChart(model)}</div>
      </section>

      <section class="modal-section">
        <h3>Training normalization statistics</h3>
        <div class="table-scroll">
          <table class="reference-table">
            <thead><tr><th>Predictor</th><th>Training mean</th><th>Training std</th><th>Coefficient</th></tr></thead>
            <tbody>${modelTable(model)}</tbody>
          </table>
        </div>
      </section>

      <section class="modal-section">
        <h3>Current product imagery</h3>
        <p>These are the static renderings from the same currently published RAP-valid guidance.</p>
        <div class="reference-images">
          <a href="${escapeHtml(images.index || 'assets/latest_index.png')}?v=${cacheKey}" target="_blank" rel="noreferrer"><img src="${escapeHtml(images.index || 'assets/latest_index.png')}?v=${cacheKey}" alt="Current experimental damaging-wind index map"><span>Current 0–10 index</span></a>
          <a href="${escapeHtml(images.probability || 'assets/latest_probability.png')}?v=${cacheKey}" target="_blank" rel="noreferrer"><img src="${escapeHtml(images.probability || 'assets/latest_probability.png')}?v=${cacheKey}" alt="Current experimental conditional damaging-wind probability map"><span>Current conditional probability</span></a>
        </div>
      </section>

      <section class="modal-section validation-section">
        <h3>Validation status</h3>
        <p><strong>No independent AUC, Brier score, reliability diagram, ROC curve, or similar verification statistics are stored in this repository yet.</strong> Those values are intentionally not shown or estimated here.</p>
        <p>The current implementation is a research prototype trained on warm-season damaging-wind and null-convection cases. The original research used point-based RAP sounding extraction, while this web system applies the model to gridded RAP fields. Direct comparison against the original case dataset remains an important validation step.</p>
      </section>

      <section class="modal-section">
        <h3>Data and boundary sources</h3>
        <ul class="reference-list">
          <li><strong>Environment:</strong> RAP 13-km pressure-level guidance accessed through Herbie.</li>
          <li><strong>Composite:</strong> refined Gulf Coast standardized logistic model in <code>config/refined_gulf_coast_model.json</code>.</li>
          <li><strong>CWA outline:</strong> NOAA/NWS Reference Map Feature Service.</li>
          <li><strong>County/parish outlines:</strong> U.S. Census Bureau TIGERweb county-equivalent boundaries.</li>
          <li><strong>Display smoothing:</strong> visualization only; hover readouts and driver diagnostics use unsmoothed model values.</li>
        </ul>
      </section>
    `;
  }

  function renderHelp() {
    helpContent.innerHTML = `
      <nav class="help-nav" aria-label="Help topics">
        <a href="#help-overview">Overview</a>
        <a href="#help-layers">Layers</a>
        <a href="#help-time">RAP timing</a>
        <a href="#help-readout">Readout</a>
        <a href="#help-diagnostics">Diagnostics</a>
        <a href="#help-boundaries">Boundaries</a>
        <a href="#help-updates">Updates</a>
        <a href="#help-caveats">Caveats</a>
        <a href="#help-troubleshooting">Troubleshooting</a>
      </nav>

      <section id="help-overview" class="modal-section">
        <h3>What this page is</h3>
        <p>This is an experimental, RAP-based Gulf Coast conditional damaging-wind guidance display. It answers: <strong>if thunderstorms develop or are ongoing, how favorable is the environment for damaging convective wind?</strong> It does not forecast convective initiation by itself and it is not official NWS operational guidance.</p>
      </section>

      <section id="help-layers" class="modal-section">
        <h3>Displayed guidance layers</h3>
        <p><strong>0–10 Index:</strong> the fitted conditional probability multiplied by 10 and rounded. It is intended as a quick favorability scale.</p>
        <p><strong>Probability:</strong> the raw fitted conditional damaging-wind probability from the logistic model, displayed as a percentage.</p>
        <p>The colored map is lightly smoothed for presentation. That smoothing does not change the hover readout or parameter diagnostics.</p>
      </section>

      <section id="help-time" class="modal-section">
        <h3>Valid time, cycle, and forecast hour</h3>
        <p>The system tries to show guidance valid for the <strong>current UTC hour</strong>. It first looks for the current-hour RAP <code>f00</code>. If that analysis is not available yet, it falls back to an earlier cycle at the forecast hour valid now.</p>
        <div class="example-card"><strong>Example:</strong> At 14:54Z, the preferred product is 14Z f00. If it is unavailable, the page can use 13Z f01 valid 14Z. When 14Z f00 arrives, it replaces the fallback even though both are valid at 14Z.</div>
      </section>

      <section id="help-readout" class="modal-section">
        <h3>Map hover readout</h3>
        <p>Enable <strong>Hover readout</strong> and move the mouse over the map. The box beside the cursor samples the nearest valid RAP grid point and displays the unsmoothed 0–10 index and conditional probability, grid coordinates, sampling distance, valid time, cycle, and forecast hour.</p>
        <p>Samples farther than 40 km from a valid model point are rejected so clicks well outside the guidance domain do not produce misleading values.</p>
      </section>

      <section id="help-diagnostics" class="modal-section">
        <h3>Composite parameter diagnostics</h3>
        <p>Enable <strong>Show parameter drivers</strong>. The diagnostics panel automatically enables the hover readout and updates as you move across the map.</p>
        <p>For each of the seven predictors it shows the raw RAP-derived value, standardized anomaly <code>z</code>, and additive contribution <code>coefficient × z</code>. Predictors are ranked by the absolute size of that contribution. Positive values raise the model logit and negative values lower it.</p>
        <p>The panel also reconstructs the logit and probability as a consistency check. These contribution values explain the fitted model calculation, not a physical cause-and-effect attribution.</p>
      </section>

      <section id="help-boundaries" class="modal-section">
        <h3>CWA and county/parish outlines</h3>
        <p><strong>LIX CWA outline</strong> loads the official NWS County Warning Area boundary. <strong>County/parish outlines</strong> load Census TIGERweb county-equivalent boundaries intersecting the guidance domain. These are independent reference overlays and do not affect the model values.</p>
      </section>

      <section id="help-updates" class="modal-section">
        <h3>How automatic updating works</h3>
        <p>GitHub Actions launches watcher runs at approximately <strong>:07 and :37 each hour</strong>. Each scheduled watcher checks RAP availability every <strong>2 minutes for up to 24 minutes</strong>. When it finds a preferred product newer than the published one, it regenerates the maps, static images, metadata, and unsmoothed readout/diagnostic grid, then deploys them to GitHub Pages.</p>
        <p>An open webpage checks <code>latest.json</code> every <strong>2 minutes</strong> and also checks when you return to a hidden tab. The hover-readout data independently checks for a new published version about every 125 seconds.</p>
      </section>

      <section id="help-caveats" class="modal-section">
        <h3>Scientific caveats</h3>
        <ul class="reference-list">
          <li>The model is conditional on convection and does not forecast storm initiation.</li>
          <li>The training cases are damaging-wind reports and null-convection cases, not a perfect catalogue of confirmed microbursts.</li>
          <li>The research dataset used point-based RAP sounding extraction; this implementation uses gridded RAP calculations.</li>
          <li>Derived fields such as DCAPE, theta-e deficit, and lapse rates should continue to be checked against the original research extraction.</li>
          <li>The 0–10 index is a presentation transform of model probability, not a separate physical parameter.</li>
          <li>Independent operational verification statistics are not yet included in this repository.</li>
        </ul>
      </section>

      <section id="help-troubleshooting" class="modal-section">
        <h3>Troubleshooting</h3>
        <p><strong>Map looks stale:</strong> compare the Valid and RAP cycle/fXX text at the top. The browser normally refreshes automatically; a hard refresh can be used after webpage-code changes.</p>
        <p><strong>Hover readout unavailable:</strong> the latest guidance run may have failed before creating <code>readout_grid.json</code>, or the browser may still be on a deployment generated before diagnostics were added.</p>
        <p><strong>Boundary layer fails:</strong> those overlays are loaded from external NOAA/NWS and Census services, so a temporary service/network problem can affect them without affecting the RAP guidance itself.</p>
        <p><strong>Diagnostics panel shows no values:</strong> enable hover readout and place the cursor inside the valid guidance coverage.</p>
      </section>
    `;
  }

  referenceButton.addEventListener('click', async () => {
    openModal(referenceModal);
    if (referenceLoaded) return;
    referenceContent.innerHTML = '<p>Loading model reference and current imagery…</p>';
    try {
      const { metadata, grid } = await referenceData();
      renderReference(metadata, grid);
      referenceLoaded = true;
    } catch (error) {
      console.error(error);
      referenceContent.innerHTML = `<div class="modal-error">${escapeHtml(error.message || 'Could not load reference information.')}</div>`;
    }
  });

  renderHelp();
  helpButton.addEventListener('click', () => openModal(helpModal));

  document.querySelectorAll('[data-close-modal]').forEach(button => {
    button.addEventListener('click', () => {
      const modal = document.getElementById(button.dataset.closeModal);
      if (modal) closeModal(modal);
    });
  });

  [referenceModal, helpModal].forEach(modal => {
    modal.addEventListener('mousedown', event => {
      if (event.target === modal) closeModal(modal);
    });
  });

  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (!referenceModal.classList.contains('is-hidden')) closeModal(referenceModal);
    if (!helpModal.classList.contains('is-hidden')) closeModal(helpModal);
  });
})();
