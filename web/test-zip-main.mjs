/* Proves a repository import can only ever contribute main-branch keys.
 *
 * Two halves:
 *
 *   1. The guard, on synthetic archives built here. A "Download ZIP" is a
 *      snapshot of whatever branch was on screen and the file name proves
 *      nothing, so the page reads the "<repo>-<branch>/" wrapper GitHub puts
 *      inside and refuses anything that is not main. These cases always run.
 *
 *   2. The extraction, on the real archives. Every key the page pulls out of
 *      a ZIP is compared against a key found by walking the git tree at the
 *      commit the ZIP was cut from -- a commit first checked to be on
 *      origin/main. Equal sets, equal file:line locations, or it fails.
 *      Runs only when the archives and clones are present.
 *
 *   node web/test-zip-main.mjs                     # guard only
 *   node web/test-zip-main.mjs ~/Downloads .cache/repos
 */
import { readFileSync, existsSync } from "node:fs";
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
const { keysFromZip, ZIP_SOURCES, requireMain } = win.__recon;

let failed = 0;
function ok(cond, what) {
  console.log(`  ${cond ? "ok  " : "FAIL"}  ${what}`);
  if (!cond) failed++;
}

// ── a minimal ZIP writer, stored entries only ───────────────────────
// keysFromZip inflates only method 8, so stored entries exercise every
// header field the reader touches without pulling in a deflater.
function zip(files, comment) {
  const enc = new TextEncoder(), locals = [], central = [];
  let off = 0;
  for (const [name, text] of files) {
    const n = enc.encode(name), body = enc.encode(text ?? "");
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true);
    lh.setUint16(8, 0, true);                 // stored
    lh.setUint32(18, body.length, true);      // compressed size
    lh.setUint32(22, body.length, true);      // uncompressed size
    lh.setUint16(26, n.length, true);
    locals.push(new Uint8Array(lh.buffer), n, body);

    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true);
    cd.setUint16(10, 0, true);
    cd.setUint32(20, body.length, true);
    cd.setUint32(24, body.length, true);
    cd.setUint16(28, n.length, true);
    cd.setUint32(42, off, true);              // local header offset
    central.push(new Uint8Array(cd.buffer), n);
    off += 30 + n.length + body.length;
  }
  const cdBytes = central.reduce((a, b) => a + b.length, 0);
  const cmt = enc.encode(comment ?? "");
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(8, files.length, true);
  eocd.setUint16(10, files.length, true);
  eocd.setUint32(12, cdBytes, true);
  eocd.setUint32(16, off, true);
  eocd.setUint16(20, cmt.length, true);

  const parts = [...locals, ...central, new Uint8Array(eocd.buffer), cmt];
  const out = new Uint8Array(parts.reduce((a, b) => a + b.length, 0));
  let p = 0;
  for (const b of parts) { out.set(b, p); p += b.length; }
  return out.buffer;
}

const SRC = "const a = t('one.key');\nconst b = t('two.key');\n";
const SHA = "6023b5cf0801b042843f983393375564b98f6a39";

// What GitHub actually ships: a wrapper directory entry, then the files.
const tree = (root, inner) => [[root + "/", ""], [root + "/" + inner, SRC]];
const web = (root) => tree(root, "app/a.ts");

const read = (files, spec) => keysFromZip(zip(files, SHA), spec);
async function refusal(files, spec) {
  try { await read(files, spec); return null; } catch (e) { return e; }
}

console.log("\nbranch guard — archives");
{
  const res = await read(web("front-main"), ZIP_SOURCES.website);
  ok(res.origin.branch === "main", "front-main.zip reads as main");
  ok(res.origin.commit === SHA, "the commit comes from the archive comment");
  ok([...res.hits.keys()].join() === "one.key,two.key", "and its keys come through");

  const mob = await read(tree("mobile-main", "src/a.ts"), ZIP_SOURCES.mobile);
  ok(mob.origin.branch === "main", "mobile-main.zip reads as main");

  const dev = await refusal(web("front-develop"), ZIP_SOURCES.website);
  ok(dev && dev.notMain, "front-develop.zip is refused");
  ok(dev && /"develop"/.test(dev.message), "the error names the branch it saw");

  const feat = await refusal(web("front-feature-xyz"), ZIP_SOURCES.website);
  ok(feat && feat.notMain, "a feature branch is refused");

  // GitHub flattens "release/main" to "release-main"; a suffix test alone
  // would wave this through as main.
  const rel = await refusal(web("front-release-main"), ZIP_SOURCES.website);
  ok(rel && rel.notMain, 'a branch called "release/main" is refused');
  ok(rel && /"release-main"/.test(rel.message), "…and is named as release-main, not main");

  // Renaming the file cannot launder the branch: the wrapper is what counts.
  const renamed = await refusal(web("front-hotfix"), ZIP_SOURCES.website);
  ok(renamed && renamed.notMain, "renaming the .zip does not change the verdict");

  // A fork or renamed repo: without the expected prefix there is no dash to
  // split repo from branch on, so the branch is unknowable. "our-front-main"
  // is repo "our-front" on main just as readily as repo "our" on
  // "front-main", and a "-main" suffix test would swallow both.
  const fork = await refusal(web("our-front-main"), ZIP_SOURCES.website);
  ok(fork && fork.notMain, "an unrecognised repo name is refused, suffix or not");
  ok(fork && /does not come from front/.test(fork.message),
     "…and the error says so, rather than blaming the branch");
  const forkDev = await refusal(web("our-front-dev"), ZIP_SOURCES.website);
  ok(forkDev && forkDev.notMain, "…as it is without the suffix");

  // The same rule keeps one application's keys out of another's slot, which
  // is reachable by choosing a slot and picking the file there. Both of
  // these used to pass as main.
  const crossed = await refusal(tree("mobile-main", "src/a.ts"), ZIP_SOURCES.website);
  ok(crossed && crossed.notMain, "a mobile archive is refused by the website slot");
  const crossedFeat = await refusal(tree("mobile-feature-main", "src/a.ts"), ZIP_SOURCES.website);
  ok(crossedFeat && crossedFeat.notMain,
     'and so is mobile\'s "feature-main", which the suffix test read as main');

  // The suffix trap on the repo's own archives, too.
  const hotfix = await refusal(web("front-hotfix-main"), ZIP_SOURCES.website);
  ok(hotfix && /"hotfix-main"/.test(hotfix.message),
     'a branch called "hotfix-main" is named as such, not read as main');

  // Not a GitHub archive at all: no single wrapper to read a branch from.
  const loose = await refusal([["app/a.ts", SRC], ["src/b.ts", SRC]], ZIP_SOURCES.website);
  ok(loose && loose.notMain, "a wrapper-less archive is refused");
}

console.log("\nbranch guard — expanded folders (Safari unzips on download)");
for (const [name, slot, want] of [["front-main", "website", true],
                                  ["front-develop", "website", false],
                                  ["front-release-main", "website", false],
                                  ["front-hotfix-main", "website", false],
                                  ["our-front-main", "website", false],
                                  ["mobile-main", "website", false],
                                  ["mobile-main", "mobile", true],
                                  ["app-main", "backoffice", true]]) {
  let got = true;
  try { requireMain(name, ZIP_SOURCES[slot], "That folder"); }
  catch { got = false; }
  ok(got === want, `${name}/ in the ${slot} slot is ${want ? "accepted" : "refused"}`);
}

// ── the real archives, against main itself ──────────────────────────
const zipDir = process.argv[2], repoDir = process.argv[3];
const CASES = [
  { slot: "website",    zip: "front-main.zip",  repo: "website" },
  { slot: "mobile",     zip: "mobile-main.zip", repo: "mobile" },
  { slot: "backoffice", zip: "app-main.zip",    repo: "backoffice" },
];

// The same rules as the page, applied to a git tree instead of an archive.
const PATTERNS = [
  /\bt\(\s*['"`]([^'"`\n]+)['"`]/g,
  /\bi18n(?:ext)?\.t\(\s*['"`]([^'"`\n]+)['"`]/g,
  /i18nKey\s*=\s*['"{]+\s*['"`]?([^'"`}\n]+)['"`]?/g,
];
const SKIP = ["node_modules", "dist", "build", ".next", "ios", "android",
              "__tests__", "coverage", ".expo", ".git"];

function keysFromGit(repo, sha, spec) {
  const git = (...a) => execFileSync("git", ["-C", repo, ...a], { maxBuffer: 1 << 28 }).toString();
  const files = git("ls-tree", "-r", "--name-only", sha).split("\n").filter((rel) => {
    if (!rel || !spec.exts.some((x) => rel.endsWith(x))) return false;
    const parts = rel.split("/");
    return spec.include.includes(parts[0]) && !parts.some((x) => SKIP.includes(x));
  });
  const hits = new Map();
  for (const rel of files) {
    const lines = git("show", `${sha}:${rel}`).split("\n");
    for (let ln = 0; ln < lines.length; ln++) {
      for (const rx of PATTERNS) {
        rx.lastIndex = 0;
        let m;
        while ((m = rx.exec(lines[ln])) !== null) {
          const key = m[1].trim();
          if (!key) continue;
          if (!hits.has(key)) hits.set(key, []);
          const w = hits.get(key), loc = rel + ":" + (ln + 1);
          if (w.length < 5 && !w.includes(loc)) w.push(loc);
        }
      }
    }
  }
  return { hits, files };
}

if (!zipDir || !repoDir) {
  console.log("\nreal archives: skipped — pass a ZIP directory and a clone directory to run them");
} else {
  for (const c of CASES) {
    const zipPath = join(zipDir.replace(/^~/, process.env.HOME), c.zip);
    const repo = join(repoDir, c.repo);
    console.log(`\n${c.zip} vs main`);
    if (!existsSync(zipPath) || !existsSync(repo)) {
      console.log(`  skip  ${!existsSync(zipPath) ? zipPath : repo} is not here`);
      continue;
    }
    const buf = readFileSync(zipPath);
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    const spec = ZIP_SOURCES[c.slot];

    let res = null, err = null;
    try { res = await keysFromZip(ab, spec); } catch (e) { err = e; }
    ok(!err, err ? `accepted by the guard (${err.message})` : "accepted by the guard as main");
    if (!res) continue;

    // The archive's own claim is worth nothing until the commit is placed
    // on origin/main.
    const sha = res.origin.commit;
    ok(!!sha, `the archive names a commit (${sha ? sha.slice(0, 7) : "none"})`);
    let onMain = false, behind = "?";
    try {
      execFileSync("git", ["-C", repo, "merge-base", "--is-ancestor", sha, "origin/main"]);
      behind = execFileSync("git", ["-C", repo, "rev-list", "--count", `${sha}..origin/main`]).toString().trim();
      onMain = true;
    } catch { /* absent, or on another branch */ }
    ok(onMain, `${sha ? sha.slice(0, 7) : "?"} is on origin/main (main is ${behind} commits ahead)`);
    if (!onMain) continue;

    const B = keysFromGit(repo, sha, spec);
    const a = new Set(res.hits.keys()), b = new Set(B.hits.keys());
    const extra = [...a].filter((k) => !b.has(k));
    const missing = [...b].filter((k) => !a.has(k));
    const locs = [...a].filter((k) => b.has(k) &&
      res.hits.get(k).slice().sort().join("|") !== B.hits.get(k).slice().sort().join("|"));

    console.log(`  ${a.size} keys from the ZIP, ${b.size} from main across ${B.files.length} files`);
    ok(extra.length === 0, `nothing imported that main lacks${extra.length ? " — " + JSON.stringify(extra.slice(0, 5)) : ""}`);
    ok(missing.length === 0, `nothing on main missed${missing.length ? " — " + JSON.stringify(missing.slice(0, 5)) : ""}`);
    ok(locs.length === 0, `every file:line matches${locs.length ? " — e.g. " + locs[0] : ""}`);
  }
}

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
