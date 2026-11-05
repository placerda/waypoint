from pathlib import Path

import pytest

from app.common.database import (
    get_waypoint_repository_for_settings,
    reset_waypoint_repository_for_tests,
)
from app.common.onelake import OneLakeClient
from app.common.settings import Settings
from app.modules.records.service import WaypointService

CORPUS = Path(__file__).resolve().parents[1] / "corpus"

def test_recording_pool_is_bounded():
    assert Settings().database_pool_max_size == 4
    with pytest.raises(ValueError):
        Settings(database_pool_max_size=5)


async def test_private_runtime_never_falls_back_to_memory():
    with pytest.raises(ValueError, match="in-memory fallback is disabled"):
        await get_waypoint_repository_for_settings(Settings(require_postgres=True))


async def test_selected_seed_errors_are_not_reported_as_success():
    reset_waypoint_repository_for_tests()
    with pytest.raises(FileNotFoundError):
        await get_waypoint_repository_for_settings(
            Settings(seed_required=True, ledgerfield_seed_path=str(CORPUS / "missing.json"))
        )
    reset_waypoint_repository_for_tests()


async def test_selected_native_invoice_has_matching_findings_and_document_text():
    reset_waypoint_repository_for_tests()
    settings = Settings(
        ledgerfield_seed_path=str(CORPUS / "data/waypoint/waypoint-seed.json"),
        corpus_local_root=str(CORPUS),
        default_seed_enabled=False,
    )
    repository = await get_waypoint_repository_for_settings(settings)
    try:
        service = WaypointService(repository, OneLakeClient(settings))
        invoices = await service.list_invoices()
        assert [i.id for i in invoices] == ["INV-2026-08034"]
        invoice = await service.get_invoice_detail(invoices[0].id)
        assert str(invoice.total_amount) == "985000.00"
        finding = invoice.findings[0]
        assert finding.status == "escalate"
        assert str(finding.overpayment_amount) == "111000.00"
        contract = await service.get_contract_document_detail(
            finding.contract_document_ids[0], include_content=True
        )
        assert contract.content_source == "local-corpus"
        assert "not billable to the sponsor" in contract.text
        for policy_id in finding.policy_ids:
            policy = await service.get_policy_detail(policy_id, include_content=True)
            assert policy.text
            assert policy.content_source == "local-corpus"
        assert await get_waypoint_repository_for_settings(settings) is repository
    finally:
        await repository.close()
        reset_waypoint_repository_for_tests()


@pytest.mark.parametrize("uri", [
    "../pyproject.toml", "data/../../pyproject.toml", "/etc/passwd",
    "https://production.example.org/contract.md", "data\\..\\..\\pyproject.toml",
])
def test_local_corpus_does_not_read_outside_bundled_data(uri):
    client = OneLakeClient(Settings(corpus_local_root=str(CORPUS)))
    assert client.resolve_document(uri, "contract").text is None


def test_local_corpus_cache_includes_root():
    from app.common.onelake import get_onelake_client

    first = get_onelake_client(Settings(corpus_local_root=str(CORPUS)))
    second = get_onelake_client(Settings(corpus_local_root=str(CORPUS / "different")))
    assert first is not second
