const pptxgen = require("pptxgenjs");
const { applyTheme } = require("./apply_theme.cjs");

const OUT = process.argv[2] || "Dora.pptx";

const INK = "202723";
const FOREST = "294D3C";
const GRAY = "68736D";
const LIGHT = "E8ECE9";
const OCHRE = "B68638";
const WHITE = "FFFFFF";
const MIDGRAY = "B9C1BC";

const HEAD = "Cambria";
const BODY = "Arial";
const NB = " ";

const THEME = {
  name: "Dora Field",
  headFontFace: HEAD,
  bodyFontFace: BODY,
  colors: {
    dk1: INK, lt1: WHITE, dk2: FOREST, lt2: LIGHT,
    accent1: FOREST, accent2: OCHRE, accent3: GRAY, accent4: "7D9A88", accent5: "A9BDB0", accent6: "D9C49A",
    hlink: FOREST, folHlink: GRAY,
  },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.title = "Dora";
pres.author = "Dora";
pres.theme = { headFontFace: HEAD, bodyFontFace: BODY };

const W = 13.333;
const M = 0.6;
const CW = W - 2 * M;

const slideNumber = { x: W - M - 0.6, y: 7.0, w: 0.6, h: 0.3, fontFace: BODY, fontSize: 11, color: GRAY, align: "right" };

pres.defineSlideMaster({
  title: "Content",
  background: { color: WHITE },
  slideNumber,
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: M, y: 0.45, w: CW, h: 0.85, fontFace: BODY, fontSize: 36, bold: true, color: INK, align: "left", valign: "top", margin: 0 }, text: "" } },
  ],
});

pres.defineSlideMaster({
  title: "Open",
  background: { color: WHITE },
  objects: [],
});

pres.defineSlideMaster({
  title: "Appendix",
  background: { color: WHITE },
  slideNumber,
  objects: [
    { text: { text: "APPENDIX", options: { x: M, y: 0.3, w: 3, h: 0.3, fontFace: BODY, fontSize: 12, bold: true, color: GRAY, charSpacing: 2, margin: 0 } } },
    { placeholder: { options: { name: "title", type: "title", x: M, y: 0.62, w: CW, h: 0.6, fontFace: BODY, fontSize: 28, bold: true, color: INK, align: "left", valign: "top", margin: 0 }, text: "" } },
  ],
});

function text(slide, value, options) {
  slide.addText(value, { isTextBox: true, margin: 0, fontFace: BODY, color: INK, valign: "top", ...options });
}

function line(slide, x1, y1, x2, y2, options = {}) {
  const flipV = y2 < y1;
  const flipH = x2 < x1;
  slide.addShape(pres.shapes.LINE, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1), flipV, flipH,
    line: { color: options.color || GRAY, width: options.width || 1.25, dashType: options.dash || "solid",
            endArrowType: options.arrow ? "triangle" : undefined },
  });
}

function rect(slide, x, y, w, h, options = {}) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: options.fill ? { color: options.fill, transparency: options.transparency || 0 } : { type: "none" },
    line: options.line ? { color: options.line, width: options.lineWidth || 1, dashType: options.dash || "solid" } : { type: "none" },
  });
}

function dot(slide, cx, cy, d, color, outline) {
  slide.addShape(pres.shapes.OVAL, {
    x: cx - d / 2, y: cy - d / 2, w: d, h: d,
    fill: outline ? { color: WHITE } : { color },
    line: { color, width: 1.5 },
  });
}

const STUDY = { west: -110.75, south: 41.55, east: -108.70, north: 43.80 };
const WYOMING = { west: -111.05, south: 41.0, east: -104.05, north: 45.0 };
const PINEDALE = { lon: -109.86, lat: 42.87 };
const COS_LAT = Math.cos((43 * Math.PI) / 180);

function projection(frame, extent) {
  const inchesPerDegree = frame.w / ((extent.east - extent.west) * COS_LAT);
  const height = (extent.north - extent.south) * inchesPerDegree;
  return {
    height,
    x: (lon) => frame.x + (lon - extent.west) * COS_LAT * inchesPerDegree,
    y: (lat) => frame.y + (extent.north - lat) * inchesPerDegree,
  };
}

function drawStudyBox(slide, p, emphasis = true) {
  const x = p.x(STUDY.west);
  const y = p.y(STUDY.north);
  rect(slide, x, y, p.x(STUDY.east) - x, p.y(STUDY.south) - y,
       { fill: FOREST, transparency: emphasis ? 82 : 75, line: FOREST, lineWidth: 1.75 });
}

function drawWyomingMap(slide, frame, { labels = true, graticule = true } = {}) {
  const p = projection(frame, WYOMING);

  if (graticule) {
    for (let lat = 42; lat <= 44; lat++) line(slide, p.x(WYOMING.west), p.y(lat), p.x(WYOMING.east), p.y(lat), { color: LIGHT, width: 0.75 });
    for (let lon = -110; lon <= -105; lon++) line(slide, p.x(lon), p.y(WYOMING.north), p.x(lon), p.y(WYOMING.south), { color: LIGHT, width: 0.75 });
  }
  rect(slide, p.x(WYOMING.west), p.y(WYOMING.north), p.x(WYOMING.east) - p.x(WYOMING.west), p.height, { line: INK, lineWidth: 1.25 });
  drawStudyBox(slide, p);
  dot(slide, p.x(PINEDALE.lon), p.y(PINEDALE.lat), 0.13, OCHRE);

  if (labels) {
    for (const lat of [42, 43, 44]) text(slide, `${lat}°N`, { x: p.x(WYOMING.west) - 0.62, y: p.y(lat) - 0.1, w: 0.5, h: 0.22, fontSize: 12, color: GRAY, align: "right" });
    for (const lon of [-110, -108, -106]) text(slide, `${-lon}°W`, { x: p.x(lon) - 0.4, y: p.y(WYOMING.south) + 0.1, w: 0.8, h: 0.22, fontSize: 12, color: GRAY, align: "center" });
    text(slide, "WYOMING", { x: p.x(-107.3), y: p.y(44.55), w: 2.2, h: 0.3, fontSize: 14, bold: true, color: GRAY, charSpacing: 4 });
    text(slide, "Pinedale", { x: p.x(PINEDALE.lon) - 0.6, y: p.y(PINEDALE.lat) + 0.1, w: 1.2, h: 0.28, fontSize: 14, color: INK, align: "center" });
    text(slide, "Study region", { x: p.x(STUDY.east) + 0.12, y: p.y(STUDY.south) - 0.32, w: 1.6, h: 0.28, fontSize: 14, bold: true, color: FOREST });
  }
  return p;
}

// Slide 1
{
  pres.addSection({ title: "Main story" });
  const slide = pres.addSlide({ masterName: "Open", sectionTitle: "Main story" });

  text(slide, `Sublette, Wyoming${NB}·${NB}Spring 2019`, { x: M, y: 1.05, w: 5.8, h: 0.35, fontSize: 16, bold: true, color: FOREST, charSpacing: 1 });
  text(slide, "How does movement change as a landscape greens?", { x: M, y: 1.55, w: 6.2, h: 3.0, fontFace: HEAD, fontSize: 46, color: INK, lineSpacingMultiple: 0.95 });
  text(slide, "Dora", { x: M, y: 4.6, w: 5.8, h: 0.45, fontSize: 26, bold: true, color: FOREST });
  text(slide, "A connected workflow for ecological research", { x: M, y: 5.1, w: 6.2, h: 0.8, fontSize: 20, color: GRAY });

  drawWyomingMap(slide, { x: 7.35, y: 1.25, w: 5.35 });
  text(slide, "Reference map, drawn to scale (WGS84). Wyoming boundary and the study bounding box of the example query. No animal locations shown.",
       { x: 7.35, y: 5.95, w: 5.35, h: 0.5, fontSize: 12, color: GRAY });

  slide.addNotes(
    "Open with the question, not the product. In spring, snow melts and vegetation greens from low to high elevation. Many mule deer in western Wyoming move between winter and summer ranges at the same time. " +
    "A researcher who wants to compare that movement with greenness needs at least two kinds of evidence: GPS tracking records for individual animals, and satellite measurements of vegetation. " +
    "These come from different providers, in different formats, at different scales and on different schedules. " +
    "Dora is the workspace that connects those forms of evidence into one research workflow. " +
    "The map shows Wyoming and the bounding box that our example query uses around Sublette County. Pinedale marks the county seat. It is a reference map only. It shows no animal locations.\n\n" +
    "Timing: about 45 seconds."
  );
}

// Slide 2
{
  const slide = pres.addSlide({ masterName: "Content", sectionTitle: "Main story" });
  slide.addText("One question spans several datasets", { placeholder: "title", align: "left" });

  const rows = [
    { name: "GPS tracking records", source: "Movebank · CSV fixes per animal", issue: "Different identifiers", case: true },
    { name: "Satellite vegetation", source: `MODIS · 250${NB}m, 16-day composites`, issue: "Different observation intervals", case: true },
    { name: "Rainfall measurements", source: `CHIRPS · 0.05° daily grid`, issue: "Different spatial resolutions", case: false },
    { name: "Field records", source: "Surveys and management notes", issue: "Different formats and quality fields", case: false },
  ];
  const top = 2.15;
  const rowH = 1.08;
  const meetX = 10.15;
  const meetY = top + 2 * rowH - 0.25;

  text(slide, "EVIDENCE", { x: M, y: 1.6, w: 3, h: 0.3, fontSize: 14, bold: true, color: GRAY, charSpacing: 2 });
  text(slide, "PREPARATION ISSUE", { x: 5.25, y: 1.6, w: 3.5, h: 0.3, fontSize: 14, bold: true, color: GRAY, charSpacing: 2 });

  rows.forEach((row, index) => {
    const y = top + index * rowH;
    const mid = y + 0.3;
    const color = row.case ? INK : GRAY;

    text(slide, row.name, { x: M, y, w: 4.4, h: 0.42, fontSize: 22, bold: true, color });
    text(slide, row.source, { x: M, y: y + 0.45, w: 4.4, h: 0.32, fontSize: 16, color: GRAY });
    text(slide, row.issue, { x: 5.25, y: y + 0.08, w: 3.7, h: 0.6, fontSize: 20, color });
    line(slide, 9.05, mid, meetX, meetY, { color: row.case ? FOREST : MIDGRAY, width: row.case ? 1.75 : 1.25, dash: row.case ? "solid" : "dash" });
  });

  dot(slide, meetX, meetY, 0.16, FOREST);
  text(slide, "QUESTION", { x: 10.45, y: meetY - 0.62, w: 2.3, h: 0.3, fontSize: 14, bold: true, color: GRAY, charSpacing: 2 });
  text(slide, "Mule-deer movement and vegetation greenness", { x: 10.45, y: meetY - 0.3, w: 2.3, h: 1.3, fontSize: 20, bold: true, color: FOREST });

  line(slide, M, top + 2 * rowH - 0.12, 9.05, top + 2 * rowH - 0.12, { color: LIGHT, width: 1 });
  text(slide, "Case study: solid lines. Platform context, not part of the case study: dashed lines.",
       { x: M, y: 6.6, w: 9, h: 0.3, fontSize: 13, color: GRAY });

  slide.addNotes(
    "Even this one question needs several datasets, and each arrives with its own preparation problem. " +
    "Tracking records identify animals by study-specific tags, so identifiers must be scoped to the study before they can be trusted. " +
    "MODIS vegetation values describe 16-day composite windows at 250 metres, while movement is something we summarise per day. " +
    "Rainfall from CHIRPS arrives as a daily grid at 0.05 degrees, a different spatial scale again. " +
    "Field records add their own formats and quality conventions. " +
    "The two solid lines are the sources in the mule-deer case. Rainfall and field records are dashed because they are broader platform context. They are not part of this case study. " +
    "Before any comparison is meaningful, someone has to resolve all of these differences. Today that work is usually rebuilt for each new question. We make no claim here about hours or money saved.\n\n" +
    "Timing: about 1 minute."
  );
}

// Slide 3
{
  const slide = pres.addSlide({ masterName: "Content", sectionTitle: "Main story" });
  slide.addText("Dora connects the research workflow", { placeholder: "title", align: "left" });

  const stages = [
    { name: "Question", output: "Species, region and period" },
    { name: "Fetch", output: "Raw files and source metadata" },
    { name: "Normalize", output: "Consistent observations" },
    { name: "Recipe", output: "Prepared dataset and saved recipe" },
    { name: "Analysis", output: "Findings, map, timeline and evidence" },
  ];
  const step = CW / stages.length;
  const trackY = 2.3;

  line(slide, M + 0.09, trackY, M + 4 * step + 0.09, trackY, { color: FOREST, width: 1.75 });
  stages.forEach((stage, index) => {
    const x = M + index * step;

    dot(slide, x + 0.09, trackY, 0.2, FOREST, index === 0);
    text(slide, stage.name, { x, y: trackY + 0.35, w: step - 0.25, h: 0.5, fontSize: 26, bold: true, color: index === 0 ? INK : FOREST });
    text(slide, stage.output, { x, y: trackY + 1.0, w: step - 0.35, h: 1.2, fontSize: 18, color: GRAY });
  });

  text(slide, "Prepare comparable evidence in one workflow.", { x: M, y: 5.05, w: CW, h: 0.55, fontSize: 28, bold: true, color: INK });
  text(slide, "Agents propose sources and preparation plans. Validated application code performs every calculation.",
       { x: M, y: 5.75, w: 9.5, h: 0.75, fontSize: 18, color: GRAY });

  slide.addNotes(
    "Dora connects these steps into one workflow that starts from the research question: a species, a region and a period. " +
    "Fetch finds and retrieves permitted records. It keeps the original files and the source metadata, so we can always go back to what the provider published. " +
    "Normalize converts each supported source into consistent observation tables. It keeps time semantics, units, identifiers, quality fields and the reference to the source record. " +
    "Recipe is a saved preparation plan. It selects suitable datasets and states every spatial match, temporal match, aggregation and measurement window. It is saved with the versions of its inputs. " +
    "Analysis calculates historical summaries and returns findings, maps, timelines and evidence references. " +
    "One important design rule: agents can propose sources and preparation plans, but validated application code performs the calculations. A language model never invents a number. " +
    "If a technical analogy helps this audience: it is a little like Databricks for ecology, but the important part is the ecological workflow, not the analogy.\n\n" +
    "Timing: about 1 minute."
  );
}

// Slide 4
{
  const slide = pres.addSlide({ masterName: "Content", sectionTitle: "Main story" });
  slide.addText("Match movement and vegetation by place and time", { placeholder: "title", align: "left" });

  const colW = 5.6;
  const rightX = W - M - colW;

  text(slide, "INPUT", { x: M, y: 1.6, w: 2, h: 0.28, fontSize: 13, bold: true, color: GRAY, charSpacing: 2 });
  text(slide, "Movebank tracking records", { x: M, y: 1.9, w: colW, h: 0.45, fontSize: 24, bold: true, color: FOREST });
  text(slide, "GPS fixes at irregular times, with study-scoped animal IDs", { x: M, y: 2.4, w: colW, h: 0.65, fontSize: 18, color: GRAY });

  text(slide, "INPUT", { x: rightX, y: 1.6, w: 2, h: 0.28, fontSize: 13, bold: true, color: GRAY, charSpacing: 2 });
  text(slide, "MODIS vegetation observations", { x: rightX, y: 1.9, w: colW, h: 0.45, fontSize: 24, bold: true, color: FOREST });
  text(slide, `NDVI, a satellite index of vegetation greenness. 250${NB}m pixels, 16-day composites.`, { x: rightX, y: 2.4, w: colW, h: 0.65, fontSize: 18, color: GRAY });

  line(slide, M + 0.1, 3.15, M + 0.1, 4.35, { color: FOREST, width: 1.5, arrow: true });
  line(slide, 11.3, 3.15, 11.3, 4.35, { color: FOREST, width: 1.5, arrow: true });

  text(slide, "Prepared output: one row per tracked animal and day", { x: M + 0.35, y: 3.8, w: 9.6, h: 0.4, fontSize: 20, bold: true, color: INK });

  const columns = [
    { name: "Animal ID", hint: "study-scoped", w: 2.0 },
    { name: "Day", hint: "UTC", w: 1.6 },
    { name: "Location", hint: `1${NB}km grid cell`, w: 2.6 },
    { name: "Daily displacement", hint: "km from previous day", w: 3.1 },
    { name: "Matched NDVI", hint: "composite value", w: 2.8 },
  ];
  const header = columns.map((column) => ({ text: column.name, options: { bold: true, color: WHITE, fill: { color: FOREST }, fontSize: 18 } }));
  const hints = columns.map((column) => ({ text: column.hint, options: { italic: true, color: GRAY, fill: { color: LIGHT }, fontSize: 15 } }));
  slide.addTable([header, hints], {
    x: M, y: 4.5, w: CW, colW: columns.map((column) => column.w), rowH: [0.5, 0.45],
    fontFace: BODY, valign: "middle", margin: [0, 0.12, 0, 0.12], border: { type: "solid", pt: 1, color: WHITE },
  });

  const locationCenter = M + 2.0 + 1.6 + 0.12;
  const ndviCenter = M + 2.0 + 1.6 + 2.6 + 3.1 + 0.12;
  line(slide, locationCenter, 5.55, locationCenter, 5.85, { color: OCHRE, width: 1.5 });
  text(slide, "Common location reference", { x: locationCenter + 0.12, y: 5.65, w: 3.4, h: 0.35, fontSize: 18, bold: true, color: OCHRE });
  text(slide, "Fixes and pixels map to the same grid cell", { x: locationCenter + 0.12, y: 6.0, w: 4.4, h: 0.35, fontSize: 15, color: GRAY });
  line(slide, ndviCenter, 5.55, ndviCenter, 5.85, { color: OCHRE, width: 1.5 });
  text(slide, "Explicit matching by observation time", { x: ndviCenter + 0.12, y: 5.65, w: 2.75, h: 0.6, fontSize: 18, bold: true, color: OCHRE });
  text(slide, `Latest composite, ≤${NB}32${NB}days`, { x: ndviCenter + 0.12, y: 6.3, w: 2.8, h: 0.35, fontSize: 15, color: GRAY });

  text(slide, "Preparation logic. Column structure only; no rows shown.", { x: M, y: 6.75, w: 6, h: 0.28, fontSize: 12, color: GRAY });

  slide.addNotes(
    "Here is the preparation logic for the mule-deer case. " +
    "On the left: a published Movebank tracking package of GPS fixes. Fixes arrive at irregular times, and animal identifiers only make sense within their study. " +
    "On the right: MODIS vegetation observations. NDVI, the normalised difference vegetation index, is a satellite indicator of vegetation greenness. MODIS reports it at 250 metres for 16-day composite windows. " +
    "The prepared output has one row per tracked animal and UTC day. For each animal-day we keep the last good GPS fix as the representative location. " +
    "Daily displacement is the distance between consecutive daily representative locations. It is not the complete path the animal travelled. " +
    "Both sources are matched to a common 1 kilometre grid cell. A common grid does not increase the resolution of the original measurements. " +
    "Each row then takes the latest MODIS composite in that cell that started within 32 days before the day. The match rule is explicit and saved in the recipe. " +
    "Intervals and tracking gaps matter: a missing day breaks the displacement calculation, and a composite value describes a window, not a single day. " +
    "This slide shows preparation logic. The column structure matches the example code; no rows are shown because a complete run is not yet verified.\n\n" +
    "Timing: about 1 minute 10 seconds."
  );
}

// Slide 5
{
  const slide = pres.addSlide({ masterName: "Content", sectionTitle: "Main story" });
  slide.addText("Inspect the pattern and the supporting records", { placeholder: "title", align: "left" });
  text(slide, "Conceptual result layout — no measured findings shown", { x: M, y: 1.33, w: 8, h: 0.35, fontSize: 16, bold: true, color: OCHRE });

  const mapFrame = { x: M, y: 1.9, w: 6.2, h: 4.95 };
  rect(slide, mapFrame.x, mapFrame.y, mapFrame.w, mapFrame.h, { line: MIDGRAY, lineWidth: 1 });
  text(slide, "MAP · location context", { x: mapFrame.x + 0.2, y: mapFrame.y + 0.15, w: 4, h: 0.3, fontSize: 14, bold: true, color: GRAY });

  const extent = { west: -111.9, east: -107.55, north: 44.05, south: 41.35 };
  const mapH = mapFrame.h - 1.1;
  const mapW = mapH * ((extent.east - extent.west) * COS_LAT) / (extent.north - extent.south);
  const p = projection({ x: mapFrame.x + (mapFrame.w - mapW) / 2, y: mapFrame.y + 0.55, w: mapW }, extent);
  for (const lat of [42, 43, 44]) line(slide, p.x(extent.west), p.y(lat), p.x(extent.east), p.y(lat), { color: LIGHT, width: 0.75 });
  for (const lon of [-111, -110, -109, -108]) line(slide, p.x(lon), p.y(extent.north), p.x(lon), p.y(extent.south), { color: LIGHT, width: 0.75 });
  line(slide, p.x(WYOMING.west), p.y(extent.north), p.x(WYOMING.west), p.y(extent.south), { color: INK, width: 1.25 });
  text(slide, "ID", { x: p.x(WYOMING.west) - 0.5, y: p.y(43.95), w: 0.35, h: 0.25, fontSize: 12, bold: true, color: GRAY, align: "right" });
  text(slide, "WY", { x: p.x(WYOMING.west) + 0.12, y: p.y(43.95), w: 0.4, h: 0.25, fontSize: 12, bold: true, color: GRAY });
  drawStudyBox(slide, p);
  dot(slide, p.x(PINEDALE.lon), p.y(PINEDALE.lat), 0.13, OCHRE);
  text(slide, "Pinedale", { x: p.x(PINEDALE.lon) - 0.6, y: p.y(PINEDALE.lat) + 0.1, w: 1.2, h: 0.28, fontSize: 14, color: INK, align: "center" });
  for (const lat of [42, 43]) text(slide, `${lat}°N`, { x: p.x(extent.east) - 0.55, y: p.y(lat) - 0.27, w: 0.5, h: 0.22, fontSize: 12, color: GRAY, align: "right" });
  for (const lon of [-110, -108]) text(slide, `${-lon}°W`, { x: p.x(lon) + 0.06, y: p.y(extent.south) - 0.27, w: 0.7, h: 0.22, fontSize: 12, color: GRAY });
  text(slide, "Daily animal locations appear here after a verified run.", { x: mapFrame.x + 0.2, y: mapFrame.y + mapFrame.h - 0.45, w: 5.4, h: 0.3, fontSize: 14, italic: true, color: GRAY });

  const panelX = 7.1;
  const panelW = W - M - panelX;
  const timeline = { x: panelX, y: 1.9, w: panelW, h: 2.65 };
  rect(slide, timeline.x, timeline.y, timeline.w, timeline.h, { line: MIDGRAY, lineWidth: 1 });
  text(slide, `TIMELINE · March–June${NB}2019`, { x: timeline.x + 0.2, y: timeline.y + 0.15, w: 4.5, h: 0.3, fontSize: 14, bold: true, color: GRAY });

  const axisX = timeline.x + 0.3;
  const axisW = timeline.w - 0.6;
  const start = Date.UTC(2019, 2, 1);
  const end = Date.UTC(2019, 6, 1);
  const timeX = (date) => axisX + ((date - start) / (end - start)) * axisW;

  for (const [index, lane] of ["Daily displacement", "Matched NDVI"].entries()) {
    const y = timeline.y + 0.6 + index * 0.48;
    rect(slide, axisX, y, axisW, 0.38, { fill: LIGHT });
    text(slide, lane, { x: axisX + 0.12, y: y + 0.08, w: 3, h: 0.25, fontSize: 13, color: GRAY });
  }

  const axisY = timeline.y + 2.05;
  line(slide, axisX, axisY, axisX + axisW, axisY, { color: INK, width: 1 });
  const compositeStarts = [[2, 6], [2, 22], [3, 7], [3, 23], [4, 9], [4, 25], [5, 10], [5, 26]];
  for (const [month, day] of compositeStarts) {
    const x = timeX(Date.UTC(2019, month, day));
    line(slide, x, axisY - 0.2, x, axisY, { color: FOREST, width: 1.5 });
  }
  text(slide, "MODIS composite starts", { x: axisX, y: axisY - 0.47, w: 3, h: 0.24, fontSize: 12, color: FOREST });
  for (const [month, label] of [[2, "Mar"], [3, "Apr"], [4, "May"], [5, "Jun"]]) {
    const x = timeX(Date.UTC(2019, month, 1));
    line(slide, x, axisY, x, axisY + 0.08, { color: INK, width: 1 });
    text(slide, label, { x: x + 0.04, y: axisY + 0.1, w: 0.6, h: 0.25, fontSize: 13, color: INK });
  }

  const evidence = { x: panelX, y: 4.8, w: panelW, h: 2.05 };
  rect(slide, evidence.x, evidence.y, evidence.w, evidence.h, { line: MIDGRAY, lineWidth: 1 });
  text(slide, "EVIDENCE · kept with the result", { x: evidence.x + 0.2, y: evidence.y + 0.15, w: 4.5, h: 0.3, fontSize: 14, bold: true, color: GRAY });
  const references = [
    ["Tracking package", "Movebank 10255/move.3619"],
    ["Vegetation product", "MODIS MOD13Q1 v061"],
    ["Common grid", `EASE-Grid 2.0, 1${NB}km`],
    ["Saved recipe", "daily-movement-and-ndvi v1"],
  ];
  slide.addText(
    references.flatMap(([label, value], index) => [
      { text: `${label}  `, options: { bold: true, color: INK } },
      { text: value, options: { color: GRAY, breakLine: index < references.length - 1 } },
    ]),
    { isTextBox: true, x: evidence.x + 0.2, y: evidence.y + 0.55, w: evidence.w - 0.4, h: 1.4, fontFace: BODY, fontSize: 15, margin: 0, valign: "top", paraSpaceAfter: 4 }
  );

  slide.addNotes(
    "This is the experience we are building toward, and I want to be precise about its status. It is a conceptual result layout. It shows no measured findings. " +
    "The repository contains a real-source run script for this case. The recorded attempts stopped at the Recipe stage, so there are no verified results to show yet. " +
    "A researcher inspects three connected regions. " +
    "The map gives location context: where the tracked animals were, inside the study region, on each day. The study box and Pinedale are real geography; no tracks are drawn. " +
    "The timeline places observations across March to June 2019. The tick marks are the real start dates of the 16-day MODIS composites in that period, which shows how coarse the vegetation record is next to daily movement. " +
    "The evidence panel keeps the source versions and preparation references with every result: the Movebank package, the MODIS product version, the common grid and the saved recipe. " +
    "When results exist, we will be careful with language. An observation is what the records show. An association is a pattern between two measured series. Neither establishes that greening caused movement. " +
    "Any finding will state the number of tracked animals and the period it covers.\n\n" +
    "Timing: about 1 minute 15 seconds. This is the centrepiece; slow down here."
  );
}

// Slide 6
{
  const slide = pres.addSlide({ masterName: "Content", sectionTitle: "Main story" });
  slide.addText("Reuse the preparation, preserve the evidence", { placeholder: "title", align: "left" });

  const labelW = 2.0;
  const chainX = M + labelW + 0.2;
  const nodeW = 2.15;
  const gap = (W - M - chainX - 4 * nodeW) / 3;
  const nodeX = (index) => chainX + index * (nodeW + gap);
  const rowA = 2.05;
  const rowB = 4.25;
  const nodeH = 1.15;

  function node(x, y, label, tag, options = {}) {
    rect(slide, x, y, nodeW, nodeH, { fill: options.fill || WHITE, line: options.line || MIDGRAY, lineWidth: 1.25 });
    text(slide, label, { x: x + 0.15, y: y + 0.12, w: nodeW - 0.3, h: 0.6, fontSize: 16, bold: true, color: options.color || INK });
    if (tag) text(slide, tag, { x: x + 0.15, y: y + nodeH - 0.33, w: nodeW - 0.3, h: 0.25, fontSize: 13, color: options.tagColor || GRAY });
  }

  text(slide, "First analysis", { x: M, y: rowA + 0.15, w: labelW, h: 0.4, fontSize: 20, bold: true, color: INK });
  text(slide, "Spring 2019", { x: M, y: rowA + 0.52, w: labelW, h: 0.3, fontSize: 15, color: GRAY });
  ["Source versions", "Saved recipe", "Prepared dataset", "Result"].forEach((label, index) => {
    node(nodeX(index), rowA, label, ["pinned inputs", "recipe v1", "Parquet artifact", "result v1 · kept"][index],
         index === 3 ? { fill: LIGHT, line: FOREST, color: FOREST, tagColor: OCHRE } : {});
    if (index < 3) line(slide, nodeX(index) + nodeW + 0.05, rowA + nodeH / 2, nodeX(index + 1) - 0.05, rowA + nodeH / 2, { color: FOREST, width: 1.5, arrow: true });
  });

  text(slide, "Later analysis", { x: M, y: rowB + 0.15, w: labelW, h: 0.4, fontSize: 20, bold: true, color: INK });
  text(slide, "New observations", { x: M, y: rowB + 0.52, w: labelW, h: 0.3, fontSize: 15, color: GRAY });
  node(nodeX(0), rowB, "Updated observations", "new source versions");
  node(nodeX(1), rowB, "Reused or revised recipe", "v1 or v2");
  node(nodeX(3), rowB, "New result", "result v2", { fill: LIGHT, line: FOREST, color: FOREST });
  line(slide, nodeX(0) + nodeW + 0.05, rowB + nodeH / 2, nodeX(1) - 0.05, rowB + nodeH / 2, { color: FOREST, width: 1.5, arrow: true });
  line(slide, nodeX(1) + nodeW + 0.05, rowB + nodeH / 2, nodeX(3) - 0.05, rowB + nodeH / 2, { color: FOREST, width: 1.5, arrow: true });
  text(slide, "new prepared dataset", { x: nodeX(2) + 0.1, y: rowB + nodeH / 2 - 0.37, w: nodeW - 0.2, h: 0.28, fontSize: 13, color: GRAY, align: "center" });

  line(slide, nodeX(1) + nodeW / 2, rowA + nodeH + 0.05, nodeX(1) + nodeW / 2, rowB - 0.05, { color: GRAY, width: 1.25, dash: "dash", arrow: true });
  text(slide, "reuse when compatible", { x: nodeX(1) + nodeW / 2 + 0.12, y: rowA + nodeH + 0.4, w: 2.4, h: 0.3, fontSize: 13, color: GRAY });
  line(slide, nodeX(3) + nodeW / 2, rowA + nodeH + 0.05, nodeX(3) + nodeW / 2, rowB - 0.05, { color: MIDGRAY, width: 1.25, dash: "sysDot" });
  text(slide, "both stay identifiable", { x: nodeX(3) + nodeW / 2 - 2.32, y: rowA + nodeH + 0.4, w: 2.2, h: 0.3, fontSize: 13, color: GRAY, align: "right" });

  text(slide, "New observations extend the study without erasing its history.", { x: M, y: 5.95, w: CW, h: 0.5, fontSize: 26, bold: true, color: FOREST });

  slide.addNotes(
    "Reproducibility is the second value. " +
    "In the first analysis, the recipe pins the exact source versions it uses, records every preparation step, and produces a prepared dataset stored as a Parquet artifact with a checksum. The result references all of them. " +
    "Later, new observations arrive, for example another spring of tracking records or newer satellite composites. If the request is compatible, Dora reuses the saved recipe and any prepared artifacts that are still valid. " +
    "When inputs change, the cache is invalidated and the affected steps run again. If the preparation itself must change, that creates a new recipe version. The first result and its inputs remain identifiable. " +
    "We describe provenance at three levels: the dataset version, the recipe, and the artifact. We do not claim lineage back to every individual source record inside an aggregated value. " +
    "Repeated seasonal analysis is a platform use case. We do not yet claim automatic scheduling or alerts.\n\n" +
    "Timing: about 1 minute."
  );
}

// Slide 7
{
  const slide = pres.addSlide({ masterName: "Content", sectionTitle: "Main story" });
  slide.addText("The workflow extends beyond animal tracking", { placeholder: "title", align: "left" });

  const colW = (CW - 2 * 0.5) / 3;
  const colX = (index) => M + index * (colW + 0.5);
  const glyphY = 2.35;
  const cell = 0.36;

  const scenarios = [
    { status: "CURRENT FOCUS", statusColor: FOREST, title: "Wildlife movement and environmental conditions", detail: "Tracking records with vegetation and rainfall" },
    { status: "SOURCES SUPPORTED", statusColor: INK, title: "Habitat change across seasons or years", detail: "MODIS, Sentinel-2 and CHIRPS observations" },
    { status: "EXPANSION PATH", statusColor: GRAY, title: "Restoration monitoring", detail: "Field plots, surveys and management records" },
  ];
  scenarios.forEach((scenario, index) => {
    const x = colX(index);

    text(slide, scenario.status, { x, y: 1.65, w: colW, h: 0.3, fontSize: 14, bold: true, color: scenario.statusColor, charSpacing: 2 });
    text(slide, scenario.title, { x, y: 4.2, w: colW - 0.1, h: 1.2, fontSize: 22, bold: true, color: INK });
    text(slide, scenario.detail, { x, y: 5.55, w: colW - 0.2, h: 0.65, fontSize: 17, color: GRAY });
    if (index > 0) line(slide, x - 0.25, 1.7, x - 0.25, 6.25, { color: LIGHT, width: 1 });
  });

  {
    const x0 = colX(0);
    for (let row = 0; row < 4; row++) for (let col = 0; col < 5; col++) {
      rect(slide, x0 + col * cell, glyphY + row * cell, cell, cell, { fill: [LIGHT, "D3DDD6", "BCCDC2"][(row + col) % 3], line: WHITE, lineWidth: 1 });
    }
    for (const [col, row] of [[1, 0], [2, 1], [2, 2], [3, 3]]) dot(slide, x0 + col * cell + cell / 2, glyphY + row * cell + cell / 2, 0.11, FOREST);
  }

  {
    const x0 = colX(1);
    const small = 0.24;
    const shades = [[LIGHT, "D3DDD6", LIGHT, "BCCDC2"], ["D3DDD6", "BCCDC2", "D3DDD6", "9FB6A8"], ["BCCDC2", "9FB6A8", "BCCDC2", "7D9A88"]];
    shades.forEach((palette, panel) => {
      const px = x0 + panel * (4 * small + 0.25);
      for (let row = 0; row < 4; row++) for (let col = 0; col < 4; col++) {
        rect(slide, px + col * small, glyphY + 0.2 + row * small, small, small, { fill: palette[(row * 3 + col) % 4], line: WHITE, lineWidth: 0.75 });
      }
      text(slide, `Period ${panel + 1}`, { x: px, y: glyphY + 0.3 + 4 * small, w: 1.0, h: 0.25, fontSize: 12, color: GRAY });
    });
  }

  {
    const x0 = colX(2);
    slide.addShape(pres.shapes.CUSTOM_GEOMETRY, {
      x: x0, y: glyphY, w: 2.6, h: 1.45,
      fill: { color: WHITE }, line: { color: GRAY, width: 1.5, dashType: "dash" },
      points: [{ x: 0.1, y: 0.25 }, { x: 1.4, y: 0 }, { x: 2.5, y: 0.35 }, { x: 2.3, y: 1.3 }, { x: 0.9, y: 1.45 }, { x: 0, y: 0.9 }, { close: true }],
    });
    for (const [px, py] of [[0.6, 0.45], [1.5, 0.35], [1.9, 0.9], [0.9, 0.95]]) {
      rect(slide, x0 + px, glyphY + py, 0.16, 0.16, { fill: WHITE, line: INK, lineWidth: 1.25 });
    }
  }

  text(slide, "Schematic illustrations. No measured data.", { x: M, y: 6.55, w: 6, h: 0.28, fontSize: 12, color: GRAY });

  slide.addNotes(
    "The mule-deer case is one example of a general pattern: a question that needs several kinds of evidence matched by place and time. " +
    "The first scenario, wildlife movement and environmental conditions, is the current focus. The historical pipeline for it exists in code and integration tests. " +
    "The second, habitat change across seasons or years, uses sources we already connect: MODIS and Sentinel-2 vegetation, water and moisture indicators, and CHIRPS rainfall. The connectors exist; we have not shown a complete habitat-change analysis. " +
    "The third, restoration monitoring with field plots and management records, is the expansion path. Field collection, camera traps, sensors, population counts and water-quality sources are future source families. " +
    "The intended users are conservation groups, researchers and land managers who want answers without assembling geospatial datasets themselves. " +
    "Dora does not replace repositories like Movebank or providers like NASA and the Climate Hazards Center. Dora connects the repositories and tools that researchers already use. " +
    "The glyphs are schematic illustrations, not data.\n\n" +
    "Timing: about 50 seconds."
  );
}

// Slide 8
{
  const slide = pres.addSlide({ masterName: "Open", sectionTitle: "Main story" });

  text(slide, "Build a continuous record of environmental change", { x: M, y: 0.85, w: 7.6, h: 1.5, fontFace: HEAD, fontSize: 40, color: INK });
  text(slide, [
    { text: "One ecological question.", options: { breakLine: true } },
    { text: "Multiple data sources.", options: { breakLine: true } },
    { text: "One reproducible workflow." },
  ], { x: M, y: 2.75, w: 7.6, h: 1.65, fontFace: HEAD, fontSize: 32, color: FOREST, lineSpacingMultiple: 1.05 });

  text(slide, "NEXT STEP", { x: M, y: 4.85, w: 3, h: 0.3, fontSize: 14, bold: true, color: OCHRE, charSpacing: 2 });
  text(slide, "Validate a complete historical workflow with researchers.", { x: M, y: 5.2, w: 8.4, h: 0.45, fontSize: 22, bold: true, color: INK });
  text(slide, "Extend from public datasets to field observations and recurring monitoring.", { x: M, y: 5.8, w: 8.4, h: 0.4, fontSize: 16, color: GRAY });

  drawWyomingMap(slide, { x: 9.3, y: 2.75, w: 3.4 }, { labels: false, graticule: false });
  text(slide, "Wyoming · study region", { x: 9.3, y: 5.55, w: 3.4, h: 0.3, fontSize: 14, color: GRAY });

  slide.addNotes(
    "Dora provides three values. Dora connects relevant evidence from different sources. Dora preserves reproducible preparation and analysis. Dora supports comparisons across locations and time periods. A study can become a continuous record. " +
    "The next practical step is specific. We want to validate a complete historical workflow, starting with this mule-deer and vegetation question, together with researchers who know the system. That means a full run from fetch to analysis, checked results, and honest limitations. " +
    "After that, the direction is to extend from public datasets to field observations and recurring monitoring. " +
    "Leave this slide up for discussion. Useful questions to invite: which datasets would you want connected first, and what would you need to see before trusting a prepared dataset?\n\n" +
    "Timing: about 40 seconds, then discussion."
  );
}

// Appendix A1
{
  pres.addSection({ title: "Appendix" });
  const slide = pres.addSlide({ masterName: "Appendix", sectionTitle: "Appendix" });
  slide.addText("Data sources and study references", { placeholder: "title", align: "left" });

  const head = (value) => ({ text: value, options: { bold: true, color: WHITE, fill: { color: FOREST } } });
  const rows = [
    ["Movebank Data Repository", "Published animal tracking packages", "GPS fixes; interval set by each study", "Public packages with DOI, license and citation; no login"],
    ["Movebank direct-read API", "Study-based tracking access", "As set by each study", "Credentials permit an attempt; owner permission or license acceptance can be required"],
    ["MODIS MOD13Q1 v061 (Terra)", "NDVI, EVI, pixel reliability", `250${NB}m; 16-day composites`, "Microsoft Planetary Computer STAC"],
    ["Sentinel-2 L2A", "Vegetation (NDVI), water (MNDWI), moisture (NDMI)", `10–20${NB}m bands; revisit of several days`, "Microsoft Planetary Computer STAC; cloud filter ≤ 80%"],
    ["CHIRPS v2.0", "Daily rainfall", "0.05°; daily; 50°S–50°N", "CC-BY-4.0; cite Funk et al. 2015"],
  ];
  slide.addTable([["Source", "Provides", "Spatial / temporal", "Access and terms"].map(head), ...rows.map((row) => row.map((value) => ({ text: value })))], {
    x: M, y: 1.45, w: CW, colW: [2.9, 3.2, 2.8, 3.2], fontFace: BODY, fontSize: 13, color: INK, valign: "middle",
    border: { type: "solid", pt: 0.75, color: LIGHT }, margin: [0.04, 0.1, 0.04, 0.1], rowH: 0.48,
  });

  text(slide, "Case-study references", { x: M, y: 4.75, w: 6, h: 0.32, fontSize: 16, bold: true, color: INK });
  slide.addText([
    { text: "Tracking package: “Wyoming Sublette Mule Deer (2018-2020)”, Movebank Data Repository handle 10255/move.3619, package UUID 0e3a4577-b063-47d9-9d6b-edec949aa5fa.", options: { bullet: true, breakLine: true } },
    { text: "Query: Odocoileus hemionus; bounding box 110.75°W–108.70°W, 41.55°N–43.80°N; 1 March–30 June 2019 (UTC).", options: { bullet: true, breakLine: true } },
    { text: `Common grid: EASE-Grid 2.0 global, 1${NB}km. Matching to a common grid does not increase the resolution of the source measurements.`, options: { bullet: true, breakLine: true } },
    { text: "Provider links: datarepository.movebank.org · planetarycomputer.microsoft.com · chc.ucsb.edu/data/chirps · github.com/movebank/movebank-api-doc", options: { bullet: true } },
  ], { isTextBox: true, x: M, y: 5.12, w: CW, h: 1.7, fontFace: BODY, fontSize: 13, color: INK, margin: 0, valign: "top", paraSpaceAfter: 4 });

  slide.addNotes(
    "Source details come from SOURCES.md and the connector code in the habitat package.\n" +
    "Full links:\n" +
    "Movebank Data Repository: https://datarepository.movebank.org/\n" +
    "Movebank package handle: https://datarepository.movebank.org/handle/10255/move.3619\n" +
    "Movebank API documentation: https://github.com/movebank/movebank-api-doc/blob/master/movebank-api.md\n" +
    "MODIS MOD13Q1 v061 STAC collection: https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-13Q1-061\n" +
    "Sentinel-2 L2A STAC collection: https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a\n" +
    "CHIRPS product documentation: https://chc.ucsb.edu/data/chirps\n" +
    "CHIRPS citation: Funk, C. et al. (2015). The climate hazards infrared precipitation with stations — a new environmental record for monitoring extremes. Scientific Data 2, 150066.\n\n" +
    "Cite the Movebank package with the DOI and citation that its repository page lists. The connector keeps that metadata in the raw manifest."
  );
}

// Appendix A2
{
  const slide = pres.addSlide({ masterName: "Appendix", sectionTitle: "Appendix" });
  slide.addText("Technical architecture and current implementation scope", { placeholder: "title", align: "left" });

  const stages = [
    ["Fetch", "Connectors → raw-file archive with manifests and checksums"],
    ["Normalize", "PostgreSQL / PostGIS catalog and canonical observations"],
    ["Recipe", "Typed recipe → compiled SQL → Parquet feature artifact"],
    ["Analysis", "Historical summaries, findings, evidence references"],
  ];
  const archW = 5.4;
  text(slide, "PIPELINE · Python, durable job coordinator", { x: M, y: 1.5, w: archW, h: 0.3, fontSize: 13, bold: true, color: GRAY, charSpacing: 1 });
  stages.forEach(([name, detail], index) => {
    const y = 1.95 + index * 1.15;
    rect(slide, M, y, archW, 0.85, { fill: index === 2 ? LIGHT : WHITE, line: MIDGRAY });
    text(slide, name, { x: M + 0.15, y: y + 0.12, w: 1.5, h: 0.3, fontSize: 16, bold: true, color: FOREST });
    text(slide, detail, { x: M + 1.65, y: y + 0.12, w: archW - 1.8, h: 0.65, fontSize: 13, color: INK });
    if (index < 3) line(slide, M + 0.5, y + 0.85, M + 0.5, y + 1.15, { color: FOREST, width: 1.25, arrow: true });
  });
  text(slide, "Agents propose sources, mappings and recipes. Validation and deterministic code execute them.", { x: M, y: 6.55, w: archW, h: 0.45, fontSize: 12, color: GRAY });

  const scopeX = M + archW + 0.6;
  const scopeW = W - M - scopeX;
  const groups = [
    ["In code and integration tests", FOREST, "Historical pipeline from Fetch to Analysis. Saved recipes with pinned input versions. Reuse of compatible artifacts; changed inputs miss the cache."],
    ["Not yet established", OCHRE, "A complete successful run of examples/sublette_mule_deer_ndvi.py. Recorded attempts stopped at Recipe (invalid recipe; database execution error)."],
    ["Experimental", INK, "Forecasting methods exist in Analysis. Recipe returns insufficient_data for strict forecast preparation until a point-in-time availability contract exists."],
    ["Prototype and expansion", GRAY, "Web demo uses synthetic pronghorn records (2025) and scripted assistant commands. Local imports support map exploration only. Field, camera-trap, sensor, population and water-quality sources are future work."],
  ];
  let y = 1.5;
  for (const [label, color, body] of groups) {
    text(slide, label, { x: scopeX, y, w: scopeW, h: 0.3, fontSize: 15, bold: true, color });
    text(slide, body, { x: scopeX, y: y + 0.33, w: scopeW, h: 0.95, fontSize: 13, color: INK });
    y += 1.33;
  }

  slide.addNotes(
    "Sources in the repository: README.md, RECIPE_INTEGRATION.md, ANALYSIS_INTEGRATION.md, src/recipe/README.md, web/src/App.tsx and examples/sublette_mule_deer_ndvi.py.\n\n" +
    "Storage: original files stay in a raw archive (local files in development, object storage later). PostgreSQL with PostGIS holds the catalog and canonical observations. Recipe writes feature datasets as checksummed Parquet artifacts.\n\n" +
    "Forecast limits: src/recipe/README.md states that strict forecast preparation returns insufficient_data until Stage 2 and Analysis agree on historical availability and per-row feature-time contracts. ANALYSIS_INTEGRATION.md confirms that forecast queries stop at Recipe. The CHIRPS available_at value is the file's Last-Modified time, so it cannot serve as a historical availability time.\n\n" +
    "Case-study status: data/runs/sublette-mule-deer-ndvi holds two failure records and no feature artifact. One failure is INVALID_RECIPE after bounded planner attempts; the other is DATABASE_EXECUTION_FAILED. No case-study metrics are reported anywhere in this deck.\n\n" +
    "Provenance is recorded at dataset, recipe and artifact level. Complete source-record lineage for every aggregated output is not claimed."
  );
}

// Appendix A3
{
  const slide = pres.addSlide({ masterName: "Appendix", sectionTitle: "Appendix" });
  slide.addText("Presentation design references", { placeholder: "title", align: "left" });

  const references = [
    ["TED — 10 tips for better slide decks", "https://blog.ted.com/10-tips-for-better-slide-decks/"],
    ["Garr Reynolds — Design tips", "https://www.garrreynolds.com/design-tips"],
    ["Penn State — Assertion-evidence approach", "https://www.assertion-evidence.com/"],
    ["Duarte — Tips for crafting a storytelling presentation", "https://www.duarte.com/blog/tips-for-crafting-a-storytelling-presentation/"],
    ["Duarte — How generative AI is changing presentations", "https://www.duarte.com/blog/how-generative-ai-is-changing-presentations/"],
    ["Datawrapper — 10 ways to use fewer colors", "https://www.datawrapper.de/blog/10-ways-to-use-fewer-colors-in-your-data-visualizations"],
  ];
  slide.addText(references.flatMap(([label, url], index) => [
    { text: label, options: { bold: true, breakLine: true } },
    { text: url, options: { color: GRAY, fontSize: 13, breakLine: index < references.length - 1 } },
  ]), { isTextBox: true, x: M, y: 1.5, w: 8.5, h: 4.9, fontFace: BODY, fontSize: 15, color: INK, margin: 0, valign: "top", paraSpaceAfter: 6 });

  text(slide, "These sources informed the communication approach. The palette, typography and field-research style are project choices.", { x: M, y: 6.55, w: 9, h: 0.4, fontSize: 12, color: GRAY });

  slide.addNotes("Design references only. They do not support any claim about Dora.");
}

(async () => {
  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();
