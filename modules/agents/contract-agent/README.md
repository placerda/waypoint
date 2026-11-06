# contract-agent

The bounded invoice-investigation agent uses **Castia 0.7.7**, not MAF.
Only the Foundry `responses` protocol is exposed. Structured evidence comes
from the private Waypoint API; the only mutating tool is `record_assurance`.
It creates cases, runs, recommendations and optional drafts, never payments.

The agent executes native Foundry toolbox MCP inside its private network.
It discovers the real tool schema and offers it to Castia as a function tool.
Calls execute through the same toolbox's `tools/call`, preserving grounding
and references, including reference ID zero. Model inference and synthesis
still use APIM only. The model service does not connect to the private toolbox.
This follows the official BYO raw-MCP hosted-agent integration pattern, not a
local search engine or a replacement REST retriever.

`gateway.py` isolates the pinned Castia model initialization seam. The inherited
tool loop, plain Responses calls and streaming all use its managed-auth APIM
transport. `FOUNDRY_PROJECT_ENDPOINT` remains a real **agent project endpoint**;
`WAYPOINT_GATEWAY_BASE_URL` is a distinct APIM `/openai/v1/` endpoint. There is
no direct-model fallback. Native Responses usage and MCP specs are preserved.

No optimizer config resolver is used: instructions are loaded explicitly from
`.agent_configs/baseline/instructions.md`, and the selected model deployment
must be supplied through the environment. Historical sibling agents, optimizer
datasets and corpus remain in the fork but are not selected by `azure.yaml`.

Copy `.env.example` to an ignored local `.env`, supply the environment
interfaces in [the application guide](../../../docs/private-invoice-application.md),
then run:

```powershell
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python main.py
```

The FoundryIQ lane uses GA `2026-04-01` for knowledge sources and basic
intent-based REST retrieval. Explicit model-free minimal/extractive KB
configuration and the separate MCP wire contract use `2026-08-01-preview`.
The private service
rejects GA MCP discovery with HTTP 400. Both the project connection target and
the toolbox server URL must use the supported MCP version. This is a preview
dependency, not an entirely GA lane. There are no KB models, query planning
or answer synthesis. Castia performs reasoning
and synthesis through APIM. MCP inputs follow the server-discovered schema,
not an assumed REST `messages` payload. Source grounding records and citations
remain available; automatic KB-side decomposition is intentionally absent.

The lane fails closed until actual private native MCP retrieval, published
tool schema, citations and model-free configuration are verified. The native
upstream MCP URL is an explicit routing assertion, not proof that the project
toolbox connection matches it. Inspect that connection during acceptance.
A plain model-call test or setting a boolean is not compatibility evidence.
