# Caldova private application release

The manually dispatched `caldova-release.yml` workflow releases the application
agent and its matching web application, not the landing-zone foundation.
It builds from an exact Git archive on the existing private ACR pool,
creates a candidate version, evaluates that exact
version and digest, and only then builds and publishes the matching web image
before changing the API's promoted version.

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
The evaluation service can live in a separately approved, supported private
region without relocating the application or agent. Configure the
`CALDOVA_EVALUATION_PROJECT_ENDPOINT` variable in the `caldova-private` GitHub
environment. The full-release step passes it explicitly as
`--evaluation-project`, so an unset or malformed value fails rather than
silently choosing another project. Local commands without that option retain
the original project.

Set `CALDOVA_EVALUATION_PRIVATE_ADDRESS` to the verified services.ai private
endpoint address. The workflow supplies `--evaluation-private-address` so only
that evaluation hostname is resolved to the approved RFC 1918 address within
the release process. The original HTTPS hostname, SNI and certificate
validation remain unchanged. Other destinations use normal resolution, and
the resolver is restored on success or error. This avoids changing corporate
DNS, VPN routes or existing private DNS zones for the separate region.

The original project client still creates the explicitly versioned sessions
and invokes the real candidate. Only the native evaluation definition and run
use the separate evaluation client. Both projects must remain reachable through
their approved private network paths and the job identity must have the needed
resource-scoped access. Receipts and native metadata record both project
endpoints along with the exact source, image and candidate version. Replaying a
historical diagnostic in the new region is not current-source quality approval.
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

## Coordinated web publication

Only a completed native run with total 3, passed 3, failed 0 and errored 0
allows the matching web image to be built. Promotion requires that successful
web build receipt, the same source commit, the expected private repository and
an immutable digest. The API image is unchanged because this release does not
change its source code. Authentication configuration is preserved.

The existing single-revision web application is updated first. Its new
revision must be ready, its root must return 200 and anonymous invoice access
must return 401 before the API can move to the evaluated agent version.
The web supports both recorded legacy evidence and new retrieval provenance.
The API must then become ready, pass health and anonymous-denial checks, and
retain the exact selected version while the matching web digest remains active.

If publication fails, the release fails. Recovery restores a pin or web image
changed by this attempt to its recorded baseline and checks readiness and
anonymous denial again. It refuses to overwrite an unrelated concurrent pin
or image change. A recovery failure remains visible in the failed receipt and
requires operator intervention. No accepted source pin is updated on failure.
Source-only validation does not exercise Azure rollout or recovery.

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
