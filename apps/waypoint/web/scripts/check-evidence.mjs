import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import i18next from "i18next";
import { I18nextProvider, useTranslation } from "react-i18next";

const web = fileURLToPath(new URL("../", import.meta.url));
const appRequire = createRequire(import.meta.url);
const filename = path.join(web, "app", "routes", "invoices.tsx");
const input = fs.readFileSync(filename, "utf8");
const parsed = ts.createSourceFile(
  filename, input, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);
const names = new Set([
  "CaseSection", "ClauseSection", "EvidenceSection", "EvidenceGroup",
  "CitationItem", "SourceReference", "StanceTag", "cleanSourceRef",
  "matchBasisDocument", "lanePlaneKey", "PLANE_LABELS", "AGENT_PLANES", "IQ_PLANES",
]);
const statements = parsed.statements.filter(statement =>
  (ts.isFunctionDeclaration(statement) && statement.name && names.has(statement.name.text))
  || (ts.isVariableStatement(statement) && statement.declarationList.declarations.some(
    declaration => ts.isIdentifier(declaration.name) && names.has(declaration.name.text),
  )),
);
assert.equal(statements.length, names.size, "Every evidence component must be exercised.");

// Render the real route functions without its unrelated router and browser imports.
const selected = statements.map(statement => statement.getFullText(parsed)).join("\n")
  + "\nexport { ClauseSection, EvidenceSection }";
const compiled = ts.transpileModule(selected, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX,
  },
}).outputText;
const exportsObject = {};
vm.runInNewContext(compiled, {
  require: appRequire,
  exports: exportsObject,
  useTranslation,
  useLabels: () => ({
    planeScope: value => value,
    score: value => String(value),
    decision: value => value,
  }),
}, { filename, timeout: 3000 });

const resources = Object.fromEntries(["en", "es"].map(language => [
  language,
  {
    invoices: JSON.parse(fs.readFileSync(
      path.join(web, "app", "i18n", "locales", language, "invoices.json"), "utf8",
    )),
  },
]));
const i18n = i18next.createInstance();
await i18n.init({
  lng: "en", resources, defaultNS: "invoices", interpolation: { escapeValue: false },
});
const item = {
  claim: "# Retrieved passage\nSupplier-caused restart charges are not billable. <script>example</script>",
  source_ref: "Capacity Reservation Agreement",
  source_uri: "https://example.invalid/synthetic-contract",
  ref_id: 0,
  provenance: "native-foundry-mcp",
};
const native = [{
  plane: "foundryiq",
  agent: "Native knowledge retrieval",
  provenance: "native-foundry-mcp",
  summary: "Retrieved passages, not an independent expert verdict.",
  evidence: [item],
}];
const legacy = [{
  plane: "foundryiq",
  agent: "contract-policy-expert",
  evidence: [{ claim: "Legacy citation", source_ref: item.source_ref }],
}];
const render = (name, fanout) => renderToStaticMarkup(React.createElement(
  I18nextProvider,
  { i18n },
  React.createElement(exportsObject[name], {
    fanout, verdict: "escalate", documents: [], records: [],
    basisSummary: null, onOpenDocument: () => {},
  }),
));

for (const language of ["en", "es"]) {
  await i18n.changeLanguage(language);
  const clauses = render("ClauseSection", native);
  assert(clauses.includes(i18n.t("clauses.retrieved", { count: 1 })));
  assert(!clauses.includes(i18n.t("clauses.cited", { count: 1 })));
  assert(clauses.includes("<details"));
  assert(clauses.includes("&lt;script&gt;example&lt;/script&gt;"));
  assert(!clauses.includes("<script>"));

  const evidence = render("EvidenceSection", native);
  assert(evidence.includes("FoundryIQ"));
  for (const plane of ["FabricIQ", "WorkIQ", "WebIQ"]) assert(!evidence.includes(plane));
  assert(evidence.includes(i18n.t("evidence.references", { count: 1 })));
  assert(evidence.includes(i18n.t("evidence.retrievedListed")));

  const oldClauses = render("ClauseSection", legacy);
  assert(oldClauses.includes(i18n.t("clauses.cited", { count: 1 })));
  assert(!oldClauses.includes("<details"));
  const oldEvidence = render("EvidenceSection", legacy);
  for (const plane of ["FoundryIQ", "FabricIQ", "WorkIQ", "WebIQ"]) {
    assert(oldEvidence.includes(plane));
  }
  assert(oldEvidence.includes(i18n.t("evidence.citations", { count: 1 })));

  const mixed = render("ClauseSection", [...native, ...legacy]);
  assert(mixed.includes(i18n.t("clauses.retrieved", { count: 1 })));
  assert(mixed.includes(i18n.t("clauses.cited", { count: 1 })));
  const empty = render("EvidenceSection", []);
  for (const plane of ["FoundryIQ", "FabricIQ", "WorkIQ", "WebIQ"]) {
    assert(!empty.includes(plane));
  }
  console.log(`Evidence rendering passed: ${language}, native, legacy, mixed and empty.`);
}
