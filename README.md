# Stock Trend Workflow

This repository is the analysis-first reference implementation of
[stock-trend-workflow-plan-v2_2.md](stock-trend-workflow-plan-v2_2.md).

It provides:

- versioned JSON contracts and policy files;
- atomic run manifests, locks, hashes, and state transitions;
- point-in-time fact normalization and deterministic screening;
- OpenAI and Anthropic API structured-output validator adapters;
- Codex and Claude subscription-backed producer adapters;
- mandatory cross-vendor semantic validation;
- deterministic rendering and a durable publication outbox;
- paper-only risk, approval, order-state, and evaluation primitives;
- an offline demo and failure-oriented tests.

Live trading is intentionally unavailable.

## Setup

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
~~~

## Offline demonstration

~~~bash
.venv/bin/stocktrend demo
~~~

The demo uses an OpenAI-labeled scripted producer and an
Anthropic-labeled scripted validator. It performs no network calls and creates
operational files under state/ and publishable output under artifacts/. It also
creates two batch-scoped email packages: trending analysis results and sanitized
system logs. The final committer publishes their artifacts after finalization.

## Run from Codex

Sign the Codex CLI in with the ChatGPT/Codex subscription and set only the
opposite-vendor validator key:

~~~bash
codex login
export ANTHROPIC_API_KEY=...
.venv/bin/stocktrend run \
  --input tests/fixtures/demo_observations.json \
  --host codex \
  --input-profile test
~~~

This command is a provider-integration smoke test, not a live market-sourcing
run. The fixture contains only AAPL and NVDA; the current momentum and volume
screen is expected to retain only NVDA. No sourcer or quote adapter is started
by `stocktrend run`. Test-profile input is always degraded with
`NON_PRODUCTION_INPUT` and cannot become execution-eligible.

The producer runs through `codex exec` using saved ChatGPT authentication. The
subprocess environment removes OpenAI API-key variables so it cannot silently
switch to usage-based OpenAI API billing. Anthropic API is used only for the
independent validator.

## Run from Claude

Sign Claude Code in with the Claude subscription and set only the OpenAI
validator key:

~~~bash
claude auth login
export OPENAI_API_KEY=...
.venv/bin/stocktrend run \
  --input tests/fixtures/demo_observations.json \
  --host claude \
  --input-profile test
~~~

Claude is the subscription-backed producer and OpenAI API is the independent
validator. `--host auto` is the default and recognizes Codex or Claude runtime
environment markers; pass the host explicitly in ordinary terminals.

API validator models are configured with `STOCKTREND_OPENAI_MODEL` and
`STOCKTREND_ANTHROPIC_MODEL`. Optional subscription producer overrides use
`STOCKTREND_CODEX_MODEL` and `STOCKTREND_CLAUDE_CODE_MODEL`. If the independent
validator is unavailable, the research artifact is retained with a warning and
all proposals remain non-executable.

## Live market-data analysis

Live market-data analysis is implemented separately from trading. It uses the
versioned four-bucket universe in `spec/universe.yaml` and a normalized,
read-only HTTPS gateway. The adapter is disabled by default until the endpoint,
license, retention policy, allowlisted host, and read-only credential have been
approved. Live order submission remains unavailable.

The gateway receives `GET` requests with `symbol` and `session_date` query
parameters, a bearer token in the `Authorization` header, and must return:

~~~json
{
  "schema_version": "1.0.0",
  "symbol": "NVDA",
  "quote": {
    "record_id": "provider-quote-id",
    "observed_at": "2026-07-15T19:59:30Z",
    "price": 100.0,
    "bid": 99.99,
    "ask": 100.01
  },
  "bar_metrics": {
    "record_id": "provider-metrics-id",
    "observed_at": "2026-07-15T19:59:00Z",
    "average_volume_20d": 1000000,
    "volume_ratio": 1.5,
    "momentum_20d_pct": 5.0
  }
}
~~~

After approval, set `adapters.http_json_gateway.enabled: true` in
`spec/sources.yaml` and configure the three environment variables documented in
`.env.example`. Then build and check a snapshot:

~~~bash
.venv/bin/stocktrend source --session-date YYYY-MM-DD
.venv/bin/stocktrend source-status --require-analysis-ready
.venv/bin/stocktrend source-status --require-full-coverage
~~~

Or source and analyze in one command:

~~~bash
.venv/bin/stocktrend live-analysis \
  --session-date YYYY-MM-DD \
  --host codex \
  --revision 1
~~~

The source command writes an immutable snapshot under
`state/source_snapshots/` and a heartbeat under `state/sourcing/`. Missing or
stale heartbeats block analysis. Incomplete industry coverage is allowed only
as research and adds `SOURCE_COVERAGE_INCOMPLETE` to every proposal. A
calendar-aware scheduler may invoke `live-analysis`; its dead-man monitor should
invoke `source-status --require-analysis-ready`, while the coverage monitor
should invoke `source-status --require-full-coverage`. Both alert on a nonzero
exit status.

## Completion email and logs

Before the final committer, every batch generates two independent emails:

1. `trending_analysis.md`, with the validated trend results and digest.
2. `system_logs.md`, with an attached sanitized `system_log.json`.

Each has its own durable request under `state/outbox/`, tagged with `batch_id`
and `email_kind`. Set `batch_id` in the input document to select a business
batch identifier; otherwise the run ID is used. The committer remains the last
workflow stage and publishes the digest and both email packages under
`artifacts/<run_id>/` before connector delivery is allowed. The default
recipient is `hodalalala@gmail.com`; override it with
`STOCKTREND_SUMMARY_EMAIL` or `--email-to`. Requests are created as `blocked`
and become `pending` only after the run reaches the terminal `committed` state.
The host agent then delivers both requests through its authenticated mail
connector and acknowledges each with:

~~~bash
.venv/bin/stocktrend email-ack \
  --operation-id email_<run_id>_<email_kind>_<hash> \
  --provider-message-id <connector-message-id>
~~~

No SMTP or Gmail credential is stored in this repository. Until a host
connector confirms delivery, each email request remains pending.

## Important boundaries

- Subscription production is isolated from usage-based validator API access.
- Codex must report ChatGPT login; Claude must report non-API subscription auth.
- Provider requests contain only the minimized task packet assembled by the
  workflow.
- Any source whose license forbids processing by the validator vendor cannot
  support an executable proposal.
- The paper engine uses the same explicit order transitions intended for future
  live adapters, but no live broker class is included.

## Repository map

~~~text
spec/                  Workflow, strategy, risk, approval, evaluation, sources
schemas/               JSON Schema contracts
prompts/               Versioned model task instructions
stocktrend/            Deterministic core and provider adapters
adapters/              Platform integration notes
CLAUDE.md               Durable Claude host routing and delivery instructions
tests/                  Contract, workflow, and failure tests
state/                  Runtime state, ignored by Git
artifacts/              Finalized publishable output, ignored by Git
~~~
