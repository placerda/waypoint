# Private bounded invoice application

## Selected application and provenance

Upstream baseline: `caldova/waypoint@f98794cada07fe036ed67004e7ea8381c0aae870`.
Root `app-definition.json` is the native Agent Landing Zone v1 application
contract (`id: caldova-waypoint`); root `azure.yaml` selects only:

| Component | Source/build context | Runtime |
| --- | --- | --- |
| web | `apps/waypoint/web` | private-environment Container App, port 3000 |
| api | `apps/waypoint/api` | private-environment Container App, port 8000 |
| contract-agent | `modules/agents/contract-agent` | Foundry hosted agent, Responses 2.0.0 |

This modifies an existing azd configuration; it does not re-scaffold Aspire.
`apps/waypoint/apphost.cs` remains unchanged for upstream local development.
Do **not** run its provisioning path alongside the landing-zone foundation.
Other upstream agents, Fabric, M365, optimizer and fleet templates are retained
as source history, not selected or deployed.
The Web route table/navigation excludes the historical fleet map and optimizer
quality page. Invoice review, overview and persisted-run Activity remain.
Production Web startup rejects missing native MSAL configuration, plaintext
API URLs and credential-bearing endpoints instead of using local defaults.

The landing-zone owner must bind `AGENTLZ_APP_DEFINITION` to this root before
first provision. Each component's real `source.commit` pins its implementation;
build the exact pinned component tree, not uncommitted workspace files. All
Docker contexts are component-local `.` with a local `Dockerfile`, as required
by the native isolated ACR build lifecycle. Hosted resources/protocols are in
`azure.yaml`, not unsupported app-definition fields. There are no custom hooks
or arbitrary role assignments in the application contract.

The API alone opts into `secret-reader` for its PostgreSQL connection. All
other component profiles are the implicit configuration-read/image-pull base;
there is no direct `model-user` grant and no Cosmos conversation store.

## Private reachability

Both Container Apps use app ingress `external` **inside an internal-only ACA
environment**. This permits VPN clients and the hosted agent to reach the apps
through their private environment VIP. Per-app `internal` would mean
environment-only reachability and would block these callers. The foundation
must enforce `environment.internal=true`, private DNS, VPN routing and firewall
rules; these application entries do not authorize a public ACA environment.
Public/internet ingress is not an acceptable substitute.

## One canonical synthetic investigation

`tools/prepare_invoice_corpus.py` reproducibly selects the native source data:

- Invoice **INV-2026-08034**, supplier **sup-009 / BluePeak Biologics**.
- Invoice total **USD 985,000.00**: USD 111,000 supplier-caused contamination
  investigation plus USD 874,000 matched base services.
- Finding **finding-inv-2026-08034**, severity `high`, expected **escalate**,
  grounded money at risk **USD 111,000.00**.
- Contract `contract-sup-009-bluepeak-biologics-capacity-agreement`, section 4:
  BluePeak-process-error investigation, cleaning and restart charges are not
  billable to the sponsor.
- Policies `policy-quality-release-billability-policy` and
  `policy-invoice-dispute-and-recovery-procedure`.
- Evidence `ev-inv-2026-08034-01` and `ev-inv-2026-08034-02`.

The API image bundles the exact selected seed and three matching original
Markdown documents under `corpus/`. The full upstream corpus is unchanged.
Do not use the API's historical built-in seed: its similarly numbered invoice
has a different total and identifiers. `APP_DEFAULT_SEED_ENABLED=false` and the
explicit bundled seed path prevent this mixture. Native invoice PDF/HTML URIs
are provenance metadata; no generated PDF artifact is claimed by this subset.

The authenticated API serves bundled contract/policy text with
`include_content=true` and `content_source=local-corpus`. Paths are confined to
the bundled `data/` directory, with no arbitrary URL fetch or OneLake dependency.
This structured evidence path is not a substitute for the FoundryIQ acceptance
gate described below.

## Environment and identity interfaces

Supply actual deployment values outside git; the fork contains no tenant,
subscription, private endpoint IDs, DSNs, tokens or local evidence.
Native custom settings materialization requires the existing-vault secret URI
in `APP_DATABASE_CONNECTION_KEY_VAULT_URI`; never put the DSN itself into an
azd environment file. The selected API identity needs the secret-reader
capability on that same vault. Key-only nonsecret settings are required inputs,
not empty/default production endpoints.

| Runtime | Required configuration |
| --- | --- |
| Web | `API_ENDPOINT_HTTP` = private API HTTPS URL; `WAYPOINT_MSAL_ENABLED=true`; `WAYPOINT_MSAL_TENANT_ID`, `WAYPOINT_MSAL_CLIENT_ID`, `WAYPOINT_MSAL_API_SCOPE`, `WAYPOINT_MSAL_REDIRECT_URI` |
| API auth | `APP_MSAL_BEARER_VALIDATION_ENABLED=true`; `APP_MSAL_TENANT_ID`, `APP_MSAL_CLIENT_ID` = API token audience, `APP_MSAL_REQUIRED_SCOPE=user_impersonation`; `APP_MSAL_ALLOWED_APP_IDS` = allowed hosted-agent app/client IDs; `APP_CORS_ALLOWED_ORIGINS` = private web origin |
| API persistence | `APP_DATABASE_CONNECTION` = TLS PostgreSQL least-privilege application DSN, provided through a secret reference; `APP_REQUIRE_POSTGRES=true`; `APP_RUN_STARTUP_DATABASE_BOOTSTRAP=false` |
| API corpus | `APP_LEDGERFIELD_SEED_PATH=/app/corpus/data/waypoint/waypoint-seed.json`; `APP_CORPUS_LOCAL_ROOT=/app/corpus`; `APP_SEED_REQUIRED=true`; `APP_DEFAULT_SEED_ENABLED=false` |
| API agent invocation | `APP_FOUNDRY_ENDPOINT` = new agent project endpoint; `APP_FOUNDRY_ORCHESTRATOR_AGENT_NAME=contract-agent` |
| Agent project | `FOUNDRY_PROJECT_ENDPOINT` = new agent project `/api/projects/<project>` endpoint, with **no model deployments** |
| Agent model | `AZURE_AI_MODEL_DEPLOYMENT_NAME` = deployment in the separate model account; `WAYPOINT_GATEWAY_BASE_URL` = private-reachable APIM HTTPS `/openai/v1/`; `WAYPOINT_GATEWAY_SCOPE=https://cognitiveservices.azure.com/.default` |
| Agent tools | `WAYPOINT_API_BASE_URL` = new private API HTTPS URL; `WAYPOINT_API_SCOPE` = API application ID URI + `/.default` |
| FoundryIQ | `TOOLBOX_NAME=contract-toolbox`; `TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT` = new private project toolbox MCP URL; `WAYPOINT_KB_TOOL_NAME` = actual published KB retrieve tool name; `WAYPOINT_KB_MCP_ENDPOINT` = upstream native Search `/knowledgebases/<kb>/mcp?api-version=2026-04-01` URL; `WAYPOINT_FOUNDRYIQ_RETRIEVAL_MODE=minimal-extractive`; `WAYPOINT_FOUNDRYIQ_GATEWAY_VERIFIED=false` until accepted |
| Telemetry | Foundation-managed OTel/Application Insights settings; content recording is not enabled by this adaptation |

The native API supports delegated-user tokens and explicit managed-identity
application roles. Assign the hosted agent `Waypoint.Write` (which includes read
access), not admin; constrain its app ID. Grant the API identity only the native
project/hosted-agent invocation access it needs. Model-account access belongs to
APIM's backend identity, **not** the hosted-agent identity. Native foundation/
Entra owners configure those assignments; the app contract grants none of them
implicitly. Browser auth has no local/API-key fallback in the deployed settings.
The agent requires managed bearer auth, HTTPS and certificate validation for API
tools, regardless of historical insecure/key settings.

Before the API starts, the database owner must run its existing idempotent
`python -m app.bootstrap` command with the least-privilege application DSN and
the separately supplied privileged `APP_DATABASE_BOOTSTRAP_CONNECTION`. Do not
give that privileged connection to the request-serving process. A missing DSN
or required seed fails startup instead of reporting success with memory/empty
data. Region, database ownership, network and bootstrap execution remain the
infrastructure owner's responsibility.

### Small PostgreSQL footprint

The actual repository schema uses JSONB, stored generated columns and ordinary
primary/partial-unique indexes. It contains no vector columns, `CREATE
EXTENSION`, external analytics engine or database-side UUID extension; Python
generates record IDs. Stored generated columns require PostgreSQL 12 or later;
there is no application requirement for a specific newer major. Use a currently
supported platform major and validate the chosen service version before release.

For the one-invoice recording workload there is no code-driven requirement
above the available B1ms candidate (1 vCPU / 2 GiB) and 32 GiB storage. This is
not a measured production capacity guarantee. The API explicitly runs one
Uvicorn worker, with `APP_DATABASE_POOL_MIN_SIZE=1` and
`APP_DATABASE_POOL_MAX_SIZE=4` (validated upper bound four per process).
Configure maximum one API replica in the foundation to cap runtime pooling at
four sessions; bootstrap/admin connections are separate and short-lived. The
background run reaper shares this same pool. Fabric mirroring/replication is
not enabled and does not justify a larger database SKU.

## Gateway adapter and compatibility gates

Castia 0.7.7 constructs `AIProjectClient` and calls
`get_openai_client().responses`; an arbitrary APIM URL is **not** a project
endpoint. `gateway.py` retains a typed project client but overrides only its
OpenAI model transport. `GatewayModel` initializes the pinned Castia state and
inherits its existing `respond`, `stream`, function-tool loop and tool execution.
Updating Castia requires rerunning its seam tests and reviewing these private
initialization fields. No model request uses the agent project's local models.
The pinned Castia loop echoes only function calls/results and would otherwise
lose preceding MCP grounding and reasoning items on continuation. The isolated
Responses transport restores the complete preceding output, removes duplicate
function-call echoes, and requests encrypted reasoning for subsequent turns.
This state is task-local, not shared between concurrent investigations.
The inherited tool loop remains intact and is explicitly bounded to eight
iterations so the agent can perform focused follow-up searches.

The transport obtains a refreshable managed bearer, injects current W3C trace
context, preserves native Responses output/usage and streaming, does not follow
redirects, and never retries against a direct Foundry endpoint. HTTP failures
remain failures. The same trace context is propagated API → hosted agent and
agent → Waypoint API. APIM must preserve it toward the separate model backend.

### Approved model-free FoundryIQ capability

The original KB-side LLM-through-APIM route is **officially unsupported**, not
merely untested. The approved alternative is a native **minimal/extractive**
FoundryIQ knowledge base using GA Search API `2026-04-01`, with no KB `models`,
query-planning LLM or answer-synthesis deployment. Do not deploy a synthesis
model or grant Search a direct model-account role for KB reasoning.
The single Castia agent performs analysis and synthesis through APIM only.

This preserves real knowledge sources, semantic ranking, supported
semantic/vector retrieval, source attribution and native MCP. It does **not**
provide automatic KB-side question decomposition, iterative planning or a
ready-written KB answer. The agent must issue focused searches and follow-up
queries itself; difficult or vague questions can require more agent turns.
If vectorization is configured, its embeddings still use the approved gateway;
model-free KB reasoning does not mean model-free embedding generation.
The initial bounded three-document recording selects **semantic-only**
retrieval, with no populated vectors or embedding calls. The native foundation
ingests the original Markdown as deterministic source-attributed text chunks;
no separate local retrieval engine is introduced. Vectorization remains a
future explicitly configured capability, not a feature claimed by this build.

Native GA REST retrieval accepts only the following input shape for a basic
semantic request (the typed `knowledge.minimal_retrieve_request` helper is
available for acceptance probes, not used as a replacement runtime retriever):

```json
{"intents": [{"type": "semantic", "search": "BluePeak supplier process error charges"}]}
```

Do not send preview `messages`, `outputMode` or `retrievalReasoningEffort`
fields to that GA endpoint. MCP has a **separate server-discovered input
schema**; this application deliberately removes the old guessed `query`
parameter guidance and leaves the published tool schema untouched. Inspect
the actual GA `tools/list` schema during acceptance. The remote MCP result has
text content containing JSON grounding records (`ref_id`, `title`, content and
configured source fields), not the REST `references`/`activity` envelope.
Keep the returned source fields and cite their actual passages; GA does not
promise the preview-only authenticated `citationUrl` feature.

FoundryIQ remains a **server-side MCP** spec, not a local replacement search.
The configured native Search URL is a routing assertion: compare it to the
actual project toolbox connection; merely setting it cannot verify that route.
The project toolbox token uses the Foundry AI audience; a direct native Search
MCP client would need the Search audience instead. These tokens are not
interchangeable, and this adaptation does not silently switch transports.

Passing the MCP spec through APIM in a mocked test proves client serialization
and continuation preservation, not remote-service compatibility. Before setting
`WAYPOINT_FOUNDRYIQ_GATEWAY_VERIFIED=true`, independently verify:

1. A real selected-model tool loop through APIM, including its response/reasoning
   continuation behavior, bearer audience and private network reachability.
2. The model service can invoke the private project toolbox MCP endpoint with
   the FoundryIQ authorization token and actual published KB tool name.
3. The actual toolbox connection targets the asserted GA MCP endpoint, its
   discovered tool schema succeeds, and the KB definition has no models.
   There are no KB query-planning/synthesis calls. Any configured embedding
   calls route through APIM to the separate model account. Inspect real
   gateway/backend traces; no agent-account deployment or direct-model bypass.
4. Retrieved citations contain the selected section-4 and policy passages.
5. A real app-triggered investigation persists case/run/recommendation IDs in
   PostgreSQL, survives API restart, and escalates USD 111,000 without financial
   execution.

If native GA MCP/toolbox compatibility or service-side private networking does
not support that routing, report the exact incompatibility and keep this gate
closed. A plain Responses call does not establish FoundryIQ compatibility.
The application raises an explicit error rather than silently dropping the KB
lane or bypassing APIM.

Official compatibility references:

- [APIM integration support and explicit KB LLM limitation](https://learn.microsoft.com/azure/search/search-how-to-configure-azure-openai-api-management)
- [Native knowledge base API versions and optional models](https://learn.microsoft.com/azure/search/agentic-retrieval-how-to-create-knowledge-base)
- [GA intents, native MCP behavior and grounding shape](https://learn.microsoft.com/azure/search/agentic-retrieval-how-to-retrieve)

## Local validation commands

```powershell
python tools\prepare_invoice_corpus.py
python -m unittest discover -s tests -v
Set-Location modules\agents\contract-agent
uv run --frozen python -m unittest discover -s tests -v
Set-Location ..\..\..\apps\waypoint\api
uv run --frozen pytest tests\test_private_application.py tests\test_app_role_auth.py tests\test_user_auth.py tests\test_assurance_runs.py tests\test_onelake_corpus.py -q
Set-Location ..\web
npm run typecheck
node --test tests\private-config.test.mjs
npm run i18n:check
npm run build
```

Also run the pinned foundation's `python -m config.appdefinition --validate
<fork-root>`. Offline tests do not establish Azure deployment, live PostgreSQL
durability, private DNS/VPN access, model/KB routing or recording readiness.
