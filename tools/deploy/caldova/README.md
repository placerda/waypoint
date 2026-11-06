# Caldova private application release

The manually dispatched `caldova-release.yml` workflow releases the application
agent, not the landing-zone foundation. It builds from an exact Git archive on
the existing private ACR pool, creates a candidate version, evaluates that exact
version and digest, and only then changes the API's promoted version.

The source fork is public. Only synthetic cases and non-secret configuration
belong here. "Private" describes the Azure network and executor, not repository
visibility. Never commit credentials, real business documents or runtime exports.

The API must first run the session-aware transport with
`APP_FOUNDRY_ORCHESTRATOR_AGENT_VERSION` set to the current numeric version and
`APP_FOUNDRY_RESPONSES_API_VERSION=v1`. A newer hosted version is not isolated by
calling the unversioned endpoint alone. Every release evaluation creates a
version-ref session and sends its ID in the Responses **request body**. The
returned session ID must match. Production uses the same mechanism.
Creating a hosted version is asynchronous. Session creation waits up to ten
minutes only for the explicit `agent_version_not_ready` response. Other startup
errors fail immediately. A provisioning timeout cannot bypass evaluation or
promote a different version.

## Real quality gate

Before any private executor job, the hosted source-validation job checks API
privacy and telemetry, every contract-agent regression, fail-closed release
contracts, web types, both locales, evidence rendering and the production web
build. `npm run evidence:check` renders the real invoice evidence components
in isolation. It checks retrieval versus citation labels, escaped passages,
native active lanes, mixed counts and unchanged legacy behavior. It is not
a browser end-to-end test or native evaluation approval.

The optional `source_only` dispatch input runs these checks without queuing a
private executor or performing Azure authentication, builds, evaluations or
promotion. Supply `runner_label=source-only-validation` for this mode.
The default remains a full release. A successful source-only run is not a
successful deployment or permission to change the active version.

Three versioned, read-only cases cover the known discrepancy, the separate
base-capacity line and an unavailable invoice. A structured `gpt-6.1-sol` judge
accessed only through the private AI gateway scores business accuracy, grounding,
governance and instruction adherence. Every score must be at least 4 out of 5.
Missing results, invocation errors, incomplete responses and missing rationales
fail the release.

Native Foundry evaluations execute Python threshold graders over the actual
agent responses and APIM judge scores. The Foundry report retains responses,
expected behavior and reasons. These are explicit custom evaluators, not
server-side built-in quality judges. This SDK/manual fallback is used because
the Azure MCP transport was unavailable. It does not bypass the AI gateway.
The native run must complete and pass every case before promotion.
The unrelated Agent Optimizer `eval.yaml` is not executed by this workflow.
This release uses the dataset and explicit rubric in this directory.

The invoice register counts the recommendation's referenced evidence records
when no expert citations were recorded. It does not invent independent experts
or Foundry IQ provenance. The aggregate shows money at risk for reviewed
invoices, including escalations, not guaranteed recovery. An agent with no
recorded expert lanes shows its actual invoice records instead of empty IQ lanes.

The recorder captures successful native MCP passages and their actual source
titles, original URIs and reference IDs in a request-local scope. Repeated
passages are deduplicated. These are retrieved passages, not an independent
expert verdict or a model-invented citation. Model-supplied citation metadata
is ignored. Historical recommendations are not backfilled. Deploy this code
only through the same candidate evaluation gate, never by changing the accepted
production version while native evaluation is blocked.

Success and failure receipts are retained as Actions artifacts. They identify
source commit, immutable image, candidate version, native evaluation and report.
The deployment step rechecks all identifiers and the unchanged active baseline.
It does not rebuild or silently select `latest`.

## Private execution and identity

Only `placerda` can dispatch the trusted `caldova-private-invoice` branch.
The `caldova-private` GitHub environment restricts deployment to that exact
branch. Its Azure variables are the existing runner identity client ID, tenant
and subscription. Its federated credential has subject
`repo:placerda@6265211/waypoint@1447365558:environment:caldova-private`.
This is the actual immutable subject returned by GitHub OIDC, including the
verified owner and repository IDs. Do not weaken GitHub's immutable-subject
setting or replace this with the legacy name-only subject.

The private VM runs a single-job ephemeral runner with a unique dispatch label.
Do not install it as a persistent service or approve untrusted pull requests for
this executor. Require approval for all external fork contributors. The VM's
firewall permits only reviewed outbound GitHub HTTPS destinations, not inbound
public access. No interactive Azure login or long-lived client secret is copied
to the VM. Azure authentication comes from the job's OIDC token.

After a successful promotion, record the accepted version, source commit and
digest in `app-definition.json` before any later native application deployment.
Infrastructure updates, including gateway policies, remain a separately reviewed
prepare, validate and deploy operation. They are not reprovisioned per release.
