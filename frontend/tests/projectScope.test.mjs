// Run with:  npm run test:scope   (compiles projectScope.ts, then node --test)
import test from "node:test";
import assert from "node:assert/strict";
import { sfetch, setProjectScope } from "../.tmp-test/projectScope.js";

const settled = (p, ms = 40) => Promise.race([
  p.then(() => "settled", () => "rejected"),
  new Promise((r) => setTimeout(() => r("pending"), ms)),
]);

// A fetch whose response/body we release manually, to reproduce "late response" ordering.
function controllableFetch() {
  const calls = [];
  globalThis.fetch = (url, init) => new Promise((resolve, reject) => {
    calls.push({ url, init, resolve: (body = "{}") => resolve(new Response(body, { status: 200 })), reject });
  });
  return calls;
}

test("a response that arrives after switching project is dropped and never repaints", async () => {
  const calls = controllableFetch();
  setProjectScope("A");
  let painted = null;
  sfetch("/api/projects/A/risk/").then((r) => r.json()).then((d) => { painted = d; });
  setProjectScope("B");                       // user switches to Project B
  calls[0].resolve('{"project":"A"}');        // A's late response arrives
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(painted, null);
});

test("the response for the CURRENT project is delivered", async () => {
  const calls = controllableFetch();
  setProjectScope("B");
  const p = sfetch("/api/projects/B/risk/").then((r) => r.json());
  calls[0].resolve('{"project":"B"}');
  assert.deepEqual(await p, { project: "B" });
});

test("late A cannot overwrite B even when A's response arrives AFTER B's", async () => {
  const calls = controllableFetch();
  const paints = [];
  setProjectScope("A");
  sfetch("/a").then((r) => r.json()).then((d) => paints.push(d.project));
  setProjectScope("B");
  sfetch("/b").then((r) => r.json()).then((d) => paints.push(d.project));
  calls[1].resolve('{"project":"B"}');
  calls[0].resolve('{"project":"A"}');       // stale, arrives last
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(paints, ["B"]);
});

test("switching while the BODY is still being read also drops the result", async () => {
  controllableFetch();
  globalThis.fetch = () => Promise.resolve({
    ok: true, status: 200,
    text: () => new Promise((r) => setTimeout(() => r("slow body"), 20)),
    json: () => new Promise((r) => setTimeout(() => r({ project: "A" }), 20)),
  });
  setProjectScope("A");
  const p = sfetch("/api/projects/A/x/").then((r) => r.text());
  await new Promise((r) => setTimeout(r, 5));     // headers already in
  setProjectScope("B");
  assert.equal(await settled(p, 60), "pending");
});

test("a network error that arrives after switching is swallowed, not shown as B's error", async () => {
  const calls = controllableFetch();
  setProjectScope("A");
  const p = sfetch("/api/projects/A/x/");
  setProjectScope("B");
  calls[0].reject(new Error("boom"));
  assert.equal(await settled(p), "pending");
});

test("an error in the same project still propagates", async () => {
  const calls = controllableFetch();
  setProjectScope("A");
  const p = sfetch("/api/projects/A/x/");
  calls[0].reject(new Error("boom"));
  assert.equal(await settled(p), "rejected");
});

test("non-GET requests are never guarded", async () => {
  const calls = controllableFetch();
  setProjectScope("A");
  const p = sfetch("/api/projects/A/risk-register/k/", { method: "PATCH", body: "{}" });
  setProjectScope("B");
  calls[0].resolve('{"ok":true}');
  assert.equal(await settled(p), "settled");
});

test("response metadata (ok/status) stays readable for the current project", async () => {
  const calls = controllableFetch();
  setProjectScope("A");
  const p = sfetch("/api/projects/A/x/");
  calls[0].resolve("{}");
  const r = await p;
  assert.equal(r.ok, true);
  assert.equal(r.status, 200);
});

// ── Same-project, out-of-order response race (the root-cause class behind
// the post-consolidation "duplicate Sep-23 row" report: an older in-flight
// GET to the SAME URL resolving after a newer one for that URL). ──────────

function queuedFetch() {
  const calls = [];
  globalThis.fetch = (url, init) => new Promise((resolve) => {
    calls.push({ url, resolve: (body) => resolve(new Response(body, { status: 200 })) });
  });
  return calls;
}

test("an older response for the SAME url never overwrites a newer response for that url", async () => {
  const calls = queuedFetch();
  setProjectScope("A");
  const paints = [];
  sfetch("/api/projects/A/versions/").then((r) => r.json()).then((d) => paints.push(d.count));   // request #1 (stale)
  sfetch("/api/projects/A/versions/").then((r) => r.json()).then((d) => paints.push(d.count));   // request #2 (fresh, e.g. after a mutation)
  // Resolve out of order: the NEWER request's response arrives first, the OLDER one arrives after.
  calls[1].resolve('{"count":7}');
  calls[0].resolve('{"count":1}');
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(paints, [7]);        // only the newer (correct) response ever painted
});

test("re-fetching the same url after a prior request already resolved still works normally", async () => {
  const calls = queuedFetch();
  setProjectScope("A");
  const first = sfetch("/api/projects/A/versions/").then((r) => r.json());
  calls[0].resolve('{"count":1}');
  assert.deepEqual(await first, { count: 1 });
  const second = sfetch("/api/projects/A/versions/").then((r) => r.json());
  calls[1].resolve('{"count":7}');
  assert.deepEqual(await second, { count: 7 });
});

test("different urls do not interfere with each other's sequencing", async () => {
  const calls = queuedFetch();
  setProjectScope("A");
  const a = sfetch("/api/projects/A/versions/").then((r) => r.json());
  const b = sfetch("/api/projects/A/risk/").then((r) => r.json());
  calls[1].resolve('{"who":"risk"}');
  calls[0].resolve('{"who":"versions"}');
  assert.deepEqual(await Promise.all([a, b]), [{ who: "versions" }, { who: "risk" }]);
});

// ── dedupeVersionsById (defensive rendering guard) ─────────────────────────
import { dedupeVersionsById } from "../.tmp-test/dateFormat.js";

test("dedupeVersionsById collapses duplicate ids, keeping the first occurrence", () => {
  const out = dedupeVersionsById([
    { id: "b91d33b0", role: "CURRENT" },
    { id: "7d3a336c", role: "PREVIOUS" },
    { id: "b91d33b0", role: undefined },   // the exact reported symptom: a second, role-less row for the same id
  ]);
  assert.deepEqual(out.map((v) => v.id), ["b91d33b0", "7d3a336c"]);
  assert.equal(out[0].role, "CURRENT");
});

test("dedupeVersionsById is a no-op when there are no duplicates", () => {
  const input = [{ id: "1" }, { id: "2" }, { id: "3" }];
  assert.deepEqual(dedupeVersionsById(input), input);
});
