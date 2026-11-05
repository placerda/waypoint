import assert from "node:assert/strict";
import test from "node:test";
import { validatePrivateConfiguration } from "../private-config.js";

const valid = {
  NODE_ENV: "production",
  API_ENDPOINT_HTTP: "https://private-api.example.org",
  WAYPOINT_MSAL_ENABLED: "true",
  WAYPOINT_MSAL_TENANT_ID: "directory",
  WAYPOINT_MSAL_CLIENT_ID: "browser-client",
  WAYPOINT_MSAL_API_SCOPE: "api://waypoint/user_impersonation",
  WAYPOINT_MSAL_REDIRECT_URI: "https://private-web.example.org/auth/msal/callback",
};

test("production requires explicit private endpoints and delegated auth", () => {
  assert.doesNotThrow(() => validatePrivateConfiguration(valid));
  for (const key of Object.keys(valid).filter((key) => key !== "NODE_ENV")) {
    assert.throws(() => validatePrivateConfiguration({ ...valid, [key]: "" }));
  }
});

test("production never defaults to plaintext API or credential-bearing URLs", () => {
  for (const endpoint of ["http://localhost:8000", "https://user:password@api.example.org"]) {
    assert.throws(() => validatePrivateConfiguration({ ...valid, API_ENDPOINT_HTTP: endpoint }));
  }
  assert.throws(() => validatePrivateConfiguration({
    ...valid, WAYPOINT_MSAL_REDIRECT_URI: "http://localhost:3000/auth/msal/callback",
  }));
});

test("explicit local development remains available without production settings", () => {
  assert.doesNotThrow(() => validatePrivateConfiguration({ NODE_ENV: "development" }));
});
