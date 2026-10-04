import { animals, map, monthInput, months, stopPlay } from './sampleMap.js';

const objects = {
  'movement-map': { view: 'map', title: 'Movement map', category: 'Visualizations', description: 'Pronghorn movement · Sublette County, Wyoming · 2025' },
  'movement-table': { view: 'table', title: 'Movement locations', category: 'Visualizations', description: 'Location records and generated rainfall · Synthetic sample data' },
  'movement-chart': { view: 'chart', title: 'Monthly movement', category: 'Visualizations', description: 'Straight-line distance between sampled locations · All four animals' },
  'movement-overview': { view: 'analysis', title: 'Movement overview', category: 'Analysis', description: 'A summary calculated from the synthetic pronghorn tracks' },
  'dry-months': { view: 'analysis', title: 'Dry-month comparison', category: 'Analysis', description: 'Compare sample movement with generated monthly rainfall' },
};
let activeObject = 'movement-map';
let tableFilter = '';
const sectionStates = new WeakMap();
let searching = false;

function selectedPoints() {
  return animals.flatMap(animal => animal.points.filter(point => point.month <= Number(monthInput.value)).map(point => ({ ...point, animal: animal.id })));
}

function monthlySummary() {
  return months.slice(0, Number(monthInput.value) + 1).map((month, index) => {
    const points = animals.map(animal => animal.points[index]);

    return { month, distance: points.reduce((sum, point) => sum + (point.km ?? 0), 0), rainfall: points.reduce((sum, point) => sum + point.rain, 0) / points.length };
  });
}

function heading(title, description) {
  return `<div class="content-heading"><div><h2>${title}</h2><p>${description}</p></div><span class="sample-pill">SYNTHETIC SAMPLE</span></div>`;
}

function metric(label, value, note) {
  return `<article class="metric-card"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`;
}

function tableRows() {
  return selectedPoints().filter(point => `${point.animal} ${months[point.month]} 2025-${String(point.month + 1).padStart(2, '0')}-15`.toLowerCase().includes(tableFilter.toLowerCase())).map(point => `<tr><td>${point.animal}</td><td>2025-${String(point.month + 1).padStart(2, '0')}-15</td><td>${point.lat.toFixed(4)}</td><td>${point.lon.toFixed(4)}</td><td>${point.rain}</td><td>${point.km == null ? '—' : point.km.toFixed(2)}</td></tr>`).join('');
}

function renderTable(content) {
  content.innerHTML = `${heading('Location records', `${selectedPoints().length} records through ${months[Number(monthInput.value)]} 2025 · WGS84 coordinates`)}<div class="content-heading"><p class="content-note" id="table-count"></p><input type="search" class="table-search" id="table-search" placeholder="Filter animal or month…" aria-label="Filter movement locations"></div><div class="table-scroll"><table><thead><tr><th scope="col">Animal ID</th><th scope="col">Date</th><th scope="col">Latitude</th><th scope="col">Longitude</th><th scope="col">Rainfall (mm)</th><th scope="col">Previous segment (km)</th></tr></thead><tbody id="movement-rows"></tbody></table></div><p class="content-note">January has no previous segment. Distances connect sample locations; distances do not measure the complete path traveled.</p>`;
  const search = document.getElementById('table-search');
  search.value = tableFilter;
  const updateRows = () => {
    document.getElementById('movement-rows').innerHTML = tableRows();
    const count = document.getElementById('movement-rows').children.length;
    document.getElementById('table-count').textContent = count ? `${count} matching records` : 'No matching records. Try an animal ID or month.';
  };
  search.addEventListener('input', () => { tableFilter = search.value; updateRows(); });
  updateRows();
}

function renderChart(content) {
  const data = monthlySummary();
  const scale = Math.max(5, Math.ceil(Math.max(...data.map(month => month.distance)) / 5) * 5);
  const total = data.reduce((sum, month) => sum + month.distance, 0);
  const segments = Math.max(0, (data.length - 1) * animals.length);
  const bars = data.map((month, index) => `<div class="chart-column"><div class="chart-value">${index === 0 ? '—' : month.distance.toFixed(1)}</div><div class="chart-track"><div class="chart-bar" style="height:${month.distance / scale * 100}%" title="${month.month}: ${index === 0 ? 'no previous location' : `${month.distance.toFixed(1)} km combined segment distance`}"></div></div><span>${month.month}</span></div>`).join('');
  content.innerHTML = `${heading('Distance between sample locations', `January – ${months[Number(monthInput.value)]} 2025 · Combined monthly distance, in kilometers`)}<div class="metric-grid">${metric('Combined segment distance', `${total.toFixed(1)} km`, 'Sum across all four sample tracks')}${metric('Mean segment distance', segments ? `${(total / segments).toFixed(1)} km` : '—', 'Between consecutive monthly locations')}${metric('Sampled segments', segments, 'January has no previous location')}</div><article class="content-card"><h3>Monthly segment distance (km)</h3><div class="chart-container" role="img" aria-label="Monthly combined distances: ${data.map((month, index) => `${month.month}: ${index ? `${month.distance.toFixed(1)} kilometers` : 'no previous location'}`).join('; ')}">${bars}</div><div class="chart-legend"><i></i> Combined distance · 4 tagged animals</div></article><p class="content-note">These straight-line segments are calculated from synthetic coordinates. The chart does not show the actual distance animals traveled.</p>`;
}

function renderAnalysis(content) {
  const points = selectedPoints();
  const segments = points.filter(point => point.km !== null);
  const total = segments.reduce((sum, point) => sum + point.km, 0);
  const month = Number(monthInput.value);
  const north = animals.filter(animal => animal.points[month].lat > animal.points[0].lat).length;
  if (activeObject === 'movement-overview') {
    content.innerHTML = `${heading('Movement overview', `Calculated from ${points.length} sample locations through ${months[month]} 2025`)}<div class="metric-grid">${metric('Tagged animals', animals.length, 'Synthetic pronghorn tracks')}${metric('Sample locations', points.length, `${month + 1} monthly locations per animal`)}${metric('Combined segment distance', `${total.toFixed(1)} km`, `${segments.length} straight-line segments`)}</div><article class="content-card"><h3>Direction of movement</h3><p>${month === 0 ? 'January provides the first location for each animal. Select a later month to compare movement.' : `${north} of ${animals.length} sample tracks end north of their January location through ${months[month]} 2025.`} ${segments.length ? `Mean distance between consecutive locations is ${(total / segments.length).toFixed(1)} km.` : ''}</p><div class="analysis-actions"><button class="primary-button" data-open-map type="button">View tracks on map</button><button class="plain-button" data-ask="Give me more detail about movement" type="button">Discuss with assistant</button></div></article><article class="content-card"><h3>Inputs and method</h3><p>Input: the four synthetic pronghorn tracks. Method: great-circle distance between consecutive monthly coordinates. Straight connections do not establish the route traveled or the reason for movement.</p></article>`;
    return;
  }

  const data = monthlySummary();
  const dryMonths = data.filter(item => item.rainfall <= 10);
  const rows = data.map((item, index) => `<tr><td>${item.month} 2025</td><td>${item.rainfall.toFixed(1)}</td><td>${index ? item.distance.toFixed(1) : '—'}</td><td>${item.rainfall <= 10 ? 'Low sample rainfall' : 'Above sample threshold'}</td></tr>`).join('');
  content.innerHTML = `${heading('Movement and sample rainfall', `January – ${months[month]} 2025 · Monthly comparison across four sample animals`)}<div class="metric-grid">${metric('Months compared', data.length, 'Through the selected timeline month')}${metric('Low-rainfall months', dryMonths.length, 'Mean generated rainfall ≤ 10 mm')}${metric('Latest mean rainfall', `${data.at(-1).rainfall.toFixed(1)} mm`, `${months[month]} 2025 · generated values`)}</div><article class="content-card"><h3>Monthly comparison</h3><p>The comparison uses generated rainfall at the four sample locations. The 10 mm threshold is a demonstration setting.</p><div class="table-scroll analysis-table"><table><thead><tr><th scope="col">Month</th><th scope="col">Mean rainfall (mm)</th><th scope="col">Combined segments (km)</th><th scope="col">Sample classification</th></tr></thead><tbody>${rows}</tbody></table></div><div class="analysis-actions"><button class="primary-button" data-dry-map type="button">Explore September on map</button><button class="plain-button" data-ask="Show supporting data" type="button">Discuss inputs</button></div></article><p class="content-note">The blue rainfall grid is a static illustration. Generated monthly point values support this table. The comparison does not establish cause.</p>`;
}

function renderObject() {
  const content = document.getElementById('object-content');
  const view = objects[activeObject].view;
  if (view === 'table') renderTable(content);
  if (view === 'chart') renderChart(content);
  if (view === 'analysis') renderAnalysis(content);
}

function openObject(id) {
  const object = objects[id];
  if (!object) return;
  activeObject = id;
  const mapView = object.view === 'map';
  document.getElementById('map-view').hidden = !mapView;
  document.getElementById('object-content').hidden = mapView;
  document.getElementById('object-title').textContent = object.title;
  document.getElementById('object-category').textContent = object.category;
  document.getElementById('object-description').textContent = object.description;
  document.querySelector('.object-tools').hidden = !mapView;
  for (const button of document.querySelectorAll('[data-object]')) {
    const active = button.closest('.view-tabs') ? button.dataset.view === object.view : button.dataset.object === id;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  if (window.innerWidth <= 760) {
    document.getElementById('layers').hidden = true;
    document.getElementById('layers-toggle').setAttribute('aria-expanded', 'false');
  }

  renderObject();
  if (mapView) map.invalidateSize();
}

export function refreshObjectSearch() {
  const query = document.getElementById('object-search').value.trim().toLowerCase();
  let matches = 0;
  for (const section of document.querySelectorAll('.object-section')) {
    if (query && !searching) sectionStates.set(section, section.open);
    const items = [...section.querySelectorAll('.layer, .object-link[data-object]')];
    for (const item of items) {
      item.hidden = !item.textContent.toLowerCase().includes(query);
      if (!item.hidden) matches++;
    }
    section.hidden = query !== '' && items.every(item => item.hidden);
    if (query) section.open = true;
    else if (searching) section.open = sectionStates.get(section) ?? true;
  }
  searching = query !== '';
  document.getElementById('search-empty').hidden = matches > 0;
}

export function initializeObjects({ runQuestion, showChat }) {
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-object]');
    if (button) openObject(button.dataset.object);
    if (event.target.closest('[data-open-map]')) openObject('movement-map');
    if (event.target.closest('[data-dry-map]')) {
      stopPlay();
      openObject('movement-map');
      runQuestion('Show the dry months');
    }
    const ask = event.target.closest('[data-ask]');
    if (ask) { showChat(); runQuestion(ask.dataset.ask); }
  });
  document.addEventListener('open-map', () => openObject('movement-map'));
  document.addEventListener('monthchange', renderObject);
  document.getElementById('object-search').addEventListener('input', refreshObjectSearch);
  openObject('movement-map');
}
