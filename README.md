# Stock Trend Research Workflow

This repository is the analysis-only reference implementation of
[stock-trend-research-workflow-plan-v3_0.md](stock-trend-research-workflow-plan-v3_0.md).
See [ARCH.md](ARCH.md) for the detailed execution architecture, trust
boundaries, model routing, and task identities.

It provides:

- a versioned, industry-diversified research universe;
- point-in-time market, filing, industry, and bounded social facts;
- deterministic source coverage and momentum screening;
- isolated market-context and per-symbol model tasks;
- research-only trend signals linked to evidence claims;
- independent cross-vendor semantic validation;
- atomic manifests, checkpoints, hashes, and state transitions;
- deterministic digests, HTML research dashboards, sanitized logs, and durable
  email requests;
- point-in-time evaluation of research assessments.

The codebase has no transaction, account, position-sizing, or market-action
module or contract. `research_signal` is structurally research-only and contains
no entry, exit, quantity, or eligibility fields.

## Setup

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
~~~

## Offline demonstration

~~~bash
.venv/bin/stocktrend demo
~~~

The demo uses an OpenAI-labeled scripted producer and an Anthropic-labeled
scripted validator. It performs no network calls. Operational files are written
under `state/`, while finalized publishable output is written under `artifacts/`.
Each run generates two HTML email packages: a validated research dashboard and
sanitized system logs.

## Run from Codex

Sign the Codex CLI in with the ChatGPT/Codex subscription and configure only the
opposite-vendor validator key:

~~~bash
codex login
export ANTHROPIC_API_KEY=...
.venv/bin/stocktrend run \
  --input tests/fixtures/demo_observations.json \
  --host codex \
  --input-profile test
~~~

The producer runs through `codex exec` with saved ChatGPT authentication.
OpenAI API-key variables are removed from the producer subprocess. Anthropic is
used only for the independent validator.

The fixture contains AAPL and NVDA; the configured screen is expected to retain
only NVDA. Test-profile input is always marked `NON_PRODUCTION_INPUT`.

## Run from Claude

Sign Claude Code in with subscription authentication and configure only the
OpenAI validator key:

~~~bash
claude auth login
export OPENAI_API_KEY=...
.venv/bin/stocktrend run \
  --input tests/fixtures/demo_observations.json \
  --host claude \
  --input-profile test
~~~

Claude is the subscription-backed producer and the OpenAI Responses API is the
independent validator. Producer subprocesses have tools disabled and remove API
credentials belonging to the producer vendor.

Validator models are configured with `STOCKTREND_OPENAI_MODEL` and
`STOCKTREND_ANTHROPIC_MODEL`. Optional subscription producer overrides use
`STOCKTREND_CODEX_MODEL` and `STOCKTREND_CLAUDE_CODE_MODEL`. Validator failure,
timeout, malformed output, or same-vendor routing leaves the affected signal
research-only with unavailable or indeterminate validation.

## Current market-data research

The production research path uses the versioned four-bucket universe in
`spec/universe.yaml`. The approved market-data adapter is Tiingo's data-only
HTTPS API. It reads the consolidated equity snapshot and adjusted end-of-day
history endpoints and derives the 20-session average volume, current volume
ratio, and 20-session momentum deterministically.

Create a Tiingo account, review its internal-use license, and place the token in
`STOCKTREND_TIINGO_API_TOKEN` in `.env`. The adapter sends the token in the
`Authorization` header and never in a URL.

Build and check a source snapshot:

~~~bash
.venv/bin/stocktrend source --session-date YYYY-MM-DD
.venv/bin/stocktrend source-status --require-analysis-ready
.venv/bin/stocktrend source-status --require-full-coverage
~~~

Source and analyze current data in one command:

~~~bash
.venv/bin/stocktrend analyze-live-data \
  --session-date YYYY-MM-DD \
  --host codex \
  --revision 1
~~~

The source command writes an immutable snapshot under
`state/source_snapshots/` and a heartbeat under `state/sourcing/`. Missing or
stale heartbeats block production research. Incomplete industry coverage is
allowed only with `SOURCE_COVERAGE_INCOMPLETE` recorded on the run.

The generic normalized HTTPS gateway remains disabled unless it is explicitly
reviewed and selected as `production.approved_adapter`.

## Public-source enrichment

When enabled, production sourcing adds normalized facts from:

- SEC EDGAR filing listings for company-specific filing events;
- EIA Today in Energy for relevant power-infrastructure context;
- an authenticated-browser snapshot of `@aleabitoreddit` posts visible in X's
  Latest search for the rolling prior five days.

Public enrichers reject redirects, allowlist hosts, store normalized facts
rather than page HTML, and treat all external content as untrusted data. Their
configured LLM fallback remains disabled.

Before current-data sourcing, the Codex host stores the normalized X snapshot at
`state/social/x_aleabitoreddit.json`. Cookies, tokens, screenshots, page HTML,
and browser session data must never be stored. If authentication is required,
ask the user to sign in rather than switching sources or bypassing access.

Validate a browser snapshot with:

~~~bash
.venv/bin/stocktrend validate \
  --schema social_browser_snapshot \
  state/social/x_aleabitoreddit.json
~~~

SEC requires an identifying user agent. Set `STOCKTREND_SEC_USER_AGENT` to an
application name and monitored contact email. It is sent only to SEC and is not
persisted in source snapshots or model packets.

## Research signals and validation

Every `research_signal` includes:

- a positive, negative, watch, or no-action assessment;
- an intended research horizon;
- a 1-to-10 uncalibrated signal-strength score, not an expected-return score;
- evidence-linked 5-session, 21-session, and 63-session outlooks;
- a thesis and monitoring triggers;
- evidence claim IDs and known gaps;
- producer lineage;
- `research_only: true`;
- an independent validation status and reason codes.

Deterministic checks enforce strategy version, assessment vocabulary, horizon,
claim existence, fact lineage, and same-instrument evidence. The semantic
validator receives only the signal, selected claims, and a minimized evidence
packet. Its vendor must differ from the producer vendor. Outlook percentages are
stored as `model_estimate_uncalibrated`; they are research likelihood estimates,
not historical win rates, expected returns, or profit guarantees.

Validate a stored signal with:

~~~bash
.venv/bin/stocktrend validate \
  --schema research_signal \
  path/to/research_signal.json
~~~

## Completion email and logs

Before the final committer, every batch generates:

1. `trending_analysis.html`, containing a responsive research dashboard with:
   the top five validated positive-trend opportunities, validated negative-trend
   warnings, and 5-session, 21-session, and 63-session likelihood outlooks. The
   Markdown digest remains attached for evidence review.
2. `system_logs.html`, with an attached sanitized `system_log.json`.

Each request declares `text/html; charset=utf-8`, is durable, batch-scoped, and
is blocked until the run reaches `committed`. The host then sends it through an
authenticated mail connector and acknowledges confirmed delivery:

~~~bash
.venv/bin/stocktrend email-ack \
  --operation-id <operation-id> \
  --provider-message-id <connector-message-id>
~~~

Credentials, raw provider prompts, and raw provider responses are excluded from
completion packages.

## Repository map

~~~text
spec/          Workflow, strategy, evaluation, source, tier, and universe policy
schemas/       Versioned JSON Schema research contracts
prompts/       Bounded model task instructions
stocktrend/    Deterministic research engine and provider adapters
adapters/      Host and validation integration notes
tests/         Contract, workflow, sourcing, security, and failure tests
state/         Ignored operational state
artifacts/     Ignored finalized research output
~~~

## Verification

~~~bash
.venv/bin/python -m pytest
.venv/bin/stocktrend demo
git diff --check
~~~

The offline demo proves only the scripted workflow. A real provider integration
was exercised only when valid credentials produced a successful network
response.
