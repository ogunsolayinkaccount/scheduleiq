// Run with: npm run test:auth
//
// Phase 3 (Authentication and Authorization) — the pure logic behind the
// global CSRF fetch wrapper (installCsrfFetchWrapper itself touches
// window.fetch/document.cookie and isn't exercised here — these pin the
// decision functions it's built from).
import test from "node:test";
import assert from "node:assert/strict";
import { readCookie, needsCsrfToken, isSameOriginUrl } from "../.tmp-test/auth.js";

test("readCookie finds a cookie among several", () => {
  assert.equal(readCookie("csrftoken", "sessionid=abc; csrftoken=XYZ123; other=1"), "XYZ123");
});

test("readCookie returns null when the cookie is absent", () => {
  assert.equal(readCookie("csrftoken", "sessionid=abc"), null);
});

test("readCookie handles an empty cookie string", () => {
  assert.equal(readCookie("csrftoken", ""), null);
});

test("readCookie decodes URL-encoded values", () => {
  assert.equal(readCookie("csrftoken", "csrftoken=a%2Fb%3Dc"), "a/b=c");
});

test("readCookie matches the exact name, not a prefix", () => {
  assert.equal(readCookie("token", "csrftoken=XYZ123"), null);
});

test("needsCsrfToken is false for safe methods", () => {
  for (const m of ["GET", "HEAD", "OPTIONS", "TRACE", "get", "Head"]) {
    assert.equal(needsCsrfToken(m), false, m);
  }
});

test("needsCsrfToken is true for mutating methods", () => {
  for (const m of ["POST", "PUT", "PATCH", "DELETE"]) {
    assert.equal(needsCsrfToken(m), true, m);
  }
});

test("needsCsrfToken defaults to GET (false) when method is omitted", () => {
  assert.equal(needsCsrfToken(undefined), false);
});

test("isSameOriginUrl accepts a relative path", () => {
  assert.equal(isSameOriginUrl("/api/projects/", "http://localhost:5170"), true);
});

test("isSameOriginUrl rejects a protocol-relative URL (could point anywhere)", () => {
  assert.equal(isSameOriginUrl("//evil.example.com/api/", "http://localhost:5170"), false);
});

test("isSameOriginUrl accepts an absolute URL matching the current origin", () => {
  assert.equal(isSameOriginUrl("http://localhost:5170/api/projects/", "http://localhost:5170"), true);
});

test("isSameOriginUrl rejects a different origin", () => {
  assert.equal(isSameOriginUrl("http://evil.example.com/api/projects/", "http://localhost:5170"), false);
});

test("isSameOriginUrl rejects a different port on the same host", () => {
  assert.equal(isSameOriginUrl("http://localhost:9999/api/projects/", "http://localhost:5170"), false);
});

test("isSameOriginUrl treats an empty url as safe (resolves relative to the page)", () => {
  assert.equal(isSameOriginUrl("", "http://localhost:5170"), true);
});
