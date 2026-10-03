import assert from "node:assert/strict";
import test from "node:test";
import { ApiAccessError } from "../lib/api-client.ts";
import { githubCatalogError } from "../lib/github-catalog.ts";

test("GitHub 503 is scoped to the connection, not a Forge-wide outage", () => {
  const message = githubCatalogError(new ApiAccessError(503));
  assert.match(message, /GitHub connection is not configured or is temporarily unavailable/);
  assert.match(message, /free offline demo/);
  assert.doesNotMatch(message, /Forge is temporarily unavailable/);
});

test("GitHub authentication failures retain safe sign-in and access guidance", () => {
  for (const status of [401, 403]) {
    const error = new ApiAccessError(status);
    assert.equal(githubCatalogError(error), error.message);
  }
});

test("unexpected GitHub failures never echo raw provider or network details", () => {
  for (const error of [new Error("private-provider-detail"), new ApiAccessError(500), null]) {
    const message = githubCatalogError(error);
    assert.match(message, /Unable to load GitHub repositories/);
    assert.doesNotMatch(message, /private-provider-detail|500/);
  }
});
