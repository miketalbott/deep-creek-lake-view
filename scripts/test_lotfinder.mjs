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
  if (!els[id]) els[id] = {
    id, checked: id === "resOnly" || id === "parcelsChk" || id === "heatChk",
    value: { blend: "50", minAcres: "0", rankBy: "view", opacity: "70" }[id] ?? "",
    innerHTML: "", style: {}, onclick: null,
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
globalThis.performance = { now: () => 0 };
globalThis.atob = s => "";

// --- run the real inline script ---
const html = readFileSync(join(web, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
new Function(scripts.join("\n"))();

function topOf(listHtml, n = 3) {
  return [...listHtml.matchAll(/<span class="sc">(\d+)<\/span>\s*<span>([^<]*)/g)]
    .slice(0, n).map(m => `${m[1]}  ${m[2].trim()}`);
}

let fails = 0;
for (const rankBy of ["view", "perdollar", "perland"]) {
  el("rankBy").value = rankBy;
  handlers["rankBtn.click"]();
  const scored = PARCELS.features.filter(f => f.properties._score != null);
  const html = el("rankList").innerHTML;
  const ok = scored.length > 500 && html.includes("rankRow");
  if (!ok) fails++;
  console.log(`rankBy=${rankBy}: ${scored.length} lots scored ${ok ? "OK" : "FAIL"}`);
  topOf(html).forEach(r => console.log("   " + r));
}

// perlist with empty listings file should degrade gracefully
el("rankBy").value = "perlist";
handlers["rankBtn.click"]();
const msgOk = el("rankList").innerHTML.includes("No lots match");
console.log(`rankBy=perlist (no listings): graceful message ${msgOk ? "OK" : "FAIL"}`);
if (!msgOk) fails++;

process.exit(fails ? 1 : 0);
