"""Reproduce the bounded application corpus from the unmodified synthetic source."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "modules" / "corpus"
TARGET = ROOT / "apps" / "waypoint" / "api" / "corpus"
INVOICE = "INV-2026-08034"


def main() -> None:
    source = json.loads((SOURCE / "data/waypoint/waypoint-seed.json").read_text(encoding="utf-8"))
    invoice = next(i for i in source["invoices"] if i["id"] == INVOICE)
    findings = [f for f in source["findings"] if f["invoice_id"] == INVOICE]
    contract_ids = {v for f in findings for v in f["contract_document_ids"]}
    policy_ids = {v for f in findings for v in f["policy_ids"]}
    seed = {
        "schema_version": source["schema_version"],
        "suppliers": [s for s in source["suppliers"] if s["id"] == invoice["supplier_id"]],
        "scenarios": [s for s in source["scenarios"] if s["id"] == invoice["scenario_id"]],
        "invoices": [invoice],
        "findings": findings,
        "evidence": [e for e in source["evidence"] if e["invoice_id"] == INVOICE],
        "contract_documents": [c for c in source["contract_documents"] if c["id"] in contract_ids],
        "policies": [p for p in source["policies"] if p["id"] in policy_ids],
    }
    seed_path = TARGET / "data/waypoint/waypoint-seed.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", TARGET / "LICENSE")
    seed_path.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    for uri in [
        *(c["uri"] for c in seed["contract_documents"]),
        *(p["metadata"]["uri"] for p in seed["policies"]),
    ]:
        destination = TARGET / uri
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / uri, destination)


if __name__ == "__main__":
    main()
