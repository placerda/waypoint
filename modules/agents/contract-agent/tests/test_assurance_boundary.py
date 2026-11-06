from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from tools import _gather_evidence_impl, _WaypointClient
from write_tools import _record_assurance_impl, _WaypointWriter, write_tools

ROOT = Path(__file__).resolve().parents[4]
SEED = ROOT / "apps/waypoint/api/corpus/data/waypoint/waypoint-seed.json"
ENV = {
    "WAYPOINT_API_BASE_URL": "https://private-api.example.org",
    "WAYPOINT_API_SCOPE": "api://waypoint/.default",
}


class InvoiceEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def gather(self, invoice):
        seed = json.loads(SEED.read_text(encoding="utf-8"))
        with patch.dict(os.environ, ENV), \
                patch.object(_WaypointClient, "resolve_invoice", AsyncMock(return_value=invoice)), \
                patch.object(_WaypointClient, "list_findings", AsyncMock(return_value=seed["findings"])), \
                patch.object(_WaypointClient, "list_evidence", AsyncMock(return_value=seed["evidence"])), \
                patch.object(_WaypointClient, "get_contract_document", AsyncMock(return_value={"title": "Agreement"})), \
                patch.object(_WaypointClient, "get_policy", AsyncMock(return_value={"name": "Policy"})):
            return await _gather_evidence_impl(None, invoice_id="INV-2026-08034")

    async def test_invoice_money_lines_and_operational_evidence_reach_the_model(self):
        invoice = json.loads(SEED.read_text(encoding="utf-8"))["invoices"][0]
        result = await self.gather(invoice)
        self.assertEqual(result["invoice"]["total_amount"], "985000.00")
        self.assertEqual(result["invoice"]["currency"], "USD")
        self.assertEqual(result["invoice"]["lines"][0]["amount"], "111000.00")
        self.assertEqual(result["invoice"]["lines"][1]["amount"], "874000.00")
        self.assertIn("supplier-caused", result["findings"][0]["summary"])
        self.assertIn("supplier-caused", result["evidence_references"][0]["excerpt"])
        self.assertEqual(result["evidence_references"][0]["id"], "ev-inv-2026-08034-01")
        self.assertNotIn("metadata", json.dumps(result))
        self.assertNotIn("html_uri", result["invoice"])

    async def test_missing_financial_fields_do_not_become_zero_or_usd(self):
        result = await self.gather({"id": "INV-2026-08034"})
        self.assertIsNone(result["invoice"]["total_amount"])
        self.assertIsNone(result["invoice"]["currency"])
        self.assertEqual(result["invoice"]["lines"], [])


class AssuranceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_finding_overrides_model_and_only_records_assurance(self):
        seed = json.loads(SEED.read_text(encoding="utf-8"))
        writes: list[tuple[str, str, dict]] = []

        async def read(client, path, params=None):
            if path == "/api/invoices/INV-2026-08034":
                return seed["invoices"][0]
            if path == "/api/findings":
                return seed["findings"]
            if path == "/api/evidence":
                return seed["evidence"]
            raise AssertionError(f"Unexpected read {path}")

        async def send(client, method, path, body):
            writes.append((method, path, body))
            if path == "/api/cases":
                return {"id": "case-1"}
            if path == "/api/runs":
                return {"id": "run-1", **body}
            if path == "/api/cases/case-1/recommendations":
                return {"id": "rec-1", **body}
            if path == "/api/runs/run-1":
                return {"id": "run-1", **body}
            raise AssertionError(f"Unexpected mutation {method} {path}")

        with patch.dict(os.environ, ENV), patch.object(_WaypointClient, "_get", read), \
                patch.object(_WaypointWriter, "_send", send):
            result = await _record_assurance_impl(None, result_json=json.dumps({
                "invoice_id": "INV-2026-08034", "decision": "approve",
                "money_at_risk": 1, "reasoning": "Supplier-caused deviation, contract section 4.",
                "confidence": 0.9, "summary": "Escalate contamination investigation.",
            }))
        self.assertTrue(result["ok"])
        self.assertEqual(result["decision"], "escalate")
        self.assertTrue(result["governance"]["overridden"])
        self.assertEqual(result["correlation"]["money_at_risk"], 111000)
        self.assertTrue(result["correlation"]["run_finalized"])
        recommendation = next(b for _, p, b in writes if p.endswith("/recommendations"))
        self.assertEqual(recommendation["decision"], "escalate")
        self.assertEqual(float(recommendation["money_at_risk"]), 111000)
        self.assertEqual(set(recommendation["evidence_ids"]),
                         {"ev-inv-2026-08034-01", "ev-inv-2026-08034-02"})
        self.assertEqual([t.name for t in write_tools()], ["record_assurance"])
        self.assertEqual(len(writes), 4)

    async def test_native_api_rejecting_completion_is_not_success(self):
        async def denied(request):
            return httpx.Response(404, request=request, json={"detail": "Not found"})

        with patch.dict(os.environ, ENV):
            writer = _WaypointWriter()
            async def headers():
                return {"Authorization": "Bearer test"}
            writer._headers = headers
            client = httpx.AsyncClient(transport=httpx.MockTransport(denied))
            with patch("write_tools.httpx.AsyncClient", return_value=client):
                with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                    await writer.update_run("run-1", status="completed", summary="", metadata={})

    def test_api_auth_and_tls_never_fall_back(self):
        with patch.dict(os.environ, {**ENV, "WAYPOINT_API_VERIFY_SSL": "false",
                                     "WAYPOINT_API_KEY": "ignored"}):
            self.assertTrue(_WaypointClient()._verify)
        with patch.dict(os.environ, {**ENV, "WAYPOINT_API_SCOPE": ""}):
            with self.assertRaises(ValueError):
                _WaypointClient()


if __name__ == "__main__":
    unittest.main()
