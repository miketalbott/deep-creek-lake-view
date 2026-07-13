// Smoke test the lot-finder: execute the real inline script from index.html
// with minimal Leaflet/DOM stubs and run the ranking on the real parcel data.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const web = join(root, "output", "webmap");

function evalGlobals(file, names) {
  const src = readFileSync(join(web, file), "utf8");
  const fn = new Function(src + "\nreturn {" + names.join(",") + "};");
  Object.assign(globalThis, fn());
}
evalGlobals("config.js", ["CONFIG"]);
evalGlobals("parcels.js", ["PARCELS"]);
evalGlobals("lake.js", ["LAKE"]);
globalThis.DEMMETA = { w: 1, h: 1, x0: 0, y0: 0, res: 10 };
globalThis.DEMB64 = "";
globalThis.LAKEPTS = [];
globalThis.proj4 = () => [0, 0];

// --- Leaflet stub: enough to construct layers and evaluate style functions ---
const layerStub = () => ({
  addTo() { return this; }, on() { return this; }, bindPopup() { return this; },
  setStyle() {}, getBounds() { return {}; }, openPopup() {}, clearLayers() {},
  setOpacity() {}, resetStyle() {},
});
globalThis.L = {
  map: () => ({ setView() { return this; }, on() {}, getZoom: () => 12,
    hasLayer: () => false, removeLayer() {}, addLayer() {}, fitBounds() {} }),
  tileLayer: layerStub, imageOverlay: layerStub, layerGroup: layerStub,
  circleMarker: layerStub, marker: layerStub, divIcon: () => ({}),
  control: { layers: layerStub },
  DomEvent: { disableClickPropagation() {}, disableScrollPropagation() {} },
  geoJSON: (data, opts = {}) => {
    const feats = data && data.features ? data.features : [];
    for (const f of feats) {
      if (typeof opts.style === "function") opts.style(f);  // Leaflet evaluates style at construction
      if (opts.onEachFeature) opts.onEachFeature(f, layerStub());
    }
    const g = layerStub();
    g.setStyle = fn => { if (typeof fn === "function") feats.forEach(fn); };
    return g;
  },
};

// --- DOM stub ---
const handlers = {};
const els = {};
function el(id) {
  const classes = new Set();
  if (!els[id]) els[id] = {
    id, checked: id === "resOnly" || id === "parcelsChk" || id === "heatChk",
    value: {
      minAcres: "0", opacity: "70", scorePreset: "balanced", propertyType: "all",
    }[id] ?? "",
    innerHTML: "", style: {}, onclick: null,
    classList: {
      toggle: (name, force) => force ? classes.add(name) : classes.delete(name),
      contains: name => classes.has(name),
    },
    setAttribute() {},
    addEventListener: (t, fn) => { handlers[`${id}.${t}`] = fn; },
    querySelectorAll: () => [],
  };
  return els[id];
}
globalThis.document = {
  getElementById: el,
  querySelector: sel => sel.includes("metric") ? { value: "area" } : { value: "2m" },
  querySelectorAll: () => [],
};
globalThis.window = { matchMedia: () => ({ matches: false }) };
globalThis.performance = { now: () => 0 };
globalThis.atob = s => "";

// --- run the real inline script ---
const html = readFileSync(join(web, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
new Function(scripts.join("\n"))();

let fails = 0;
for (const preset of ["balanced", "view", "vacant", "home", "access"]) {
  el("scorePreset").value = preset;
  handlers["presetBtn.click"]();
  handlers["rankBtn.click"]();
  const scored = PARCELS.features.filter(f => f.properties._score != null);
  const html = el("rankList").innerHTML;
  const scoresValid = scored.every(
    f => f.properties._score >= 0 && f.properties._score <= 100
      && f.properties._breakdown.length > 0
  );
  const ok = scored.length > 500 && scoresValid && html.includes("rankRow");
  if (!ok) fails++;
  console.log(`preset=${preset}: ${scored.length} lots scored ${ok ? "OK" : "FAIL"}`);
}

// Asking-price weight with an empty listings file should degrade gracefully.
for (const metric of [
  "lake_area", "perceived", "lot_size", "usable", "shore", "land_value",
  "improvements", "asking", "route_i68", "route_honi", "route_wisp",
  "route_grocery", "route_hospital",
]) {
  el(`weight_${metric}`).value = metric === "asking" ? "100" : "0";
}
handlers["rankBtn.click"]();
const msgOk = el("rankList").innerHTML.includes("No lots");
console.log(`asking price (no listings): graceful message ${msgOk ? "OK" : "FAIL"}`);
if (!msgOk) fails++;

function setOnly(id) {
  for (const metric of [
    "lake_area", "perceived", "lot_size", "usable", "shore", "land_value",
    "improvements", "asking", "route_i68", "route_honi", "route_wisp",
    "route_grocery", "route_hospital",
  ]) el(`weight_${metric}`).value = metric === id ? "100" : "0";
}

// Verify desirability direction: shorter I-68 time and larger views score best.
el("resOnly").checked = false;
el("propertyType").value = "all";
setOnly("route_i68");
handlers["rankBtn.click"]();
let scored = PARCELS.features.filter(f => f.properties._score != null);
let ordered = [...scored].sort(
  (a, b) => a.properties.drive_i68_min - b.properties.drive_i68_min
);
let directionOk = ordered[0].properties._score > ordered.at(-1).properties._score;
console.log(`I-68 lower-is-better direction: ${directionOk ? "OK" : "FAIL"}`);
if (!directionOk) fails++;

setOnly("lake_area");
handlers["rankBtn.click"]();
scored = PARCELS.features.filter(f => f.properties._score != null);
ordered = [...scored].sort(
  (a, b) => a.properties.area_2m_p90_ac - b.properties.area_2m_p90_ac
);
directionOk = ordered.at(-1).properties._score > ordered[0].properties._score;
console.log(`lake-view higher-is-better direction: ${directionOk ? "OK" : "FAIL"}`);
if (!directionOk) fails++;

process.exit(fails ? 1 : 0);
