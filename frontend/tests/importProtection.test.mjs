// Run with:  npm run test:importProtection
//
// Phase 2 (Import Protection) / Phase 3-4 (Schedule Deletion Auditing)
// frontend wiring: these pin the rules for surfacing the backend's
// structured confirmation-gate responses and for sending the exact
// confirmation phrases/flags the backend checks against.
import test from "node:test";
import assert from "node:assert/strict";
import {
  parseErrorMessage, buildImportConfirmationFields, daysRemaining,
  deleteConfirmationBody, restoreConfirmationBody,
} from "../.tmp-test/importProtection.js";

test("parseErrorMessage prefers message over error for the new 409 codes", () => {
  const body = JSON.stringify({
    error: "EXACT_DUPLICATE_IMPORT",
    message: "This file is an exact content duplicate of the existing version \"2026-08-01 Update\".",
  });
  assert.equal(parseErrorMessage(body), "This file is an exact content duplicate of the existing version \"2026-08-01 Update\".");
});

test("parseErrorMessage falls back to error when message is absent", () => {
  assert.equal(parseErrorMessage(JSON.stringify({ error: "Destination project not found." })), "Destination project not found.");
});

test("parseErrorMessage falls back to the raw text when the body is not JSON", () => {
  assert.equal(parseErrorMessage("Internal Server Error"), "Internal Server Error");
});

test("parseErrorMessage never crashes on an empty body", () => {
  assert.equal(parseErrorMessage(""), "Request failed.");
});

test("buildImportConfirmationFields sends nothing when nothing was acknowledged", () => {
  assert.deepEqual(buildImportConfirmationFields({}), {});
});

test("buildImportConfirmationFields maps each acknowledgement to its exact backend field", () => {
  assert.deepEqual(buildImportConfirmationFields({ confirmDuplicate: true }), { confirmDuplicateImport: "true" });
  assert.deepEqual(buildImportConfirmationFields({ confirmIdentity: true }), { confirmIdentityMismatch: "true" });
  assert.deepEqual(buildImportConfirmationFields({ identityCandidateDismissed: true }), { confirmNewProject: "true" });
});

test("buildImportConfirmationFields combines multiple acknowledgements", () => {
  assert.deepEqual(
    buildImportConfirmationFields({ confirmDuplicate: true, confirmIdentity: true }),
    { confirmDuplicateImport: "true", confirmIdentityMismatch: "true" },
  );
});

test("buildImportConfirmationFields ignores explicit false the same as absent", () => {
  assert.deepEqual(buildImportConfirmationFields({ confirmDuplicate: false, confirmIdentity: false }), {});
});

test("daysRemaining is null with no deadline", () => {
  assert.equal(daysRemaining(null), null);
  assert.equal(daysRemaining(undefined), null);
});

test("daysRemaining is null for an unparseable deadline", () => {
  assert.equal(daysRemaining("not-a-date"), null);
});

test("daysRemaining is null once the deadline has passed — never a negative count", () => {
  const now = new Date("2026-06-01T00:00:00Z");
  assert.equal(daysRemaining("2026-05-01T00:00:00Z", now), null);
});

test("daysRemaining counts whole days up to a future deadline", () => {
  const now = new Date("2026-06-01T00:00:00Z");
  assert.equal(daysRemaining("2026-06-11T00:00:00Z", now), 10);
});

test("deleteConfirmationBody always sends the exact phrase the backend checks", () => {
  assert.deepEqual(JSON.parse(deleteConfirmationBody()), { confirmation: "DELETE" });
});

test("deleteConfirmationBody includes reason/user only when supplied", () => {
  assert.deepEqual(
    JSON.parse(deleteConfirmationBody("Superseded by re-export", "jane@example.com")),
    { confirmation: "DELETE", reason: "Superseded by re-export", user: "jane@example.com" },
  );
});

test("restoreConfirmationBody always sends the exact phrase the backend checks", () => {
  assert.deepEqual(JSON.parse(restoreConfirmationBody()), { confirmation: "RESTORE" });
});
