# Stock Trend Research Workflow — Plan v3.0

**Analysis-only · point-in-time · evidence-linked · cross-vendor validated**

Status: Active baseline · 2026-07-16

This plan replaces the earlier workflow plan and removes transaction automation
from the product boundary. The repository produces research artifacts and
evaluates research assessments. It has no account, position-sizing, transaction,
or market-action capability, contract, adapter, command, or future rollout phase.

## 1. Purpose

The system produces reproducible stock-trend research from point-in-time market
and public-source evidence. Models interpret bounded evidence and return
structured research assessments. Deterministic code owns source collection,
normalization, screening, orchestration, schemas, validation, rendering, state,
delivery requests, and evaluation.

Core principles:

1. **Research only** — every output is structurally non-transactional.
2. **Workflow first** — a deterministic state machine surrounds bounded model
   tasks.
3. **Point-in-time correctness** — facts must have been available at the
   recorded assessment timestamp.
4. **Evidence lineage** — factual claims cite normalized fact IDs.
5. **Numbers are sourced or deterministically derived** — model scores remain
   labeled assessments.
6. **Independent validation** — every research signal receives semantic review
   from a different model vendor.
7. **Degraded results stay visible** — source or validator problems do not
   disappear; they are recorded and rendered.
8. **External content is untrusted data** — it cannot change instructions,
   policy, tools, or state transitions.
9. **Least privilege** — models receive minimized task packets and no delivery,
   Git, or secret-management tools.
10. **Explicit side effects** — publication and email delivery use durable,
    idempotent requests.
11. **Licensing and retention are correctness requirements.**
12. **One deterministic core, thin host adapters.**

## 2. Product boundary

### 2.1 Included

- US-listed common stocks, ADRs, and explicitly allowlisted ETFs;
- a reviewed, versioned research universe;
- market, filing, industry, and bounded social evidence;
- deterministic momentum, relative-volume, price, and liquidity screening;
- market-context and per-symbol research interpretation;
- research trend signals with evidence, horizons, monitoring triggers, and
  known gaps;
- cross-vendor semantic validation;
- deterministic Markdown and email packaging;
- point-in-time assessment evaluation.

### 2.2 Excluded

- account or portfolio connectivity;
- holdings, buying power, or exposure state;
- transaction instructions or quantity calculations;
- market-action adapters or state machines;
- approval flows for financial actions;
- any paper or production transaction mode;
- confidence-based capital allocation.

Adding an excluded capability is a separate product requiring a separate
repository, threat model, governance process, and explicit authorization. It is
not a phase of this plan.

## 3. Research vocabulary

The v3 assessment vocabulary is:

| Assessment | Meaning |
|---|---|
| `no_action` | Evidence does not justify additional monitoring |
| `watch` | Evidence is incomplete or mixed; continue observation |
| `positive_trend` | Cited evidence supports a positive trend assessment |
| `negative_trend` | Cited evidence supports a negative trend assessment |

Every research signal includes:

- research signal ID and revision;
- strategy ID and version;
- instrument, ticker, and venue;
- assessment timestamp and research horizon;
- thesis and monitoring triggers;
- evidence claim IDs and known gaps;
- confidence bucket as an uncalibrated assessment;
- producer lineage;
- `research_only: true`;
- semantic validation status and reason codes.

Research signals contain no entry, exit, quantity, price-protection, eligibility,
or account fields.

## 4. Workflow architecture

~~~text
[Calendar-aware research scheduler]
    |
    v
[Versioned universe registry]
    |
    v
[Read-only source adapters]
    |
    v
[Immutable snapshot + coverage report + heartbeat]
    |
    v
[Freshness and integrity gate]
    |
    v
[Normalize point-in-time facts and lineage]
    |
    v
[Deterministic industry-balanced screen]
    |
    +--> [Market-context model task]
    |
    +--> [Isolated per-symbol model tasks]
    |
    v
[Synthesis into research_signal contracts]
    |
    v
[Deterministic schema, strategy, and lineage validation]
    |
    v
[Different-vendor semantic validation]
    |
    v
[Deterministic digest + artifact QA]
    |
    v
[Research email + sanitized system-log email]
    |
    v
[Finalize immutable manifest]
    |
    v
[Idempotent artifact committer]
    |
    +--> [Published research artifacts]
    |
    +--> [Pending host-connector email requests]
~~~

Evaluation runs separately after configured horizons. It joins research signals
to later point-in-time observations and never delays research run completion.

## 5. Sourcing and coverage

### 5.1 Universe

The initial universe covers four required buckets:

- semiconductor;
- memory and storage;
- power infrastructure;
- software.

Each instrument has an ID, ticker, venue, asset type, bucket, classification
source, effective interval, and active status. Registry changes are reviewed and
versioned; historical snapshots are immutable.

### 5.2 Coverage gate

Before screening, the sourcer records configured, attempted, valid, stale,
missing, and rejected counts per bucket. Complete coverage requires at least four
fresh instruments per bucket and twenty overall.

Incomplete coverage may produce a degraded research digest, but the missing
bucket is never silently omitted. Every bucket is rendered with attempted,
valid, and passing counts.

### 5.3 Approved sources

- Tiingo data-only HTTPS API for quotes and adjusted history;
- SEC EDGAR listings for company filing events;
- EIA Today in Energy for relevant power context;
- a bounded host-browser X snapshot for the configured account and five-day
  window.

All credentials are read-only and removed from model subprocesses. Redirects
are rejected, hosts are allowlisted, and public text is treated as untrusted
data. Only normalized facts are retained when required by source policy.

## 6. Model roles

| Role | Responsibility | Output |
|---|---|---|
| market context | Interpret broad supplied facts | `cycle_context` |
| stock analyst | Assess one screened symbol | `analyst_output` |
| synthesizer | Consolidate analyst evidence | `research_signal[]` |
| semantic validator | Judge direct claim support independently | `semantic_verdict[]` |

The coordinator is deterministic code. Models cannot change run state,
validation status, source policy, delivery state, or retention policy.

Host routing:

- Codex subscription producer → Anthropic API validator;
- Claude subscription producer → OpenAI API validator.

Same-vendor validation is prohibited. Validator failure, timeout, quota
exhaustion, malformed output, or inconsistent claim coverage yields unavailable
or indeterminate validation and a degraded run reason.

## 7. Contracts

Core v3 contracts:

- `universe`;
- `source_snapshot`;
- `source_heartbeat`;
- `fact` and `facts_block`;
- `cycle_context`;
- `analyst_output`;
- `research_signal`;
- `semantic_verdict` and `validation_report`;
- `run_manifest`;
- `delivery_outbox_item` and `email_delivery_request`;
- `outcome`.

Every schema has a stable ID and semantic version. Breaking changes increment
the major version and require explicit migration or archival-only handling.

### 7.1 Fact lineage

Every fact records identity, timestamps, value, units, source record, source URL,
raw hash, provenance class, extraction method, corroboration state, license
class, and adapter version.

Deterministic validation rejects:

- future observations;
- unknown claim or fact IDs;
- duplicate claim IDs;
- cross-instrument evidence;
- strategy or version mismatch;
- assessments outside the approved vocabulary;
- research horizons outside policy;
- missing required fact types.

### 7.2 Semantic verdict consistency

A passing verdict is valid only when:

- producer and validator vendors differ;
- the target ID matches;
- supported and unsupported claim sets are disjoint;
- their union exactly equals the target evidence-claim set;
- no unsupported claims or reason codes remain.

Any inconsistency becomes indeterminate.

## 8. Run state and idempotency

The logical run key contains:

~~~text
workflow_version
strategy_id
strategy_version
venue
exchange_session_date
analysis_window
~~~

A run ID identifies one immutable attempt. A revision identifies an intentional
rerun or backfill. Checkpoints are reusable only when schema, checksum,
dependency hashes, producer versions, prompt versions, and policy versions
match.

Allowed analysis states are:

~~~text
created → ingesting → normalized → screened → analyzed → validated
        → rendered → emails_generated → finalized → committed
~~~

Any stage may transition to `failed`. Atomic writes and recorded hashes protect
checkpoint integrity. Publication and email operations use stable operation IDs
and durable acknowledgements.

## 9. Rendering and delivery

Rendering is deterministic and template-based. Artifact QA checks:

- unfilled template slots;
- unknown evidence claim references;
- unsafe script content;
- source coverage visibility;
- validation summaries and degraded banners.

Each batch creates two packages before finalization:

1. trending research analysis;
2. sanitized system logs.

Requests remain blocked until the manifest is committed. The host connector
sends each package and records acknowledgement only after confirmed success.
Completion logs exclude credentials, raw prompts, and raw provider responses.

## 10. Evaluation

Evaluation is research calibration, not transaction simulation. At T+1, T+5,
and configured horizons it records:

- baseline and observation prices;
- observed return;
- benchmark and excess return;
- whether positive or negative direction was correct;
- missing observations explicitly;
- adjusted-data and corporate-action treatment.

Watch and no-action assessments do not receive a forced directional score.
Missing observations remain missing; they are never converted to zero or
dropped to improve reported accuracy.

The initial golden set targets at least sixty point-in-time sessions spanning
rising, falling, range-bound, volatile, earnings-heavy, holiday, missing-data,
split, and halted-symbol cases.

## 11. Security and data governance

- Source adapters receive only read-only credentials.
- Producer subprocesses remove provider API keys that could change billing or
  routing.
- Model tasks run without repository, delivery, Git, or secret tools.
- URLs require HTTPS, public routability, canonical hosts, and redirect
  revalidation.
- Browser sessions, cookies, tokens, page HTML, and screenshots are not stored.
- Secrets are excluded from persisted and published artifacts.
- Each source declares license, retention, and redistribution policy.
- Operational state and licensed/raw content remain outside Git.

## 12. Platform portability

The portable unit is the deterministic engine plus versioned contracts. Host
adapters declare subscription authentication, opposite-vendor validation,
structured output, tool restrictions, timeouts, rate limits, scheduler
integration, data-sharing policy, and supported contract versions.

Conformance requires identical contract versions, deterministic validator
results, state-transition behavior, degraded-mode behavior, and delivery
idempotency. Model prose need not be byte-identical.

## 13. Observability

Each run should record:

- input and output references without secret values;
- component, prompt, model, source, schema, and policy versions;
- state transitions and degraded reasons;
- source freshness and coverage;
- validator status and reason codes;
- retries and quarantines;
- stage latency, token use, and cost where available;
- publication and delivery acknowledgements.

Alerts cover missed research deadlines, stale source heartbeats, incomplete
coverage, validator unavailability, and unacknowledged delivery requests.

## 14. Rollout

### Phase 0 — policy and threat model

- Approve research vocabulary, universe, data providers, retention, and
  cross-vendor routing.
- Confirm that excluded capabilities remain absent.

Exit gate: all required research policy values are set.

### Phase 1 — deterministic foundation

- Maintain schemas, manifests, locks, facts, provenance, screening, and failure
  tests.
- Maintain source URL, credential, and untrusted-content controls.

Exit gate: contract, lineage, idempotency, and security tests pass.

### Phase 2 — scheduled research

- Operate context, analyst, synthesis, validation, rendering, and delivery.
- Add calendar-aware scheduling and dead-man alerts.
- Build the sixty-session golden set.

Exit gate: stable scheduled runs and acceptable research-quality metrics.

### Phase 3 — calibration and portability

- Run independent evaluation jobs.
- Measure direction accuracy, benchmark-relative behavior, coverage, drift, and
  validator agreement.
- Add and conform a second host adapter.

Exit gate: evaluation methodology and adapter conformance are reviewed.

## 15. Priority backlog

### P0 — boundary and correctness

- [x] Remove transaction-oriented modules, commands, policies, and contracts.
- [x] Replace executable proposals with research-only trend signals.
- [x] Enforce exact semantic-verdict claim coverage.
- [x] Enforce same-instrument evidence lineage.
- [ ] Add schema migration documentation for the v3 contract break.
- [ ] Add adversarial tests for every deterministic validation reason.

### P1 — unattended research reliability

- [ ] Connect current-data research to an approved exchange calendar.
- [ ] Add scheduler and dead-man alert integration.
- [ ] Add durable delivery claiming for concurrent workers.
- [ ] Apply configured retry policy and retain every attempt.
- [ ] Add per-stage latency, token, and cost traces.
- [ ] Establish CI and the sixty-session golden set.

### P2 — scale and portability

- [ ] Add bounded per-symbol concurrency with provider rate limits.
- [ ] Add schema compatibility and migration tests.
- [ ] Complete second-host conformance testing.
- [ ] Add research drift and human-labeled validator calibration.

## 16. Decision register

| Decision | Required before |
|---|---|
| Exchange calendar implementation | Scheduled current-data research |
| Tiingo license and quota review | Routine production sourcing |
| X access, retention, and capture review | Social source activation |
| Concrete producer and validator models | Provider release |
| Golden-set owner and review cadence | Calibration reporting |
| Second host platform | Portability phase |

## 17. v3 removal record

Version 3 removes all transaction-oriented implementation and design material:

- paper and production transaction modes;
- transaction CLI commands;
- account and position inputs;
- risk, quantity, and financial-action policies;
- approval and transaction-event contracts;
- transaction state machines and adapter stubs;
- execution eligibility from model outputs;
- entry, stop, target, time-exit, and quantity fields;
- transaction-cost outcome simulation;
- transaction rollout and readiness phases.

The remaining system is a research, validation, evaluation, artifact, and
delivery workflow.
