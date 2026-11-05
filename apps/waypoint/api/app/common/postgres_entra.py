"""Refresh Entra credentials per physical PostgreSQL connection, never in the DSN."""

import asyncio
from typing import Any, Protocol
from uuid import UUID

import psycopg
from azure.core.credentials import AccessToken
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from .settings import Settings

POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


class PostgresCredential(Protocol):
    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken: ...

    def close(self) -> None: ...


def validate_entra_connection(conninfo: str) -> dict[str, str]:
    info = conninfo_to_dict(conninfo)
    if any(not info.get(key) for key in ("host", "dbname", "user")):
        raise ValueError("Entra PostgreSQL DSN requires explicit host, dbname and user")
    if any(key in info for key in ("password", "passfile", "service", "hostaddr", "sslpassword")):
        raise ValueError("Entra PostgreSQL DSN must not contain credentials or service overrides")
    if info.get("sslmode") != "verify-full":
        raise ValueError("Entra PostgreSQL requires sslmode=verify-full")
    if not info.get("sslrootcert"):
        raise ValueError("Entra PostgreSQL requires sslrootcert=system or an explicit CA file")
    return {key: str(value) for key, value in info.items() if value is not None}


class EntraPostgresPassword:
    def __init__(
        self,
        client_id: str = "",
        *,
        bootstrap: bool = False,
        credential: PostgresCredential | None = None,
    ) -> None:
        self._credential = credential or (
            DefaultAzureCredential(managed_identity_client_id=client_id or None)
            if bootstrap else ManagedIdentityCredential(client_id=client_id or None)
        )
        self._closed = False

    async def kwargs(self) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("PostgreSQL token provider is closed")
        # Azure Identity manages its refresh cache; no startup token is retained here.
        # Reuse the installed synchronous identity transport without blocking
        # the event loop or requiring an uninstalled optional aiohttp transport.
        token = await asyncio.to_thread(self._credential.get_token, POSTGRES_SCOPE)
        return {
            "password": token.token, "autocommit": True, "row_factory": dict_row,
            "options": "-c search_path=public",
        }

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await asyncio.to_thread(self._credential.close)


async def bootstrap_entra_database(settings: Settings) -> None:
    """Deploy identity owns DDL; the mapped API identity receives DML only."""
    from .repository import _SCHEMA_SQL, _normalize_postgres_connection_string

    if settings.fabric_mirror_enabled:
        raise ValueError("Entra-only bounded bootstrap does not enable Fabric mirroring")
    principal_id = str(UUID(settings.database_entra_principal_id))
    app_info = validate_entra_connection(
        _normalize_postgres_connection_string(settings.database_connection)
    )
    bootstrap_dsn = _normalize_postgres_connection_string(settings.database_bootstrap_connection)
    bootstrap_info = validate_entra_connection(bootstrap_dsn)
    if bootstrap_info["dbname"] != "postgres":
        raise ValueError("Entra bootstrap DSN must target postgres for pgaadauth role mapping")
    if bootstrap_info["user"] == app_info["user"]:
        raise ValueError("Bootstrap and runtime PostgreSQL roles must be distinct")
    if (
        bootstrap_info["host"] != app_info["host"]
        or bootstrap_info.get("port", "5432") != app_info.get("port", "5432")
    ):
        raise ValueError("Bootstrap and runtime DSNs must target the same PostgreSQL server")
    database_name, application_user = app_info["dbname"], app_info["user"]
    provider = EntraPostgresPassword(settings.database_bootstrap_client_id, bootstrap=True)
    try:
        async with await psycopg.AsyncConnection.connect(
            bootstrap_dsn, **await provider.kwargs()
        ) as connection:
            cursor = await connection.execute("select * from pgaadauth_list_principals(false)")
            principals = [
                {key.lower(): value for key, value in row.items()}
                for row in await cursor.fetchall()
            ]
            existing = next(
                (row for row in principals if row.get("rolename") == application_user), None
            )
            if existing is not None:
                if (
                    str(existing.get("objectid", "")).lower() != principal_id
                    or existing.get("isadmin")
                ):
                    raise ValueError(
                        "Existing runtime role has a different Entra mapping or is admin"
                    )
            else:
                cursor = await connection.execute(
                    "select 1 from pg_roles where rolname = %s", (application_user,)
                )
                if await cursor.fetchone():
                    raise ValueError("Runtime role exists without the required Entra mapping")
                await connection.execute(
                    "select * from pgaadauth_create_principal_with_oid"
                    "(%s, %s, 'service', false, false)",
                    (application_user, principal_id),
                )
            cursor = await connection.execute(
                """select 1 from pg_roles where rolname = %s
                and (rolsuper or rolcreatedb or rolcreaterole or rolreplication or rolbypassrls
                     or pg_has_role(rolname, 'azure_pg_admin', 'member'))""",
                (application_user,),
            )
            if await cursor.fetchone():
                raise ValueError("Runtime Entra role must not have administrative privileges")
            cursor = await connection.execute(
                "select 1 from pg_database where datname = %s", (database_name,)
            )
            if not await cursor.fetchone():
                await connection.execute(
                    sql.SQL("create database {}").format(sql.Identifier(database_name))
                )
            await connection.execute(
                sql.SQL("grant connect on database {} to {}").format(
                    sql.Identifier(database_name), sql.Identifier(application_user)
                )
            )
            await connection.execute(
                sql.SQL("revoke create on database {} from {}").format(
                    sql.Identifier(database_name), sql.Identifier(application_user)
                )
            )
            await connection.execute(
                sql.SQL("revoke create, temporary on database {} from public").format(
                    sql.Identifier(database_name)
                )
            )

        async with await psycopg.AsyncConnection.connect(
            make_conninfo(bootstrap_dsn, dbname=database_name), **await provider.kwargs()
        ) as connection:
            cursor = await connection.execute(
                """select 1 where
                exists (select 1 from pg_database where datname = current_database()
                        and datdba = (select oid from pg_roles where rolname = %s))
                or exists (select 1 from pg_namespace where nspname = 'public'
                           and nspowner = (select oid from pg_roles where rolname = %s))
                or exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
                           where n.nspname = 'public'
                           and c.relowner = (select oid from pg_roles where rolname = %s))""",
                (application_user,) * 3,
            )
            if await cursor.fetchone():
                raise ValueError("Runtime identity must not own the database, schema or tables")
            await connection.execute("revoke create on schema public from public")
            await connection.execute(_SCHEMA_SQL)
            role = sql.Identifier(application_user)
            for statement in (
                "revoke create on schema public from {}",
                "grant usage on schema public to {}",
                "grant select, insert, update, delete on all tables in schema public to {}",
                "alter default privileges in schema public "
                "grant select, insert, update, delete on tables to {}",
            ):
                await connection.execute(sql.SQL(statement).format(role))
    finally:
        await provider.close()
