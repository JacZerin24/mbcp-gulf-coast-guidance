(() => {
  const toggle = document.getElementById('diagnostics-toggle');
  const readoutToggle = document.getElementById('readout-toggle');
  if (!toggle || !readoutToggle || typeof map === 'undefined') return;

  const panel = document.createElement('section');
  panel.id = 'diagnostics-panel';
  panel.className = 'diagnostics-panel is-hidden';
  panel.setAttribute('aria-live', 'polite');
  map.getContainer().appendChild(panel);

  let enabled = false;
  let lastSample = null;

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function number(value, digits = 2) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toFixed(digits) : 'N/A';
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

  function productText(metadata) {
    if (typeof rapProduct === 'function') {
      const product = rapProduct(metadata);
      return `Valid ${product.valid} • RAP ${product.cycle} ${product.fhrText}`;
    }
    return 'Current RAP guidance';
  }

  function contributionRows(sample) {
    const { grid, point } = sample;
    const model = grid?.model;
    if (!model?.variables?.length || !Array.isArray(grid.columns)) return [];
    const columns = new Map(grid.columns.map((name, index) => [name, index]));

    return model.variables.map(variable => {
      const rawIndex = columns.get(variable.key);
      const raw = rawIndex === undefined ? NaN : Number(point[rawIndex]);
      const mean = Number(variable.mean);
      const std = Number(variable.std);
      const coefficient = Number(variable.coefficient);
      const z = Number.isFinite(raw) && Number.isFinite(std) && std !== 0
        ? (raw - mean) / std
        : NaN;
      const contribution = Number.isFinite(z) && Number.isFinite(coefficient)
        ? coefficient * z
        : NaN;
      return { ...variable, raw, z, contribution };
    }).filter(row => Number.isFinite(row.contribution));
  }

  function renderPlaceholder() {
    panel.innerHTML = `
      <div class="diagnostics-header">
        <div><span>Composite diagnostics</span><strong>Parameter drivers</strong></div>
        <button type="button" class="diagnostics-close" aria-label="Hide diagnostics">×</button>
      </div>
      <p class="diagnostics-placeholder">Move the mouse across the guidance map. The seven model ingredients will be ranked here by their standardized contribution at the nearest RAP grid point.</p>
      <p class="diagnostics-footnote">Contribution = coefficient × standardized anomaly. Positive raises the logistic-model probability; negative lowers it. This is a model explanation, not proof of physical causality.</p>
    `;
    wireClose();
  }

  function renderSample(sample) {
    const { metadata, grid, point, distance } = sample;
    const rows = contributionRows(sample);
    if (!rows.length) {
      renderPlaceholder();
      return;
    }

    const model = grid.model;
    const intercept = Number(model.intercept || 0);
    const totalContribution = rows.reduce((sum, row) => sum + row.contribution, 0);
    const logit = intercept + totalContribution;
    const reconstructedProbability = 100 / (1 + Math.exp(-logit));
    const maxMagnitude = Math.max(...rows.map(row => Math.abs(row.contribution)), 0.01);
    const sorted = [...rows].sort(
      (first, second) => Math.abs(second.contribution) - Math.abs(first.contribution)
    );
    const positive = sorted.find(row => row.contribution > 0);
    const negative = sorted.find(row => row.contribution < 0);

    const drivers = sorted.map((row, rank) => {
      const direction = row.contribution >= 0 ? 'positive' : 'negative';
      const width = Math.max(3, Math.abs(row.contribution) / maxMagnitude * 100);
      const sign = row.contribution >= 0 ? '+' : '';
      return `
        <div class="driver-row">
          <div class="driver-heading">
            <span class="driver-rank">${rank + 1}</span>
            <div>
              <strong>${escapeHtml(row.description)}</strong>
              <small>${number(row.raw, row.units === 'J kg-1' ? 0 : 2)} ${escapeHtml(unitLabel(row.units))} • z=${number(row.z, 2)}</small>
            </div>
            <span class="driver-contribution ${direction}">${sign}${number(row.contribution, 2)}</span>
          </div>
          <div class="driver-track"><span class="driver-bar ${direction}" style="width:${width.toFixed(1)}%"></span></div>
        </div>`;
    }).join('');

    panel.innerHTML = `
      <div class="diagnostics-header">
        <div><span>Composite diagnostics</span><strong>Parameter drivers</strong></div>
        <button type="button" class="diagnostics-close" aria-label="Hide diagnostics">×</button>
      </div>
      <div class="diagnostics-summary">
        <div><span>Index</span><strong>${number(point[2], 1)}</strong></div>
        <div><span>Probability</span><strong>${number(point[3], 1)}%</strong></div>
        <div><span>Logit</span><strong>${number(logit, 2)}</strong></div>
      </div>
      <div class="diagnostics-meta">${escapeHtml(productText(metadata))}<br>Nearest grid point ${number(distance, 1)} km from cursor • reconstructed p=${number(reconstructedProbability, 1)}%</div>
      <div class="driver-callouts">
        <span><strong>Largest boost:</strong> ${positive ? escapeHtml(positive.description) : 'none'}</span>
        <span><strong>Largest suppression:</strong> ${negative ? escapeHtml(negative.description) : 'none'}</span>
      </div>
      <div class="driver-list">${drivers}</div>
      <div class="diagnostics-baseline">Intercept / baseline logit: ${number(intercept, 3)}. The bars show additive contributions to that logit before the logistic transform.</div>
      <p class="diagnostics-footnote">Positive and negative signs describe this fitted statistical model only. They should not be interpreted as standalone physical cause-and-effect relationships.</p>
    `;
    wireClose();
  }

  function wireClose() {
    panel.querySelector('.diagnostics-close')?.addEventListener('click', () => {
      toggle.checked = false;
      toggle.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  function setEnabled(value) {
    enabled = value;
    panel.classList.toggle('is-hidden', !enabled);
    if (!enabled) return;

    if (!readoutToggle.checked) {
      readoutToggle.checked = true;
      readoutToggle.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (lastSample) renderSample(lastSample);
    else renderPlaceholder();
  }

  toggle.addEventListener('change', event => setEnabled(event.target.checked));

  window.addEventListener('mbcp:hover-sample', event => {
    lastSample = event.detail || null;
    if (!enabled) return;
    if (lastSample) renderSample(lastSample);
    else renderPlaceholder();
  });

  renderPlaceholder();
})();
