/* Proves a key held in a data file is still counted as used.
 *
 * Every key pattern needs the key as a literal inside the call. Codebases
 * routinely keep keys somewhere else and pass them in by variable:
 *
 *   export const generalPoints = ['about_us.our_way.point_2', ...]
 *   {generalPoints.map((item) => <p>{t(item)}</p>)}
 *
 * Neither line is a call with a literal, so the key scanned as unreferenced,
 * came out UNUSED, and the report asked for `delete from dictionary` on a key
 * live on the website. The scan therefore also collects bare literals, and
 * loadCode admits the ones the dictionary already carries.
 *
 * The safety property is that it is a MEMBERSHIP TEST, never a guess: a
 * literal the dictionary does not hold cannot enter the key set, so
 * MISSING_IN_DATABASE cannot grow and the only status that can change is
 * UNUSED. That is what the "never invented" cases below pin down.
 *
 *   node web/test-data-file-keys.mjs                      # synthetic only
 *   node web/test-data-file-keys.mjs ~/Downloads out/inputs   # + real archives
 */
import { readFileSync, existsSync, writeFileSync, unlinkSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import vm from "node:vm";

// ── the page, loaded the way verify.mjs loads it ────────────────────
const html = readFileSync("web/index.html", "utf8");
const script = html.slice(html.indexOf("<script>") + 8, html.lastIndexOf("</script>"));
const el = new Proxy(
  {
    addEventListener() {}, appendChild() {}, setAttribute() {}, getAttribute: () => null,
    scrollIntoView() {}, click() {}, closest: () => null, insertBefore() {},
    classList: { add() {}, remove() {}, toggle: () => false, contains: () => false },
    style: {}, dataset: {}, children: [], value: "", textContent: "", innerHTML: "",
    clientHeight: 600, scrollTop: 0, files: [],
  },
  { get: (t, k) => (k in t ? t[k] : el), set: (t, k, v) => ((t[k] = v), true) }
);
const win = { matchMedia: () => ({ matches: false }), addEventListener() {}, FileReader: class {}, Blob, URL };
vm.runInContext(script, vm.createContext({
  window: win, console, TextDecoder, TextEncoder, DecompressionStream, Blob, Response,
  document: {
    getElementById: () => el, createElement: () => el, querySelector: () => el,
    querySelectorAll: () => [], addEventListener() {}, documentElement: el, body: el,
  },
  setTimeout, clearTimeout, requestAnimationFrame: (f) => f(),
}));
const { keysFromZip, ZIP_SOURCES, loadCode, scanLines } = win.__recon;

let failed = 0;
function ok(cond, what) {
  console.log(`  ${cond ? "ok  " : "FAIL"}  ${what}`);
  if (!cond) failed++;
}

// The real shape, reduced: the key lives in a data file, the call takes a
// variable, and one literal in the same file is NOT a dictionary key.
const DATA_FILE = [
  "export const generalPoints = [",
  "  'about_us.our_way.point_2',",
  "  'not.in.the.dictionary'",
  "];",
].join("\n");
const VIEW_FILE = [
  "const V = () => generalPoints.map((item) => t(item));",
  "const W = () => t('called.directly');",
].join("\n");

function scanned(files) {
  const hits = new Map(), lits = new Map();
  for (const [rel, text] of files) scanLines(text.split("\n"), rel, hits, lits);
  const body = [...hits.keys()].sort().map((k) => [k, hits.get(k).join(";")]);
  return {
    parts: [{ name: "scan", header: ["key", "locations"], body }],
    literals: lits,
  };
}

console.log("\nkeys held in a data file");
{
  const f = scanned([["app/our_way.data.tsx", DATA_FILE], ["app/our_way.tsx", VIEW_FILE]]);

  // Without the dictionary there is nothing to test membership against.
  const calls = loadCode(f, null);
  ok(calls.has("called.directly"), "a t('literal') call is matched as before");
  ok(!calls.has("about_us.our_way.point_2"),
     "the data-file key is invisible to the call patterns alone");

  const known = new Set(["about_us.our_way.point_2", "called.directly"]);
  const both = loadCode(f, known);
  ok(both.has("about_us.our_way.point_2"), "the dictionary makes the data-file key visible");
  ok(both.get("about_us.our_way.point_2")[0] === "app/our_way.data.tsx:2",
     "and it is located in the data file, at the right line");
  ok(f.literalHits === 1, "one key came from a literal rather than a call");

  // The safety property.
  ok(!both.has("not.in.the.dictionary"),
     "a literal the dictionary does not carry is never invented");
  ok(!both.has("export const generalPoints = ["),
     "and neither is a line of code that happens to be quoted");

  const noise = loadCode(scanned([["app/a.tsx", "const s = 'Hello world';"]]),
                         new Set(["about_us.our_way.point_2"]));
  ok(noise.size === 0, "a file of plain strings contributes nothing");
}

console.log("\nbare words are not keys, however the dictionary spells them");
{
  // The dictionary genuinely holds "approved", "Addresses", "-". Every
  // codebase also holds those strings as enum members and route names, and
  // matching them credited ~30 keys per repository on no evidence.
  const f = scanned([["app/apis.ts", [
    "type Status = 'approved' | 'arrived';",
    "const route = 'Addresses';",
    "const dash = '-';",
    "const real = ['add_tracking.field_name_invoice', 'about_us_meta_title'];",
  ].join("\n")]]);
  const known = new Set(["approved", "arrived", "Addresses", "-",
                         "add_tracking.field_name_invoice", "about_us_meta_title"]);
  const m = loadCode(f, known);
  ok(!["approved", "arrived", "Addresses", "-"].some((k) => m.has(k)),
     "a bare word is never matched as a literal");
  ok(m.has("add_tracking.field_name_invoice"), "a dotted key still is");
  ok(m.has("about_us_meta_title"), "and so is an underscored one");
  ok(f.literalHits === 2, "only the two key-shaped literals counted");

  // Hyphens separate, and letters are not limited to ASCII.
  const g = scanned([["app/b.ts", [
    "const xs = ['home.how-it-works.image', 'profile.balance.fill-balance'];",
    "const y = ['\u0448\u0435\u0439\u043a\u0435\u0440.name'];",
    "const re = '^[0-9]+$';",
  ].join("\n")]]);
  const k2 = new Set(["home.how-it-works.image", "profile.balance.fill-balance",
                      "\u0448\u0435\u0439\u043a\u0435\u0440.name", "^[0-9]+$"]);
  const m2 = loadCode(g, k2);
  ok(m2.has("home.how-it-works.image") && m2.has("profile.balance.fill-balance"),
     "a hyphenated key is matched");
  ok(m2.has("\u0448\u0435\u0439\u043a\u0435\u0440.name"), "and a non-ASCII one");
  ok(!m2.has("^[0-9]+$"), "a regex string is not a key");
}

console.log("\nthe backoffice slot takes no literals at all");
{
  ok(ZIP_SOURCES.backoffice.literals === false,
     "the backoffice slot is opted out");
  ok(ZIP_SOURCES.website.literals !== false && ZIP_SOURCES.mobile.literals !== false,
     "website and mobile are not");
}

console.log("\nthe call pass is unchanged by the literal pass");
{
  const f = scanned([["app/a.tsx", [
    "t('a.one'); i18n.t('a.two');",
    "<Trans i18nKey='a.three' />",
  ].join("\n")]]);
  const m = loadCode(f, new Set());
  ok(["a.one", "a.two", "a.three"].every((k) => m.has(k)),
     "all three patterns still match");
  ok(m.get("a.one")[0] === "app/a.tsx:1", "with their file:line intact");
}

// ── the real archives, against the python scanner ───────────────────
const zipDir = process.argv[2], inputDir = process.argv[3] || "out/inputs";
const CASES = [
  { slot: "website", zip: "front-main.zip", src: "website" },
  { slot: "mobile", zip: "mobile-main.zip", src: "mobile" },
];

function knownFromInputs(dir) {
  const known = new Set();
  for (const [file, col] of [["dictionary.csv", "key"], ["db_keys.csv", "key_value"]]) {
    const p = join(dir, file);
    if (!existsSync(p)) continue;
    const lines = readFileSync(p, "utf8").split("\n");
    const head = (lines[0] || "").split(",");
    const i = head.indexOf(col);
    if (i < 0) continue;
    for (const line of lines.slice(1)) {
      // The key column holds no commas or quotes in these exports.
      const cell = line.split(",")[i];
      if (cell) known.add(cell.trim());
    }
  }
  return known;
}

if (!zipDir || !existsSync(join(inputDir, "dictionary.csv"))) {
  console.log("\nreal archives: skipped — pass a ZIP directory (and an inputs "
              + "directory holding dictionary.csv)");
} else {
  const known = knownFromInputs(inputDir);
  console.log(`\nreal archives — ${known.size} known keys from ${inputDir}`);
  for (const c of CASES) {
    const zipPath = join(zipDir.replace(/^~/, process.env.HOME), c.zip);
    console.log(`\n${c.zip}`);
    if (!existsSync(zipPath)) { console.log(`  skip  ${zipPath} is not here`); continue; }
    const b = readFileSync(zipPath);
    const res = await keysFromZip(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength),
                                 ZIP_SOURCES[c.slot]);
    const body = [...res.hits.keys()].sort().map((k) => [k, res.hits.get(k).join(";")]);
    const f = { parts: [{ name: c.zip, header: ["key", "locations"], body }],
                literals: res.literals };

    const calls = loadCode(f, null).size;
    const all = loadCode(f, known);
    console.log(`  ${calls} keys from calls, ${all.size} with data files `
                + `(+${f.literalHits}), ${res.literals.size} literals seen`);
    ok(f.literalHits > 0, "the archive holds keys outside t() calls");
    ok(all.size === calls + f.literalHits, "every added key is a new one");

    // Parity with trkeys/extract.py on the same clone and the same key set.
    // The key set goes through a file: 26k keys will not fit in argv.
    if (!existsSync(".cache/repos/" + c.src)) { console.log("  skip  no clone to compare against"); continue; }
    const tmp = `.known-${c.src}.json`;
    writeFileSync(tmp, JSON.stringify([...known]));
    const py = `
import json, sys
sys.path.insert(0, ".")
from trkeys import extract, config as cfgmod
cfg = cfgmod.load("config.json")
known = set(json.load(open(${JSON.stringify(tmp)})))
spec = dict(cfg["sources"][${JSON.stringify(c.src)}], root=${JSON.stringify(".cache/repos/" + c.src)})
h = extract.code_keys(spec, cfg["patterns"], cfg["exclude_dirs"], known=known)
print(json.dumps(sorted(h)))`;
    let pyKeys;
    try {
      pyKeys = JSON.parse(execFileSync(".venv/bin/python", ["-c", py], { maxBuffer: 1 << 28 }).toString());
    } catch (e) {
      console.log("  skip  python side unavailable:", e.message.split("\n")[0]);
      continue;
    } finally {
      unlinkSync(tmp);
    }
    const a = new Set(all.keys()), p = new Set(pyKeys);
    const extra = [...a].filter((k) => !p.has(k)), missing = [...p].filter((k) => !a.has(k));
    ok(extra.length === 0, `the page finds nothing python does not${extra.length ? " — " + JSON.stringify(extra.slice(0, 5)) : ""}`);
    ok(missing.length === 0, `and nothing python finds is missed${missing.length ? " — " + JSON.stringify(missing.slice(0, 5)) : ""}`);
  }
}

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
