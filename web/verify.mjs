/* Runs the browser reconciliation under node against the same CSV inputs
 * the python CLI used, so the two implementations can be compared. The
 * page script is an IIFE that wires up DOM listeners on load, so the DOM
 * is stubbed just enough for it to reach the end and export __recon.
 *
 *   node web/verify.mjs out/inputs
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";

const dir = process.argv[2] || "out/inputs";
const html = readFileSync("web/index.html", "utf8");
const script = html.slice(
  html.indexOf("<script>") + 8,
  html.lastIndexOf("</script>")
);

const el = new Proxy(
  {
    addEventListener() {}, appendChild() {}, setAttribute() {},
    getAttribute: () => null, scrollIntoView() {}, click() {},
    closest: () => null, insertBefore() {},
    classList: { add() {}, remove() {}, toggle: () => false, contains: () => false },
    style: {}, dataset: {}, children: [], value: "", textContent: "", innerHTML: "",
    clientHeight: 600, scrollTop: 0, files: [],
  },
  { get: (t, k) => (k in t ? t[k] : el), set: (t, k, v) => ((t[k] = v), true) }
);

const win = {
  matchMedia: () => ({ matches: false }),
  addEventListener() {}, FileReader: class {}, Blob: class {},
  URL: { createObjectURL: () => "", revokeObjectURL() {} },
};
const ctx = vm.createContext({
  window: win,
  document: {
    getElementById: () => el, createElement: () => el,
    querySelector: () => el, addEventListener() {},
    documentElement: el,
  },
  console,
});
ctx.globalThis = ctx;
vm.runInContext(script, ctx);
const R = win.__recon;

const read = (n) => {
  const rows = R.parseCSV(readFileSync(join(dir, n), "utf8"));
  return { header: rows[0], body: rows.slice(1), rows: rows.length - 1 };
};

const db = R.loadDb(read("db_keys.csv"));
const dict = R.loadDict(read("dictionary.csv"));
const usage = {
  website: R.loadCode(read("website_keys.csv")),
  mobile: R.loadCode(read("mobile_keys.csv")),
  backoffice: R.loadCode(read("backoffice_keys.csv")),
};

console.log(
  `parsed: db=${db.length} dict=${dict.length} web=${usage.website.size} mobile=${usage.mobile.size}`
);

for (const scoping of ["existing", "global"]) {
  const { rows } = R.reconcile(db, dict, usage, scoping);
  const per = new Map();
  for (const r of rows) {
    if (!per.has(r.app)) per.set(r.app, { total: 0 });
    const c = per.get(r.app);
    c.total++;
    c[r.status] = (c[r.status] || 0) + 1;
  }
  console.log(`\n--- scoping=${scoping} (${rows.length} rows) ---`);
  console.log(
    "app".padEnd(8) + ["total", "UNUSED", "SOURCE_MISSING", "SOURCE_MISMATCH", "MULTIPLE_SOURCES", "MISSING_IN_DATABASE"]
      .map((h) => h.slice(0, 9).padStart(10)).join("")
  );
  for (const [app, c] of [...per].sort()) {
    console.log(
      app.padEnd(8) +
        [c.total, c.UNUSED, c.SOURCE_MISSING, c.SOURCE_MISMATCH, c.MULTIPLE_SOURCES, c.MISSING_IN_DATABASE]
          .map((v) => String(v || 0).padStart(10)).join("")
    );
  }
}
