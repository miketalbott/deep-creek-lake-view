// Test the point-inspector raytracer exactly as shipped in index.html:
// extract its <script> functions, eval them with the real data files, and
// compare against reference results produced by scripts/verify.py logic.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const web = join(root, "output", "webmap");

// data files define globals via `var`
const ctx = {};
for (const f of ["dem.js", "lakepts.js"]) {
  new Function(readFileSync(join(web, f), "utf8") + `
    this.DEMMETA = typeof DEMMETA !== "undefined" ? DEMMETA : this.DEMMETA;
    this.DEMB64 = typeof DEMB64 !== "undefined" ? DEMB64 : this.DEMB64;
    this.LAKEPTS = typeof LAKEPTS !== "undefined" ? LAKEPTS : this.LAKEPTS;
  `).call(ctx);
}

const html = readFileSync(join(web, "index.html"), "utf8");
const start = html.indexOf("// --- point inspector");
const end = html.indexOf("const inspectGroup");
if (start < 0 || end < 0) throw new Error("inspector block not found in index.html");
let code = html.slice(start, end);
code += "\nthis.raytraceAll = raytraceAll; this.bilinear = bilinear;";

globalThis.atob = s => Buffer.from(s, "base64").toString("binary");
const fn = new Function("DEMMETA", "DEMB64", "LAKEPTS", code + "");
const api = {};
fn.call(api, ctx.DEMMETA, ctx.DEMB64, ctx.LAKEPTS);

const ref = JSON.parse(readFileSync(join(root, "data", "inspector_ref.json"), "utf8"));
let fail = 0;
for (const t of ref.tests) {
  const t0 = performance.now();
  const { vis } = api.raytraceAll(t.ox, t.oy, t.eye);
  const ms = performance.now() - t0;
  const nVis = vis.reduce((a, b) => a + b, 0);
  const diff = Math.abs(nVis - t.expected);
  const pct = (100 * diff) / ctx.LAKEPTS.length;
  const ok = pct <= 1.0; // within 1% of lake points
  if (!ok) fail++;
  console.log(
    `${t.name.padEnd(20)} eye=${t.eye}m  JS=${nVis}  python=${t.expected}  ` +
    `diff=${diff} pts (${pct.toFixed(2)}%)  ${ms.toFixed(0)}ms  ${ok ? "OK" : "FAIL"}`
  );
}
process.exit(fail ? 1 : 0);
