import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from azure.core.credentials import AccessToken

from app.common import database, postgres_entra
from app.common.postgres_entra import (
    POSTGRES_SCOPE,
    EntraPostgresPassword,
    bootstrap_entra_database,
    validate_entra_connection,
)
from app.common.repository import PostgresWaypointRepository
from app.common.settings import Settings

APP_DSN = (
    "postgresql://waypoint_runtime@private.postgres.database.azure.com/waypoint"
    "?sslmode=verify-full&sslrootcert=system"
)
BOOTSTRAP_DSN = APP_DSN.replace("waypoint_runtime@", "deploy_admin@").replace(
    "/waypoint?", "/postgres?"
)
PRINCIPAL = "11111111-2222-4333-8444-555555555555"


class Credential:
    def __init__(self):
        self.scopes = []
        self.closed = 0
        self.threads = []

    def get_token(self, scope, **kwargs):
        self.scopes.append(scope)
        self.threads.append(threading.get_ident())
        return AccessToken(f"test-token-{len(self.scopes)}", int(time.time()) + 3600)

    def close(self):
        self.closed += 1


async def test_native_pool_gets_refreshable_password_for_every_physical_connection(monkeypatch):
    credential = Credential()
    factory = Mock(return_value=credential)
    monkeypatch.setattr(postgres_entra, "ManagedIdentityCredential", factory)
    repository = PostgresWaypointRepository(
        APP_DSN, entra_enabled=True, entra_client_id="api-uai-client-id"
    )
    connect = AsyncMock(side_effect=lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr("psycopg.AsyncConnection.connect", connect)
    try:
        # Exercise the installed 3.3.1 pool's actual new/replacement connection path.
        await repository._pool._connect()
        await repository._pool._connect()
        first, replacement = connect.call_args_list
        assert first.args == replacement.args == (APP_DSN,)
        assert first.kwargs["password"] == "test-token-1"
        assert replacement.kwargs["password"] == "test-token-2"
        assert credential.scopes == [POSTGRES_SCOPE, POSTGRES_SCOPE]
        assert threading.get_ident() not in credential.threads
        assert "test-token" not in repository.connection_string
    finally:
        await repository.close()
        await repository.close()
    assert credential.closed == 1
    factory.assert_called_once_with(client_id="api-uai-client-id")


async def test_connection_callback_observes_sdk_token_refresh_after_expiration():
    class ExpiringCredential(Credential):
        now = 0

        def get_token(self, scope, **kwargs):
            self.scopes.append(scope)
            return (
                AccessToken("test-before-expiry", 10)
                if self.now < 10 else AccessToken("test-after-refresh", 100)
            )

    credential = ExpiringCredential()
    provider = EntraPostgresPassword(credential=credential)
    try:
        assert (await provider.kwargs())["password"] == "test-before-expiry"
        credential.now = 11
        assert (await provider.kwargs())["password"] == "test-after-refresh"
        assert credential.scopes == [POSTGRES_SCOPE, POSTGRES_SCOPE]
    finally:
        await provider.close()


async def test_credential_closes_even_if_pool_close_fails(monkeypatch):
    credential = Credential()
    monkeypatch.setattr(postgres_entra, "ManagedIdentityCredential", lambda **kw: credential)
    repository = PostgresWaypointRepository(
        APP_DSN, entra_enabled=True, entra_client_id="api-uai-client-id"
    )
    monkeypatch.setattr(repository._pool, "close", AsyncMock(side_effect=RuntimeError("close")))
    with pytest.raises(RuntimeError, match="close"):
        await repository.close()
    assert credential.closed == 1


async def test_entra_runtime_initializes_without_schema_ddl(monkeypatch):
    repository = PostgresWaypointRepository(
        APP_DSN, entra_enabled=True, entra_client_id="api-uai-client-id"
    )
    monkeypatch.setattr(repository._pool, "open", AsyncMock())
    connection = AsyncMock(side_effect=AssertionError("Runtime must not execute schema DDL"))
    monkeypatch.setattr(repository._pool, "connection", connection)
    upsert = AsyncMock()
    monkeypatch.setattr(repository, "_upsert_payload", upsert)
    try:
        await repository.initialize(load_default_seed=False)
        connection.assert_not_called()
        assert upsert.await_count > 0
    finally:
        await repository.close()


@pytest.mark.parametrize("dsn", [
    APP_DSN.replace("waypoint_runtime@", "waypoint_runtime:static-token@"),
    APP_DSN.replace("verify-full", "require"),
    APP_DSN.replace("&sslrootcert=system", ""),
    APP_DSN + "&passfile=credentials",
    APP_DSN + "&service=other-server",
])
def test_entra_dsn_rejects_static_credentials_or_weakened_tls(dsn):
    with pytest.raises(ValueError):
        validate_entra_connection(dsn)


async def test_token_acquisition_failure_has_no_password_fallback():
    credential = Credential()
    credential.get_token = Mock(side_effect=RuntimeError("identity unavailable"))
    provider = EntraPostgresPassword(credential=credential)
    try:
        with pytest.raises(RuntimeError, match="identity unavailable"):
            await provider.kwargs()
    finally:
        await provider.close()


class SQLConnection:
    def __init__(self, *, principals=(), ownership=False):
        self.statements = []
        self.principals = list(principals)
        self.ownership = ownership

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string()
        self.statements.append((text, params))
        cursor = SimpleNamespace(
            fetchall=AsyncMock(return_value=self.principals),
            fetchone=AsyncMock(return_value=(
                {"exists": 1} if self.ownership and "select 1 where" in text else None
            )),
        )
        return cursor


def bootstrap_settings(**overrides):
    values = {
        "database_entra_enabled": True,
        "database_connection": APP_DSN,
        "database_bootstrap_connection": BOOTSTRAP_DSN,
        "database_entra_principal_id": PRINCIPAL,
    }
    return Settings(**(values | overrides))


async def test_bootstrap_maps_object_id_owns_schema_and_grants_runtime_dml_only(monkeypatch):
    credential = Credential()
    monkeypatch.setattr(postgres_entra, "DefaultAzureCredential", lambda **kw: credential)
    admin, schema = SQLConnection(), SQLConnection()
    connect = AsyncMock(side_effect=[admin, schema])
    monkeypatch.setattr("psycopg.AsyncConnection.connect", connect)
    await bootstrap_entra_database(bootstrap_settings())
    sql = "\n".join(query for connection in (admin, schema)
                    for query, params in connection.statements)
    assert "pgaadauth_create_principal_with_oid" in sql
    assert any(params == ("waypoint_runtime", PRINCIPAL) for _, params in admin.statements)
    assert "create table if not exists suppliers" in sql
    assert 'grant connect on database "waypoint" to "waypoint_runtime"' in sql
    assert 'grant usage on schema public to "waypoint_runtime"' in sql
    assert "grant select, insert, update, delete on all tables" in sql
    assert "alter default privileges" in sql
    assert "grant usage, create" not in sql
    assert "grant connect, create" not in sql
    assert "login password" not in sql
    assert [call.kwargs["password"] for call in connect.call_args_list] == [
        "test-token-1", "test-token-2",
    ]
    assert credential.closed == 1


@pytest.mark.parametrize("principal", [
    {"rolename": "waypoint_runtime", "objectId": "different-object", "isAdmin": False},
    {"rolename": "waypoint_runtime", "objectId": PRINCIPAL, "isAdmin": True},
])
async def test_bootstrap_refuses_wrong_or_admin_existing_role(monkeypatch, principal):
    credential = Credential()
    monkeypatch.setattr(postgres_entra, "DefaultAzureCredential", lambda **kw: credential)
    admin = SQLConnection(principals=[principal])
    monkeypatch.setattr("psycopg.AsyncConnection.connect", AsyncMock(return_value=admin))
    with pytest.raises(ValueError, match="different Entra mapping or is admin"):
        await bootstrap_entra_database(bootstrap_settings())
    assert credential.closed == 1


async def test_bootstrap_refuses_runtime_owned_database_schema_or_tables(monkeypatch):
    credential = Credential()
    monkeypatch.setattr(postgres_entra, "DefaultAzureCredential", lambda **kw: credential)
    monkeypatch.setattr(
        "psycopg.AsyncConnection.connect",
        AsyncMock(side_effect=[SQLConnection(), SQLConnection(ownership=True)]),
    )
    with pytest.raises(ValueError, match="must not own"):
        await bootstrap_entra_database(bootstrap_settings())
    assert credential.closed == 1


@pytest.mark.parametrize(("key", "value"), [
    ("database_bootstrap_connection", BOOTSTRAP_DSN),
    ("database_entra_principal_id", PRINCIPAL),
    ("database_bootstrap_client_id", "bootstrap-uai-client-id"),
])
async def test_runtime_rejects_privileged_bootstrap_settings(key, value):
    database.reset_waypoint_repository_for_tests()
    try:
        with pytest.raises(ValueError, match="must not be supplied"):
            await database.get_waypoint_repository_for_settings(Settings(
                database_connection=APP_DSN, database_entra_enabled=True,
                **{key: value},
            ))
    finally:
        database.reset_waypoint_repository_for_tests()


async def test_runtime_initialization_failure_closes_pool_and_credential(monkeypatch):
    repository = SimpleNamespace(
        initialize=AsyncMock(side_effect=RuntimeError("schema not bootstrapped")),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "app.common.repository.PostgresWaypointRepository",
        lambda *args, **kwargs: repository,
    )
    database.reset_waypoint_repository_for_tests()
    try:
        with pytest.raises(RuntimeError, match="schema not bootstrapped"):
            await database.get_waypoint_repository_for_settings(Settings(
                database_connection=APP_DSN,
                database_entra_enabled=True,
                database_entra_client_id="api-uai-client-id",
            ))
        repository.close.assert_awaited_once()
    finally:
        database.reset_waypoint_repository_for_tests()
