export const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const paths = {
  A14: [[42.63,-110.00],[42.65,-109.96],[42.68,-109.98],[42.71,-109.92],[42.75,-109.88],[42.80,-109.91],[42.85,-109.84],[42.89,-109.80],[42.92,-109.76],[42.91,-109.73],[42.94,-109.78],[42.97,-109.74]],
  A22: [[42.61,-109.90],[42.63,-109.86],[42.66,-109.88],[42.69,-109.82],[42.73,-109.79],[42.78,-109.81],[42.83,-109.75],[42.87,-109.72],[42.90,-109.68],[42.89,-109.66],[42.93,-109.70],[42.95,-109.67]],
  A31: [[42.64,-109.80],[42.65,-109.77],[42.67,-109.79],[42.70,-109.74],[42.73,-109.71],[42.77,-109.73],[42.82,-109.68],[42.86,-109.64],[42.90,-109.61],[42.92,-109.58],[42.94,-109.62],[42.97,-109.59]],
  A40: [[42.60,-109.72],[42.61,-109.69],[42.62,-109.71],[42.64,-109.67],[42.66,-109.64],[42.69,-109.66],[42.78,-109.60],[42.86,-109.56],[42.91,-109.54],[42.90,-109.52],[42.93,-109.55],[42.96,-109.53]],
};

function rainMm(lat, month) {
  return Math.max(2, Math.round((42.98 - lat) * 70 - (month > 5 ? 10 : 0)));
}

function kmBetween(a, b) {
  const dLat = (b[0] - a[0]) * Math.PI / 180;
  const dLon = (b[1] - a[1]) * Math.PI / 180;
  const lat1 = a[0] * Math.PI / 180;
  const lat2 = b[0] * Math.PI / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;

  return 6371 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

export const animals = Object.entries(paths).map(([id, coords]) => ({
  id,
  points: coords.map((coord, month) => ({ month, lat: coord[0], lon: coord[1], rain: rainMm(coord[0], month), km: month === 0 ? null : kmBetween(coords[month - 1], coord) })),
}));
const regionRing = [[42.56,-110.08],[42.56,-109.46],[43.01,-109.46],[43.01,-110.08]];
const plantRing = [[42.78,-110.05],[42.78,-109.50],[42.99,-109.50],[42.99,-110.05]];
export const map = L.map('map', { zoomControl: false });
L.control.zoom({ position: 'bottomright' }).addTo(map);
L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map);
map.attributionControl.setPrefix(false);
export const basemap = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);
export const groups = { plants: L.layerGroup(), rain: L.layerGroup(), tracks: L.layerGroup(), area: L.layerGroup() };

L.polygon(plantRing, { stroke: false, fillColor: '#5c6b3a', fillOpacity: 0.28, interactive: false }).addTo(groups.plants);
for (let lat = 42.56; lat <= 42.92; lat += 0.09) {
  for (let lon = -110.08; lon <= -109.56; lon += 0.13) {
    const mm = rainMm(lat + 0.04, 8);
    L.rectangle([[lat, lon], [Math.min(lat + 0.09, 43.01), Math.min(lon + 0.13, -109.46)]], {
      stroke: false, fillColor: '#1d70b8', fillOpacity: 0.06 + Math.min(mm, 32) / 70, interactive: false,
    }).addTo(groups.rain);
  }
}
L.polygon(regionRing, { color: '#687973', weight: 1.5, dashArray: '6 5', fillOpacity: 0, interactive: false }).addTo(groups.area);
groups.rain.addTo(map);
groups.tracks.addTo(map);
groups.area.addTo(map);
export const bounds = L.latLngBounds(regionRing);
let framed = false;
new ResizeObserver(() => {
  map.invalidateSize();
  if (framed) return;
  const size = map.getSize();
  if (size.x < 40 || size.y < 40) return;

  map.fitBounds(bounds, { paddingTopLeft: [30, 65], paddingBottomRight: [30, 110], maxZoom: 11 });
  framed = true;
}).observe(document.getElementById('map'));

const layerMeta = [
  { id: 'tracks', name: 'Pronghorn movement', legend: "<i class='dot'></i> 4 animals · 48 sample records", on: true },
  { id: 'rain', name: 'Monthly rainfall', legend: "<i class='swatch'></i> Synthetic rainfall grid", on: true },
  { id: 'plants', name: 'Vegetation', legend: "<i class='swatch plant'></i> Sample habitat extent", on: false },
  { id: 'area', name: 'Study boundary', legend: "<i class='rule'></i> Sublette County extent", on: true },
];
export const list = document.getElementById('layer-list');
for (const layer of layerMeta) {
  const row = document.createElement('div');
  row.className = 'layer';
  row.innerHTML = `<label><input type="checkbox" ${layer.on ? 'checked' : ''} data-layer="${layer.id}" aria-label="Show ${layer.name}"><span><span class="layer-name">${layer.name}</span><span class="legend">${layer.legend}</span></span></label><button class="text-button" type="button" data-zoom="${layer.id}" aria-label="Zoom to ${layer.name}" title="Zoom to ${layer.name}">↗</button>`;
  list.appendChild(row);
}
list.addEventListener('change', event => {
  const box = event.target.closest('input[data-layer]');
  if (!box) return;

  if (box.checked) groups[box.dataset.layer].addTo(map);
  else map.removeLayer(groups[box.dataset.layer]);
});
list.addEventListener('click', event => {
  const button = event.target.closest('[data-zoom]');
  if (!button) return;

  document.dispatchEvent(new CustomEvent('open-map'));
  const id = button.dataset.zoom;
  const extent = id === 'tracks' ? animals.flatMap(animal => animal.points.map(point => [point.lat, point.lon])) : id === 'plants' ? plantRing : bounds;
  map.flyToBounds(extent, { padding: [40, 40], duration: reduceMotion ? 0 : 0.6, maxZoom: 11 });
});

export const monthInput = document.getElementById('month');
const when = document.getElementById('when');
const inspect = document.getElementById('inspect');
export const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function drawTracks(monthIndex) {
  groups.tracks.clearLayers();
  for (const animal of animals) {
    const visible = animal.points.filter(point => point.month <= monthIndex);
    if (visible.length > 1) L.polyline(visible.map(point => [point.lat, point.lon]), { color: '#27816b', weight: 2.5, opacity: 0.8 }).addTo(groups.tracks);
    for (const point of visible) {
      const current = point.month === monthIndex;
      const marker = L.circleMarker([point.lat, point.lon], { radius: current ? 7 : 4.5, color: current ? '#174e3f' : '#ffffff', weight: 2, fillColor: '#24765f', fillOpacity: 1 });
      marker.on('click', event => {
        L.DomEvent.stopPropagation(event);
        document.getElementById('inspect-title').textContent = animal.id;
        document.getElementById('inspect-when').textContent = `15 ${months[point.month]} 2025`;
        document.getElementById('inspect-move').textContent = point.km == null ? 'First location this year' : `${point.km.toFixed(1)} km from the previous location`;
        document.getElementById('inspect-rain').textContent = `Sample rainfall: ${point.rain} mm`;
        inspect.hidden = false;
      });
      marker.addTo(groups.tracks);
    }
  }
}

export function setMonth(index) {
  monthInput.value = String(index);
  when.textContent = `15 ${months[index]} 2025`;
  drawTracks(index);
  inspect.hidden = true;
  document.dispatchEvent(new CustomEvent('monthchange'));
}
setMonth(11);
monthInput.addEventListener('input', () => { stopPlay(); setMonth(Number(monthInput.value)); });
map.on('click', () => { inspect.hidden = true; });
document.getElementById('inspect-close').addEventListener('click', () => { inspect.hidden = true; });
const playButton = document.getElementById('play');
let timer = null;

export function stopPlay() {
  clearInterval(timer);
  timer = null;
  playButton.textContent = 'Play';
  playButton.setAttribute('aria-pressed', 'false');
}
playButton.addEventListener('click', () => {
  if (timer) { stopPlay(); return; }

  playButton.textContent = 'Pause';
  playButton.setAttribute('aria-pressed', 'true');
  if (Number(monthInput.value) >= 11) setMonth(0);
  timer = setInterval(() => {
    const next = Number(monthInput.value) + 1;
    if (next > 11) { stopPlay(); return; }

    setMonth(next);
  }, reduceMotion ? 700 : 450);
});
