import {
  animals,
  basemap,
  bounds,
  groups,
  list,
  map,
  monthInput,
  months,
  reduceMotion,
  setMonth,
  stopPlay,
} from "./sampleMap.js";
import { initializeObjects, refreshObjectSearch } from "./workspaceObjects.js";
const chat = document.getElementById("chat");
const handle = document.getElementById("chat-handle");
const thread = document.getElementById("thread");
const field = document.getElementById("question");
const workspace = document.querySelector(".workspace");
const launcher = document.getElementById("chat-launcher");
let drag = null;
function positionChat(x, y) {
  const width = workspace.clientWidth;
  const height = workspace.clientHeight;
  chat.style.left = `${Math.max(0, Math.min(x, width - chat.offsetWidth))}px`;
  chat.style.top = `${Math.max(0, Math.min(y, height - chat.offsetHeight))}px`;
  chat.style.right = "auto";
}
handle.addEventListener("pointerdown", (event) => {
  if (event.target.closest("button") || event.button !== 0) return;
  const r = chat.getBoundingClientRect();
  drag = { x: event.clientX - r.left, y: event.clientY - r.top };
  handle.setPointerCapture(event.pointerId);
  event.preventDefault();
});
handle.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const r = workspace.getBoundingClientRect();
  positionChat(event.clientX - r.left - drag.x, event.clientY - r.top - drag.y);
});
function finishDrag() {
  drag = null;
}
handle.addEventListener("pointerup", finishDrag);
handle.addEventListener("pointercancel", finishDrag);
handle.addEventListener("lostpointercapture", finishDrag);
handle.addEventListener("keydown", (event) => {
  if (
    event.target !== handle ||
    !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
  )
    return;
  event.preventDefault();
  const r = chat.getBoundingClientRect(),
    w = workspace.getBoundingClientRect();
  const step = event.shiftKey ? 40 : 12;
  positionChat(
    r.left -
      w.left +
      (event.key === "ArrowLeft"
        ? -step
        : event.key === "ArrowRight"
          ? step
          : 0),
    r.top -
      w.top +
      (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0),
  );
});
new ResizeObserver(() => {
  if (chat.hidden) return;
  if (chat.style.left) {
    const r = chat.getBoundingClientRect(),
      w = workspace.getBoundingClientRect();
    positionChat(r.left - w.left, r.top - w.top);
  }
}).observe(workspace);
function showChat() {
  if (window.innerWidth <= 760) toggleLayers(false);
  chat.hidden = false;
  chat.classList.remove("collapsed");
  launcher.hidden = true;
  document.getElementById("minimize").setAttribute("aria-expanded", "true");
  if (chat.style.left) {
    const r = chat.getBoundingClientRect(),
      w = workspace.getBoundingClientRect();
    positionChat(r.left - w.left, r.top - w.top);
  }
  field.focus();
}
document.getElementById("chat-close").addEventListener("click", () => {
  chat.hidden = true;
  launcher.hidden = false;
  launcher.focus();
});
document.getElementById("minimize").addEventListener("click", (event) => {
  const collapsed = chat.classList.toggle("collapsed");
  event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
  event.currentTarget.setAttribute(
    "aria-label",
    collapsed ? "Expand assistant" : "Minimize assistant",
  );
  if (chat.style.left) {
    const r = chat.getBoundingClientRect(),
      w = workspace.getBoundingClientRect();
    positionChat(r.left - w.left, r.top - w.top);
  }
});
launcher.addEventListener("click", showChat);
document.getElementById("rail-chat").addEventListener("click", showChat);
function toggleLayers(force) {
  const panel = document.getElementById("layers");
  panel.hidden = force === undefined ? !panel.hidden : !force;
  document
    .getElementById("layers-toggle")
    .classList.toggle("active", !panel.hidden);
  document
    .getElementById("layers-toggle")
    .setAttribute("aria-expanded", String(!panel.hidden));
  map.invalidateSize();
}
document
  .getElementById("layers-toggle")
  .addEventListener("click", () => toggleLayers());
document
  .getElementById("layers-close")
  .addEventListener("click", () => toggleLayers(false));
if (window.innerWidth <= 760) toggleLayers(false);
function zoomExtent() {
  document.dispatchEvent(new CustomEvent("open-map"));
  map.flyToBounds(bounds, {
    padding: [50, 50],
    duration: reduceMotion ? 0 : 0.6,
    maxZoom: 11,
  });
}
document.getElementById("frame").addEventListener("click", zoomExtent);
let toastTimer;
function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 3500);
}
document.getElementById("basemap").addEventListener("click", () => {
  if (!map.hasLayer(basemap)) basemap.addTo(map);
  toast("Standard OpenStreetMap basemap");
});
function updateLayerStatus() {
  document.getElementById("map-status").textContent =
    `${document.querySelectorAll("#layer-list input:checked").length} layers visible`;
}
list.addEventListener("change", updateLayerStatus);
function setLayer(id, visible) {
  const checkbox = list.querySelector(`input[data-layer="${id}"]`);
  checkbox.checked = visible;
  if (visible) groups[id].addTo(map);
  else map.removeLayer(groups[id]);
  updateLayerStatus();
}
function addExchange(question, answer, actions = []) {
  const article = document.createElement("article");
  article.className = "exchange new-response";
  if (question) {
    const label = document.createElement("p");
    label.className = "label";
    label.textContent = "YOU";
    const q = document.createElement("p");
    q.className = "question";
    q.textContent = question;
    article.append(label, q);
  }
  const brand = document.createElement("div");
  brand.className = "answer-brand";
  brand.textContent = "Habitat Watch";
  const response = document.createElement("p");
  response.className = "finding";
  response.textContent = answer;
  article.append(brand, response);
  if (actions.length) {
    const buttons = document.createElement("div");
    buttons.className = "followups";
    for (const action of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action;
      button.dataset.prompt = action;
      buttons.append(button);
    }
    article.append(buttons);
  }
  thread.append(article);
  thread.scrollTo({
    top: thread.scrollHeight,
    behavior: reduceMotion ? "instant" : "smooth",
  });
}
function runQuestion(text) {
  const q = text.toLowerCase();
  const hide = /\b(hide|remove|turn off)\b/.test(q);
  let answer,
    actions = ["Give me more detail about movement", "Show supporting data"];
  const mentionedMonth = months.findIndex((month) =>
    new RegExp(
      `\\b${month}(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\\b`,
      "i",
    ).test(text),
  );
  if (
    /dry|drier|vegetation|habitat layer|rainfall|\brain\b|zoom|extent|center|centre|track|boundary/.test(
      q,
    ) ||
    mentionedMonth >= 0
  ) {
    document.dispatchEvent(new CustomEvent("open-map"));
  }
  if (/forecast|predict|next year|next month/.test(q)) {
    answer =
      "A forecast needs observed movement and environmental data, plus a model evaluated on later records. This preview contains synthetic samples, so it cannot produce a supported forecast. You can add geographic data to explore on the map.";
    actions = ["How do I add data?", "Show supporting data"];
  } else if (/dry|drier/.test(q)) {
    stopPlay();
    setMonth(8);
    setLayer("rain", true);
    answer =
      "Showing September 2025, one of the low-rainfall months in the generated sample. The timeline now stops at September; click a track point to inspect its sample rainfall and distance from the previous location.";
    actions = [
      "Show December",
      "Hide rainfall",
      "Give me more detail about movement",
    ];
  } else if (/vegetation|habitat layer/.test(q)) {
    setLayer("plants", !hide);
    answer = hide
      ? "Vegetation hidden from the map."
      : "Added the sample vegetation extent to the map. The green area is a demonstration overlay; it is not a measured vegetation index.";
  } else if (/rainfall|\brain\b/.test(q)) {
    setLayer("rain", !hide);
    answer = hide
      ? "Rainfall hidden. The movement tracks remain available for comparison."
      : "Showing the sample rainfall grid. Darker cells represent wetter values. This grid is a fixed illustrative backdrop; point details contain the generated value for their selected month.";
    actions = ["Show the dry months", "Hide rainfall"];
  } else if (mentionedMonth >= 0) {
    stopPlay();
    setMonth(mentionedMonth);
    answer = `Showing movement through ${months[mentionedMonth]} 2025. Each animal has one synthetic location per month. Use the timeline to compare earlier or later locations.`;
    actions = ["Show December", "Zoom to the tracks"];
  } else if (/zoom|extent|center|centre/.test(q)) {
    setLayer("tracks", true);
    zoomExtent();
    answer =
      "Centered the map on the study area and made movement tracks visible. Select a point for the animal ID, month, distance, and sample rainfall.";
  } else if (/source|supporting|evidence|related data|dataset/.test(q)) {
    answer =
      "Available here: 48 synthetic movement locations for 4 pronghorn, generated monthly rainfall, a study boundary, and an illustrative vegetation extent. No live catalog is connected. Add a GeoJSON file or a CSV with latitude/longitude columns to explore your own data locally.";
    actions = ["Show vegetation on the map", "How do I add data?"];
  } else if (/add data|upload|attach|import|give data|my data/.test(q)) {
    answer =
      "Use the + button below to add GeoJSON or CSV data. CSV files need latitude and longitude columns (lat/lon also work). The file is read locally and added as a map layer; this prototype does not run scientific analysis on imported records.";
    actions = [];
  } else if (/detail|movement|distance|far|north|pattern/.test(q)) {
    const distances = animals.flatMap((a) =>
      a.points.slice(1).map((p) => p.km),
    );
    const total = distances.reduce((a, b) => a + b, 0);
    answer = `The 4 synthetic tracks contain 48 locations and 44 month-to-month segments. Mean distance between sampled locations is ${(total / distances.length).toFixed(1)} km; combined segment distance is ${total.toFixed(1)} km. All tracks end north of their January location. Straight-line segments do not measure the full path traveled or explain the cause of movement.`;
    actions = ["Zoom to the tracks", "Show supporting data"];
  } else if (/track|boundary/.test(q)) {
    const id = /boundary/.test(q) ? "area" : "tracks";
    setLayer(id, !hide);
    answer = `${id === "area" ? "Study boundary" : "Movement tracks"} ${hide ? "hidden" : "shown"} on the map.`;
  } else {
    answer =
      "This preview understands map commands for rainfall, vegetation, tracks, zoom, and months in 2025. It can summarize the synthetic movement records and let you add local data. A general chatbot and live data retrieval are not connected yet.";
    actions = ["Show rainfall", "Show August", "How do I add data?"];
  }
  addExchange(text, answer, actions);
}
document.getElementById("ask").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = field.value.trim();
  if (!text) return;
  runQuestion(text);
  field.value = "";
  field.focus();
});
field.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    document.getElementById("ask").requestSubmit();
  }
});
thread.addEventListener("click", (event) => {
  const button = event.target.closest("[data-prompt]");
  if (button) runQuestion(button.dataset.prompt);
});
const fileInput = document.getElementById("file-input");
for (const id of ["attach", "add-data", "rail-add"])
  document
    .getElementById(id)
    .addEventListener("click", () => fileInput.click());
// CSV parser handles quoted fields, escaped quotes, and CRLF lines.
function parseCSV(text) {
  const rows = [];
  let row = [],
    cell = "",
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = !quoted;
    } else if (c === "," && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((c === "\n" || c === "\r") && !quoted) {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      if (row.some((v) => v.trim())) rows.push(row);
      row = [];
      cell = "";
    } else cell += c;
  }
  if (quoted) throw new Error("CSV has an unclosed quoted field.");
  row.push(cell);
  if (row.some((v) => v.trim())) rows.push(row);
  return rows;
}
let importedCount = 0;
fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  try {
    if (file.size > 5 * 1024 * 1024)
      throw new Error("Choose a file smaller than 5 MB for this preview.");
    const text = await file.text();
    let data;
    if (/\.csv$/i.test(file.name)) {
      const rows = parseCSV(text);
      const header = (rows.shift() || []).map((v) =>
        v
          .replace(/^\uFEFF/, "")
          .trim()
          .toLowerCase(),
      );
      const lat = header.findIndex((v) => ["lat", "latitude"].includes(v)),
        lon = header.findIndex((v) => ["lon", "lng", "longitude"].includes(v));
      if (lat < 0 || lon < 0)
        throw new Error(
          "CSV needs latitude and longitude columns (or lat and lon).",
        );
      if (!rows.length) throw new Error("CSV contains no data rows.");
      data = {
        type: "FeatureCollection",
        features: rows.map((row, i) => {
          const y = Number(row[lat]),
            x = Number(row[lon]);
          if (
            !row[lat]?.trim() ||
            !row[lon]?.trim() ||
            !Number.isFinite(x) ||
            !Number.isFinite(y) ||
            Math.abs(x) > 180 ||
            Math.abs(y) > 90
          )
            throw new Error(`Invalid coordinates in CSV row ${i + 2}.`);
          return {
            type: "Feature",
            geometry: { type: "Point", coordinates: [x, y] },
            properties: Object.fromEntries(
              header.map((key, j) => [key, row[j] || ""]),
            ),
          };
        }),
      };
    } else {
      data = JSON.parse(text);
      if (
        ![
          "FeatureCollection",
          "Feature",
          "Point",
          "MultiPoint",
          "LineString",
          "MultiLineString",
          "Polygon",
          "MultiPolygon",
          "GeometryCollection",
        ].includes(data.type)
      )
        throw new Error("Choose a valid GeoJSON file.");
    }
    const features = data.type === "FeatureCollection" ? data.features : [data];
    if (!Array.isArray(features) || !features.length || features.length > 10000)
      throw new Error("Choose a file with 1–10,000 features.");
    function validateGeometry(g) {
      if (!g) throw new Error("Every feature needs a geometry.");
      if (g.type === "GeometryCollection") {
        if (!Array.isArray(g.geometries))
          throw new Error("Invalid geometry collection.");
        g.geometries.forEach(validateGeometry);
        return;
      }
      function visit(coords) {
        if (!Array.isArray(coords) || !coords.length)
          throw new Error("Invalid coordinates.");
        if (typeof coords[0] === "number") {
          if (
            coords.length < 2 ||
            !Number.isFinite(coords[0]) ||
            !Number.isFinite(coords[1]) ||
            Math.abs(coords[0]) > 180 ||
            Math.abs(coords[1]) > 90
          )
            throw new Error(
              "Coordinates must use longitude/latitude in WGS84.",
            );
        } else coords.forEach(visit);
      }
      visit(g.coordinates);
    }
    features.forEach((f) =>
      validateGeometry(f.type === "Feature" ? f.geometry : f),
    );
    const imported = L.geoJSON(data, {
      style: { color: "#b17a40", weight: 2, fillOpacity: 0.12 },
      pointToLayer: (_, latlng) =>
        L.circleMarker(latlng, {
          radius: 5,
          color: "#fff",
          weight: 1.5,
          fillColor: "#b17a40",
          fillOpacity: 1,
        }),
      onEachFeature: (feature, layer) => {
        const content = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = file.name;
        content.append(title);
        for (const [key, value] of Object.entries(
          feature.properties || {},
        ).slice(0, 8)) {
          const line = document.createElement("p");
          line.textContent = `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`;
          content.append(line);
        }
        layer.bindPopup(content);
      },
    });
    if (!imported.getBounds().isValid())
      throw new Error("The file has no map geometry.");
    const id = `imported-${++importedCount}`;
    groups[id] = imported;
    imported.addTo(map);
    const row = document.createElement("div");
    row.className = "layer";
    const label = document.createElement("label"),
      checkbox = document.createElement("input"),
      name = document.createElement("span");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.dataset.layer = id;
    checkbox.setAttribute("aria-label", `Show ${file.name}`);
    name.className = "layer-name";
    name.textContent = file.name;
    label.append(checkbox, name);
    row.append(label);
    list.append(row);
    document.getElementById("data-count").textContent = String(
      list.children.length,
    );
    refreshObjectSearch();
    document.dispatchEvent(new CustomEvent("open-map"));
    map.flyToBounds(imported.getBounds(), {
      padding: [50, 50],
      maxZoom: 13,
      duration: reduceMotion ? 0 : 0.6,
    });
    updateLayerStatus();
    showChat();
    addExchange(
      `Added ${file.name}`,
      `Added ${features.length} geographic feature${features.length === 1 ? "" : "s"} as a new layer. Your file was read locally. Click a feature to inspect its attributes; use the layer checkbox to compare it with the sample data. Scientific analysis of uploaded data is not connected in this preview.`,
    );
  } catch (error) {
    showChat();
    addExchange(null, `Could not add this file: ${error.message}`);
  } finally {
    fileInput.value = "";
  }
});
document.getElementById("export").addEventListener("click", () => {
  const data = {
    type: "FeatureCollection",
    features: animals.flatMap((animal) =>
      animal.points
        .filter((p) => p.month <= Number(monthInput.value))
        .map((p) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [p.lon, p.lat] },
          properties: {
            animal_id: animal.id,
            date: `2025-${String(p.month + 1).padStart(2, "0")}-15`,
            sample: true,
            rainfall_mm: p.rain,
            previous_segment_km: p.km,
          },
        })),
    ),
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/geo+json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "habitat-watch-sample.geojson";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast("Exported sample locations through the selected month");
});

initializeObjects({ runQuestion, showChat });
