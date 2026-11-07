import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const web = fileURLToPath(new URL("../", import.meta.url));
const filename = path.join(web, "lib", "msalAuth.ts");
const input = fs.readFileSync(filename, "utf8");
const parsed = ts.createSourceFile(filename, input, ts.ScriptTarget.Latest, true);
const names = new Set([
  "ensureAccount", "setActiveAccount", "getPopupRedirectUri", "shouldFallbackToRedirect",
]);
const statements = parsed.statements.filter(statement =>
  ts.isFunctionDeclaration(statement) && statement.name && names.has(statement.name.text),
);
assert.equal(statements.length, names.size, "Exercise the actual MSAL interaction functions.");
const compiled = ts.transpileModule(
  statements.map(statement => statement.getFullText(parsed)).join("\n")
    + "\nexport { ensureAccount, shouldFallbackToRedirect }",
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } },
).outputText;
const exportsObject = {};
const startPage = "https://example.test/login?returnTo=%2Finvoices";
vm.runInNewContext(compiled, {
  exports: exportsObject, URL, window: { location: { href: startPage } },
}, { filename, timeout: 3000 });

const config = { redirectUri: "https://example.test/" };
const scopes = ["api://test/access"];
const account = { username: "test-user" };
const calls = [];
function client(popupResult) {
  return {
    getActiveAccount: () => null,
    getAllAccounts: () => [],
    setActiveAccount: value => calls.push(["active", value]),
    loginPopup: async request => {
      calls.push(["popup", request]);
      return popupResult();
    },
    loginRedirect: async request => calls.push(["redirect", request]),
  };
}

await exportsObject.ensureAccount(client(() => ({ account })), config, scopes, { interactive: "popup" });
assert.equal(calls.filter(([name]) => name === "popup").length, 1);
assert.equal(calls.filter(([name]) => name === "redirect").length, 0);
assert.equal(calls.find(([name]) => name === "active")[1], account);

for (const errorCode of ["popup_window_error", "empty_window_error"]) {
  calls.length = 0;
  await assert.rejects(
    exportsObject.ensureAccount(
      client(() => { throw { errorCode }; }), config, scopes, { interactive: "popup" },
    ),
    /MSAL sign-in was started/,
  );
  const redirect = calls.find(([name]) => name === "redirect")[1];
  assert.equal(redirect.redirectStartPage, startPage);
  assert.equal(redirect.redirectUri, "https://example.test/auth/msal/callback");
  assert.equal(redirect.scopes, scopes);
}

for (const errorCode of ["access_denied", "interaction_required", "invalid_client"]) {
  calls.length = 0;
  const original = { errorCode };
  await assert.rejects(
    exportsObject.ensureAccount(
      client(() => { throw original; }), config, scopes, { interactive: "popup" },
    ),
    error => error === original,
  );
  assert.equal(calls.filter(([name]) => name === "redirect").length, 0);
}

calls.length = 0;
await assert.rejects(
  exportsObject.ensureAccount(client(() => { throw new Error("Unexpected popup"); }), config, scopes, { interactive: "redirect" }),
  /MSAL sign-in was started/,
);
assert.equal(calls.filter(([name]) => name === "popup").length, 0);
assert.equal(calls.filter(([name]) => name === "redirect").length, 1);
assert.equal(exportsObject.shouldFallbackToRedirect(null), false);
assert.equal(exportsObject.shouldFallbackToRedirect(new Error("Other error")), false);

const rootFilename = path.join(web, "app", "root.tsx");
const rootSource = fs.readFileSync(rootFilename, "utf8");
const rootParsed = ts.createSourceFile(rootFilename, rootSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const rootApp = rootParsed.statements.find(statement =>
  ts.isFunctionDeclaration(statement) && statement.name?.text === "App",
);
assert.ok(rootApp, "Exercise the actual application shell.");
const rootCompiled = ts.transpileModule(rootApp.getFullText(rootParsed) + "\nexport { App }", {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const rootExports = {};
const location = { pathname: "/auth/msal/callback" };
const Outlet = () => {};
const AuthProvider = () => {};
vm.runInNewContext(rootCompiled, {
  exports: rootExports,
  require: specifier => {
    assert.equal(specifier, "react/jsx-runtime");
    return { jsx: (type, props) => ({ type, props }) };
  },
  useLocation: () => location,
  useLoaderData: () => ({ env: {}, msalConfig: {}, otelConfig: {} }),
  useEffect: () => {},
  Outlet,
  AuthProvider,
}, { filename: rootFilename, timeout: 3000 });
assert.equal(rootExports.App().type, Outlet, "The redirect bridge must not initialize an AuthProvider.");
location.pathname = "/invoices";
assert.equal(rootExports.App().type, AuthProvider, "Normal routes must retain authentication.");
console.log("MSAL popup, blocked-popup redirect, auth errors and isolated redirect bridge passed.");
