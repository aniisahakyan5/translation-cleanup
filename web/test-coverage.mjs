/* Tests the coverage checks that catch a partially-loaded input set.
 *
 * The failure they guard against is silent: an application with dictionary
 * rows but no DB rows still produces a full set of plausible-looking rows,
 * every one of them marked UNUSED, which is indistinguishable from a real
 * cleanup finding.
 *
 *   node web/test-coverage.mjs
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const html = readFileSync("web/index.html", "utf8");
const script = html.slice(html.indexOf("<script>") + 8, html.lastIndexOf("</script>"));

const el = new Proxy(
  {
    addEventListener() {}, appendChild() {}, setAttribute() {}, getAttribute: () => null,
    scrollIntoView() {}, click() {}, closest: () => null,
    classList: { add() {}, remove() {}, toggle: () => false, contains: () => false },
    style: {}, dataset: {}, children: [], value: "", textContent: "", innerHTML: "",
    clientHeight: 600, scrollTop: 0, files: [],
  },
  { get: (t, k) => (k in t ? t[k] : el), set: (t, k, v) => ((t[k] = v), true) }
);
const win = { matchMedia: () => ({ matches: false }), addEventListener() {}, FileReader: class {}, Blob: class {}, URL: {} };
const ctx = vm.createContext({
  window: win,
  document: { getElementById: () => el, createElement: () => el, querySelector: () => el, addEventListener() {}, documentElement: el },
  console,
});
ctx.globalThis = ctx;
vm.runInContext(script, ctx);
const R = win.__recon;

const db = (app, key) => ({ app, key, table: "page", column: "title", scope: "direct" });
const dct = (app, key, source = null) => ({ app, key, source, type: "text", description: "" });
const use = (...keys) => new Map(keys.map((k) => [k, []]));

let failures = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failures++;
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}${ok ? "" : `  (got ${got}, want ${want})`}`);
}
function headsOf(w) { return w.map((x) => x.head.replace(/<[^>]+>/g, "")).join(" ~ "); }

console.log("\nthe reported scenario — one DB file, dictionaries for five apps, no web keys");
{
  const dbRows = [db("kz", "a")];
  const dictRows = ["am", "cy", "kz", "ru", "uz"].map((a) => dct(a, "a"));
  const usage = { website: new Map(), mobile: use("a"), backoffice: new Map() };
  const w = R.coverage(dbRows, dictRows, usage);
  console.log("    " + headsOf(w));
  check("flags the four applications as skipped", /am, cy, ru, uz skipped/.test(headsOf(w)), true);
  check("flags the missing web keys", /No web keys loaded/.test(headsOf(w)), true);
  check("does not flag mobile, which was loaded", /No mobile keys loaded/.test(headsOf(w)), false);
  check("two problems in total", w.length, 2);
}

console.log("\na complete input set stays silent");
{
  const apps = ["am", "cy", "kz", "ru", "uz"];
  const w = R.coverage(
    apps.map((a) => db(a, "a")),
    apps.map((a) => dct(a, "a")),
    { website: use("a"), mobile: use("a"), backoffice: new Map() }
  );
  check("no warnings", w.length, 0);
}

console.log("\nthe reverse gap — DB rows for an application with no dictionary");
{
  const w = R.coverage(
    [db("kz", "a"), db("jm", "b")],
    [dct("kz", "a")],
    { website: use("a"), mobile: use("a"), backoffice: new Map() }
  );
  check("flags jm", /jm has DB sources but no dictionary rows/.test(headsOf(w)), true);
  check("one problem", w.length, 1);
}

console.log("\nthe skipped application is named, and only that one");
{
  const one = R.coverage([db("kz", "a")], [dct("kz", "a"), dct("am", "a")],
    { website: use("a"), mobile: use("a"), backoffice: new Map() });
  check("names am as skipped", /am skipped/.test(headsOf(one)), true);
  check("does not name kz, which has DB sources", /kz skipped/.test(headsOf(one)), false);
}

console.log("\nscope comes from the DB files, not the dictionary");
{
  // A dictionary covering five applications plus one DB export must
  // reconcile exactly one application, not five.
  const dictRows = ["am", "cy", "kz", "ru", "uz"].map((a) => dct(a, "a"));
  const one = R.reconcile([db("kz", "a")], dictRows,
    { website: new Map(), mobile: new Map(), backoffice: new Map() }, "existing");
  check("only kz reconciled", one.apps.join(","), "kz");
  check("the other four are reported as skipped", one.skipped.join(","), "am,cy,ru,uz");

  const two = R.reconcile([db("kz", "a"), db("am", "a")], dictRows,
    { website: new Map(), mobile: new Map(), backoffice: new Map() }, "existing");
  check("adding am widens the scope to both", two.apps.join(","), "am,kz");
  check("and only three remain skipped", two.skipped.join(","), "cy,ru,uz");

  const noRows = one.rows.filter((r) => ["am","cy","ru","uz"].includes(r.app));
  check("no rows emitted for skipped applications", noRows.length, 0);
}

console.log(failures ? `\n${failures} FAILED\n` : "\nall passed\n");
process.exit(failures ? 1 : 0);
