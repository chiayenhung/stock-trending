# Stock Trend Workflow — Comprehensive Plan v2.2

**Fail-closed · point-in-time · validator-wrapped · platform-portable**

Status: Draft v2.2 · 2026-07-15

Supersedes: stock-trend-workflow-plan-v2_1.md

Initial delivery target: one reference implementation, analysis-only first

Future adapters: Claude Cowork · Codex · Hyperagent

---

## 1. Purpose and non-negotiable principles

This system produces evidence-linked stock research and, only after separate
readiness gates, structured trade proposals. Models may interpret evidence and
write narrative. Deterministic code owns orchestration, schemas, calculations,
state transitions, permissions, risk, approvals, delivery, and order handling.

1. **Workflow first, agents second** — the core is a deterministic state
   machine with bounded model tasks.
2. **Point-in-time data only** — every decision is reproducible from information
   available at its recorded decision timestamp.
3. **Numbers are sourced or derived** — market numbers originate in tools;
   derived numbers carry formula and input lineage; model scores are explicitly
   labeled as assessments rather than observed facts.
4. **Fail closed** — missing, stale, invalid, or incompletely corroborated inputs
   can produce a research artifact but can never produce an executable intent.
5. **Execution is disabled by default** — analysis-only precedes paper mode;
   paper mode precedes live mode; live mode requires an explicit readiness gate.
6. **Every executable proposal is independently validated** — every LLM
   semantic validator must use a different model vendor from the producer of
   the output being checked. Sampling is allowed for research-quality
   monitoring, never for approving an actionable proposal.
7. **External content is untrusted data** — social, news, filings, feeds, web
   pages, and tool messages are never treated as instructions.
8. **Least privilege by component** — data readers cannot place orders;
   publishers cannot access broker order tools; models never receive secrets.
9. **Side effects are explicit and idempotent** — messages, commits, approvals,
   and orders use durable outbox/state records and unique operation identifiers.
10. **One reference engine, thin adapters** — business logic and validation do
    not vary by platform; adapters expose platform capabilities and constraints.
11. **No live capital is controlled by an uncalibrated feature** — confidence
    and model scores remain logged features until statistically validated.
12. **Licensing and retention are part of correctness** — data is stored and
    redistributed only as permitted by its source terms.

## 2. Version 1 strategy and policy contract

Implementation must not begin beyond schema scaffolding until this contract is
approved and represented in spec/strategy.yaml and spec/risk-policy.yaml.
Unset required policy values are validation errors, not defaults.

### 2.1 Safe initial scope

- Asset scope: US-listed common stocks, ADRs, and explicitly allowlisted ETFs.
- Excluded initially: options, short sales, leverage, OTC securities, and
  unsupported foreign-exchange listings.
- Taiwan-listed names: analysis-only until a compatible execution venue,
  currency/FX policy, and market calendar are separately approved.
- Operating mode at launch: analysis_only.
- Paper mode: uses the same proposal, approval, order-state, reconciliation, and
  exit machinery as live mode, with a simulated or broker paper adapter.
- Initial live mode, if later enabled: every exposure-increasing or discretionary
  order requires human approval, regardless of notional or model confidence.
  Protective exits are approved as part of the entry intent and may execute
  automatically under the approved exit policy.

### 2.2 Signal vocabulary

The v1 signal_type enum is:

| Value | Meaning | May create an entry intent? |
|---|---|---:|
| no_action | Evidence does not justify action | No |
| watch | Monitor a thesis or trigger | No |
| enter_long | Propose a new long position | Only after all gates |
| add_long | Propose adding to an existing long | Only after all gates |
| reduce | Propose reducing an existing long | Only after all gates |
| exit | Propose closing an existing long | Only after all gates |

Each actionable signal must specify:

- strategy_id and strategy_version;
- instrument identity and venue;
- decision timestamp and market session;
- intended holding horizon in trading sessions;
- entry condition and maximum entry price;
- initial stop or thesis-invalidation exit;
- profit target or trailing-exit rule;
- time exit;
- quantity proposal basis, without allowing the model to finalize quantity;
- evidence claims and fact lineage;
- proposal expiry;
- assumptions and known data gaps.

### 2.3 Required policy decisions

The following are P0 configuration decisions. They must be set before paper
execution and separately approved before live execution:

- allowed instruments and venues;
- analysis and execution session windows;
- maximum quote age by stage;
- minimum liquidity and price thresholds;
- per-order, per-position, per-sector, and portfolio exposure caps;
- daily loss and drawdown circuit-breaker thresholds;
- order types and limit-price protection;
- approval roles and approval expiry;
- holding horizon and exit policy by strategy;
- market-data and corporate-action providers;
- slippage and transaction-cost assumptions.

## 3. Three separate workflows

Analysis, execution, and evaluation have different clocks and failure modes.
They are separate workflows connected only by versioned structured contracts.

### 3.1 Analysis workflow

~~~
[Market-aware scheduler]
    |
    v
[Create run manifest + acquire run lock]
    |
    v
[1. Ingest] parallel read-only source adapters
    |
    v
[2. Normalize] facts builder, corporate actions, provenance, staleness
    |
    v
[3. Screen] deterministic momentum/volume/liquidity rules
    |
    v
[4A. Market context] model interpretation
    |
    v
[4B. Per-symbol analysis] isolated model tasks
    |
    v
[4C. Synthesis] narrative + structured signal proposals
    |
    v
[5. Deterministic validation] schemas, lineage, policy, completeness
    |
    v
[6. Semantic validation]
    |-- 100% of potentially actionable proposals
    |-- sampled research-only narrative for drift monitoring
    |
    v
[7. Deterministic render] digest and delivery artifacts
    |
    v
[8. Artifact QA] numeric provenance, HTML safety, recipient policy
    |
    v
[9A. Generate trending-analysis email] batch-scoped result package
    |
    v
[9B. Generate system-logs email] sanitized operational package
    |
    v
[Finalize immutable manifest]
    |
[Final committer] idempotently publish approved artifacts
    |
    +--> [Two email delivery outbox items]
~~~

Email bodies and requests are generated before the committer, but connector
delivery, Git commit, approval request, or broker action cannot occur until the
run is finalized, the committer succeeds, and the relevant QA gates pass.

### 3.2 Execution workflow

~~~
[Validated proposal]
    |
    v
[Proposal freshness + eligibility validation]
    |
    v
[Current market/quote/position snapshot]
    |
    v
[Deterministic risk and sizing engine]
    |
    v
[Execution intent]
    |
    v
[Durable approval queue]
    |
    v
[Approval revalidation at submit time]
    |
    v
[Paper or live broker adapter]
    |
    v
[Order events + reconciliation + exit manager]
~~~

The execution workflow never consumes digest prose. It consumes only a
validated signal_proposal and creates a separate execution_intent.

### 3.3 Evaluation workflow

Evaluation is scheduled independently after each configured horizon, such as
the next eligible close, T+1, and T+5 trading sessions. It joins proposals and
orders to point-in-time market data and records missing observations explicitly.
It is not part of the analysis run completion deadline.

## 4. Components, model roles, and contracts

The plan distinguishes model agents from deterministic components.

### 4.1 Model agents

| Agent | Role | Tier | Output |
|---|---|---|---|
| source_extractor | Extract structured facts only when a feed cannot be parsed deterministically | T0 | facts[] |
| market_context | Interpret market and sector context | T2 | cycle_context |
| stock_analyst | Apply the approved rubric to one symbol | T2 | analyst_output |
| synthesizer | Produce cross-symbol narrative and proposals | T2 | digest_content, signal_proposals[] |
| semantic_validator | Check claim-evidence support independently | T1 from a different model vendor than the producer | verdicts[] |

The coordinator is deterministic code. A model may provide a research-only
recommendation about incomplete data, but it cannot change a run from degraded
to execution-eligible.

Different models from the same vendor do not satisfy validator independence.
For example, output produced by an OpenAI model in Codex is validated by a
configured Anthropic Claude model; output produced by a Claude model is
validated by a configured OpenAI model. Cross-vendor validation is invoked
through a dedicated provider adapter or validation service rather than assumed
to be native to the producer platform.

The reference host routing uses the current platform subscription only for the
producer: Codex invokes its saved ChatGPT subscription through `codex exec` and
uses an Anthropic API model as validator; Claude invokes its saved subscription
through `claude --print` and uses an OpenAI API model as validator. Producer
subprocesses remove same-vendor API-key variables and verify subscription auth
before generation so they cannot silently switch billing modes.

### 4.2 Deterministic components

| Component | Responsibility | Output |
|---|---|---|
| scheduler | Calendar-aware job triggering | schedule_event |
| run_manager | Locks, manifests, dependency hashes, state transitions | run_manifest, run_state |
| quote_ingestor | Quotes, bars, fundamentals through read-only credentials | raw facts |
| news_ingestor | Fetch and normalize configured news sources | raw facts |
| industry_ingestor | Fetch approved industry feeds | raw facts |
| social_ingestor | Fetch allowlisted posts through an approved interface | raw facts |
| facts_builder | Normalize identifiers, timestamps, units, lineage, and hashes | facts_block |
| screener | Deterministic candidate selection | candidates |
| validator_runner | Invoke code and semantic validators | validation_report |
| digest_renderer | Template-based Markdown generation | digest.md |
| email_generator | Generate separate trending-analysis and sanitized-system-log emails for one batch | email artifacts, two email requests |
| committer | Final idempotent publication of approved artifacts after email generation | commit_receipt, delivery_events |
| risk_engine | Eligibility, exposure, sizing, and circuit breakers | risk_decision |
| approval_service | Durable approval state and expiry | approval_record |
| order_manager | Submit, cancel/replace, reconcile, and track orders | order_events |
| exit_manager | Stop, target, time, and thesis-invalidation exits | exit_events |
| outcome_calculator | Point-in-time performance and attribution math | outcomes |

Template interpolation, joins, calculations, commits, and delivery formatting
do not use an LLM.

### 4.3 Industry-diversified sourcing universe and coverage gate

Production analysis must not infer its stock universe from a demo fixture or
from whichever symbols happen to be present in an input file. A deterministic,
versioned universe registry runs before quote ingestion and assigns every
instrument to one primary coverage bucket. The initial US-listed universe must
cover all four buckets below; the symbols are bootstrap examples, not an
instruction to trade and not a permanent hard-coded list.

| Required bucket | Scope | Bootstrap examples |
|---|---|---|
| semiconductor | designers, foundries, analog/connectivity, and equipment | NVDA, AMD, AVGO, QCOM, TSM, ASML, AMAT, LRCX |
| memory_storage | memory, storage, and memory-interface suppliers | MU, WDC, STX, MRVL, SIMO |
| power_infrastructure | data-center power, electrical equipment, grid construction, and generation | VRT, ETN, PWR, GEV, CEG |
| software | cloud platforms, enterprise software, data, observability, and security | MSFT, ORCL, NOW, PLTR, CRWD, SNOW, DDOG |

Each registry entry includes instrument ID, ticker, venue, asset type, bucket,
classification source, effective interval, and active/delisted status. Registry
changes are reviewed and versioned; symbol changes and delistings never mutate
historical snapshots. The initial list is reviewed at least quarterly, while
corporate-action status is checked for every run.

Before screening, the sourcer writes an immutable coverage report containing,
per bucket, the configured, attempted, valid, stale, missing, and rejected
instrument counts. A production analysis requires fresh quote and bar-metric
facts for at least four instruments in every required bucket and at least 20
instruments overall. A coverage shortfall adds
`SOURCE_COVERAGE_INCOMPLETE`, makes every proposal research-only, and is shown
prominently in the digest. Missing buckets are never silently omitted.

Screening remains threshold-based: a bucket may legitimately produce zero
candidates when none of its stocks pass the price, liquidity, volume-ratio, and
momentum rules. The digest still renders every bucket with attempted, valid,
and passing counts plus an explicit `no passing candidates` result. To prevent
one industry from crowding out the rest, the deterministic screener ranks
passing stocks within each bucket, retains at most three per bucket, and caps
the combined candidate set at 12. Ranking inputs and tie-breakers are declared
in strategy policy and do not use an LLM.

The production path is separate from the offline demo:

1. `stocktrend source` builds a point-in-time universe snapshot and coverage
   report using approved read-only adapters.
2. `stocktrend run` consumes that snapshot only when its configuration,
   universe version, hashes, and freshness checks match the run manifest.
3. Fixture provenance is accepted only in test/demo profiles. A Codex- or
   Claude-hosted production-profile run rejects fixture provenance instead of
   producing a report that resembles live sourcing.
4. The scheduler records a sourcer heartbeat, last successful snapshot, source
   latency, and per-bucket coverage. Missing or stale heartbeats raise a
   dead-man alert and block the production analysis from starting.

## 5. Contracts and schema rules

All contracts use versioned JSON Schema. The payload is distinct from the
transport envelope:

~~~json
{
  "envelope_schema_version": "1.0.0",
  "payload_schema": "signal_proposal",
  "payload_schema_version": "1.0.0",
  "run_id": "...",
  "producer": {
    "component": "stock_analyst",
    "component_version": "...",
    "platform": "...",
    "vendor": "...",
    "model": "...",
    "prompt_version": "..."
  },
  "data": {},
  "error": null
}
~~~

Exactly one of data or error is non-null.

### 5.1 Schema versioning

- Every schema has a stable $id and semantic version.
- Additive compatible changes increment the minor version.
- Breaking changes increment the major version.
- A compatibility matrix declares which producers, consumers, and adapters
  support each schema version.
- Breaking changes require migrations or explicit archival-only treatment.
- Each run manifest pins schema, prompt, model, tool, source-adapter, validator,
  template, strategy, and policy versions.
- CI validates examples, migrations, and producer/consumer compatibility.

### 5.2 Core schema index

- run_manifest
- run_state
- source_observation
- fact
- facts_block
- derived_fact
- claim
- candidate
- cycle_context
- analyst_output
- scenario
- signal_proposal
- validation_report
- semantic_verdict
- execution_intent
- risk_decision
- approval_record
- order_event
- delivery_outbox_item
- delivery_event
- outcome_observation
- outcome

### 5.3 Fact contract

Every fact includes:

- fact_id and canonicalization_version;
- fact_type and value_type;
- instrument_id, ticker, venue, and provider identifiers where applicable;
- observed_at, effective_at, retrieved_at, source timezone, and market session;
- value, currency, units, scale, and precision;
- adjusted/unadjusted status and corporate-action reference;
- source provider, source record ID, and allowed source URL;
- raw-content hash and source revision or retraction status;
- trust attributes: provenance class, freshness, corroboration state, and
  extraction method;
- license/retention class;
- run_id and source adapter version.

Trust is multidimensional. A broker source is not automatically correct merely
because it is a broker.

### 5.4 Derived fact contract

Every calculated number includes:

- derived_fact_id;
- deterministic formula identifier and formula version;
- input fact_ids;
- calculation timestamp;
- output value, units, scale, and precision;
- corporate-action and missing-data policy.

Models cannot invent derived facts. They may cite derived facts produced by
approved deterministic calculators.

### 5.5 Claim contract

Narrative claims are represented separately from facts:

- claim_id and claim_type;
- normalized claim text;
- supporting fact_ids and derived_fact_ids;
- support relationship, such as confirms, contradicts, or contextualizes;
- materiality;
- semantic-validator verdict;
- limitations and conflicting evidence.

Claim linkage is semantic, not merely referential: the existence of a fact ID
does not prove that the fact supports the claim.

### 5.6 Signal proposal contract

A signal proposal includes:

- signal_id, revision, strategy_id, and strategy_version;
- signal_type and execution_eligible boolean;
- instrument and venue identity;
- decision timestamp, session, expiry, and intended holding horizon;
- entry condition and maximum entry price where applicable;
- stop, target/trailing rule, time exit, and thesis invalidation;
- evidence claim_ids;
- known gaps and degraded reasons;
- analyst and synthesis lineage;
- confidence_bucket as an uncalibrated logged feature;
- proposal status.

The producer cannot set execution_eligible to true. Only the deterministic
validator runner can set it after all required checks pass.

## 6. Run identity, state, checkpoints, and idempotency

### 6.1 Run identity

trade_date alone is not an idempotency key. The logical run key contains:

~~~
workflow_version
strategy_id
strategy_version
venue
exchange_session_date
analysis_window
execution_mode
~~~

A run_id identifies one immutable attempt. run_revision identifies an approved
rerun or backfill. Environment is explicit: test, research, paper, or live.

### 6.2 Checkpoint rules

A checkpoint is reusable only when:

1. its file is fully written and atomically renamed;
2. its schema validates;
3. its checksum matches the manifest;
4. all dependency hashes match current inputs and configuration;
5. its producer and policy versions are compatible;
6. it is not marked superseded, retracted, or quarantined.

Stage output existence alone never implies completion.

### 6.3 Concurrency

- The run manager acquires a lease-based lock for each logical run key.
- Lock acquisition and renewal are recorded.
- Concurrent platform adapters cannot write the same run revision.
- Stale leases are recoverable only through an audited reconciliation step.
- State transitions use compare-and-set semantics.

### 6.4 Side-effect idempotency

Each delivery, approval request, Git commit, and broker operation has a durable
operation ID and outbox record. Retry checks recorded acknowledgement before
attempting the side effect again.

Each batch renders two deterministic emails before the final committer: one for
trending analysis results and one for sanitized system logs. Each request
includes batch ID, email kind, recipient, subject, body path, attachment paths,
and status. The committer is the terminal workflow stage. Only after it succeeds
may a platform mail connector perform delivery and record acknowledgement.
Credentials, raw provider prompts, and raw provider responses are excluded from
the system-log package. Requests remain blocked until the run reaches the
terminal committed state, then become pending for the connector.

## 7. Validation and degraded-mode policy

### 7.1 Invocation wrapper

~~~
invoke(task):
  validate task inputs
  verify required sources and freshness
  run producer
  validate schema and deterministic assertions
  validate provenance and policy
  run semantic validation when required
  record validation report
  pass, retry, quarantine, or fail
~~~

Retries never erase the initial failure. All attempts and validator reason codes
remain in the trace.

### 7.2 Deterministic failure policy

| Failure | Research artifact | Executable proposal |
|---|---:|---:|
| Optional news or social source unavailable | Allowed with degraded banner | Only if strategy policy says source is optional |
| Required quote or bar missing/stale | Allowed as incomplete | Prohibited |
| Corporate-action status unresolved | Allowed as incomplete | Prohibited |
| Fact or derived-fact lineage invalid | Quarantined | Prohibited |
| Actionable claim fails semantic validation | May show contradiction | Prohibited |
| Different-vendor semantic validator unavailable, timed out, or misconfigured | Allowed with validation-unavailable banner | Prohibited |
| Risk policy missing or invalid | Allowed | Prohibited |
| Approval missing, expired, or mismatched | Not applicable | Prohibited |
| Broker reconciliation incomplete | Allowed | New submissions prohibited |

The required-source matrix is declared per strategy and signal type. A model
cannot override it.

### 7.3 Validation coverage

- Schemas, enums, timestamps, ranges, freshness, units, identifiers, and lineage:
  100% deterministic validation.
- Every potentially actionable claim and proposal: 100% semantic validation by
  a different model vendor from the producer.
- Research-only narrative: sampled cross-vendor semantic validation for drift
  monitoring.
- Rendered artifacts: 100% numeric/derived-fact provenance validation.
- All order intents and state transitions: 100% deterministic validation.

### 7.4 Cross-vendor validator independence

The validator runner enforces vendor separation from trusted adapter metadata:

~~~
OpenAI/Codex producer     -> Anthropic/Claude validator
Anthropic/Claude producer -> OpenAI validator
Other producer vendor    -> configured validator with a different vendor_id
~~~

Rules:

- vendor_id identifies the model provider, not a model family or model name;
- the validator receives only the task specification, structured producer
  output, relevant claims, and a minimal evidence packet;
- the validator never receives the producer conversation, hidden reasoning, or
  writable tools;
- validator credentials, quotas, audit logs, and network policy are separate
  from producer credentials;
- the validation record includes producer vendor/model and validator
  vendor/model, and deterministic code rejects matching vendor IDs;
- unavailable, rate-limited, timed-out, malformed, or indeterminate validation
  makes the output research-only;
- any validator rejection or unresolved disagreement blocks execution and
  routes the item to human review;
- same-vendor fallback is prohibited for executable output;
- data sent to the validator must comply with source licensing, privacy,
  retention, and data-residency policy.

If licensed evidence cannot be processed by the independent vendor, it cannot
support an executable proposal until a compliant validation arrangement exists.
A redacted or minimized validator packet may be used only when it still contains
enough evidence to judge the claim.

### 7.5 Corroboration

Corroboration is claim-specific. A generic price observation does not
corroborate an industry, management, or social claim.

Examples:

- price/volume claim: independent market-data observation or derived bar fact;
- corporate event: issuer filing or approved primary-source event feed;
- industry datapoint: approved industry provider or documented independent feed;
- social thesis: at least one non-social fact that directly supports the same
  claim, not merely the same ticker.

A social-only claim may create a watch item. It cannot create an entry intent.

### 7.6 Numeric provenance

Numeric validation uses structured facts and derivations, not unrestricted
regex matching. Rendering references numeric tokens by fact or derived-fact ID.
Dates, list numbers, model rubric scores, scenario labels, and observed market
values are distinct types.

## 8. Untrusted content, security, and data governance

### 8.1 Untrusted-content boundary

Every external text payload is enclosed in a typed, delimited data block.
Prompts state that content inside the block is evidence only and cannot modify
task instructions, request tools, disclose secrets, or change policy.

This applies to:

- social posts;
- news and web pages;
- filings and transcripts;
- industry-feed descriptions;
- tool output and error messages;
- email or Slack content used as input.

### 8.2 Least privilege

- Source adapters receive read-only credentials and tools.
- Broker quote access and broker order access use separate credentials or
  separately permissioned services.
- Model agents cannot access order, approval, messaging, Git credential, or
  secret-management tools.
- Publisher components cannot access order tools.
- The order manager cannot alter research artifacts.
- Live credentials are unavailable in analysis-only and test environments.

### 8.3 URL and HTML safety

- Allowed URL schemes are explicit.
- URLs are canonicalized and checked against domain policy.
- Redirect count is limited and every redirect is revalidated.
- Loopback, link-local, private-network, and metadata-service destinations are
  blocked unless explicitly required and isolated.
- Link checks run without cookies or credentials.
- Email HTML is generated from escaped templates and sanitized.
- External images and tracking pixels are disabled by default.

### 8.4 Secrets, licensing, and retention

- Secrets are scanned before persistence and before publication.
- Raw external content is stored only when the provider license permits it.
- Each source declares retention, redistribution, and derived-data rules.
- Paid-feed, news, and social content are not committed to Git by default.
- Logs redact credentials, tokens, account identifiers, and sensitive payloads.
- X access method, current pricing, quota, and terms must be verified before
  implementation. Unsupported scraping is not a production dependency.

## 9. Executor and order-lifecycle boundary

### 9.1 Default state

live_enabled defaults to false and cannot be enabled by a prompt, model output,
signal proposal, or runtime retry. Enabling live mode requires approved
configuration plus the readiness gates in Section 14.

### 9.2 Proposal to execution intent

The risk engine creates an execution_intent from a currently valid proposal and
a fresh account/market snapshot. The intent records:

- signal_id and proposal revision;
- intent_id and intent revision;
- account and environment;
- instrument and venue;
- side, quantity, order type, and protected limit price;
- risk calculations and policy version;
- entry and exit plan;
- approval requirement and expiry;
- quote and position snapshot IDs.

The model never chooses final quantity.

### 9.3 Submit-time validation

Immediately before submission, deterministic code rechecks:

- proposal and approval expiry;
- exchange session and early-close state;
- symbol status, halt status, and corporate actions;
- quote freshness, spread, price bands, and liquidity;
- current position, open orders, buying power, and exposure;
- daily loss, drawdown, sector, position, and notional limits;
- kill switch and circuit-breaker state;
- duplicate and cancel/replace state;
- approved quantity and price still match the current intent.

Any material change invalidates approval and returns the intent to pending.

### 9.4 Order identifiers

client_order_id is derived from:

~~~
environment
intent_id
intent_revision
order_leg
order_revision
~~~

It is not equal to signal_id. Entry, stop, target, and replacement orders have
distinct IDs while retaining common intent lineage.

### 9.5 Order state machine

Allowed states include:

~~~
proposed
validated
pending_approval
approved
submitting
submitted
acknowledged
partially_filled
filled
cancel_pending
canceled
rejected
expired
reconciliation_required
reconciled
error
~~~

Transitions are deterministic and validated. Partial fills, rejection,
disconnects, cancel/replace, and restart recovery have explicit behavior.

### 9.6 Exit management

Stop, target/trailing, time, and thesis-invalidation exits are hard execution
prerequisites and must be complete before paper or live execution. The system
defines behavior for:

- partial entry fills;
- exit quantity synchronization;
- broker-native versus locally managed brackets;
- gaps through stop prices;
- market closure and halt;
- conflicting exit triggers;
- rejected or canceled exit orders;
- process restart and broker reconciliation.

Paper and live modes use the same state machine.

### 9.7 Approval policy

During initial live rollout:

- every exposure-increasing or discretionary order requires human approval;
- protective exit orders are pre-authorized with the approved entry intent and
  do not wait for a new approval when their trigger occurs;
- approval binds to one intent revision, quantity, price protection, account,
  and expiry;
- approval is invalid after any material intent change;
- no response means no order;
- the approver sees evidence, risk impact, existing exposure, and exit plan.

Automation tiers may be considered only after calibration and a separate policy
review.

## 10. Scheduling and market calendars

- Each venue uses an approved exchange calendar, including holidays, early
  closes, daylight-saving transitions, and exceptional closures.
- exchange_session_date is defined in the venue timezone.
- observed_at and retrieved_at are timezone-aware UTC timestamps.
- Analysis, execution, and evaluation jobs have separate schedules.
- Backfills are explicitly labeled and can never submit orders.
- The analysis deadline is configured relative to the intended decision window,
  not generically as market open plus 30 minutes.
- A dead-man alert fires when a required run misses its configured deadline.
- A separate execution alert fires when reconciliation, approval, or exit
  management is unhealthy.

## 11. Platform portability and adapters

The portable unit is the deterministic core engine plus versioned contracts,
not a collection of prompts.

### 11.1 Repository layout

~~~
workflow-repo/
├── AGENTS.md
├── spec/
│   ├── workflow.yaml
│   ├── strategy.yaml
│   ├── risk-policy.yaml
│   ├── approval-policy.yaml
│   ├── evaluation-policy.yaml
│   ├── tiers.yaml
│   └── sources.yaml
├── schemas/
├── core/
│   ├── orchestration/
│   ├── facts/
│   ├── screening/
│   ├── validation/
│   ├── rendering/
│   ├── delivery/
│   ├── execution/
│   └── evaluation/
├── prompts/{agent}/vN.md
├── templates/
├── adapters/
│   ├── data/
│   ├── broker/
│   ├── delivery/
│   ├── validation/
│   ├── claude-cowork/
│   ├── codex/
│   └── hyperagent/
├── .codex/
│   ├── config.toml
│   └── agents/*.toml
├── tests/
│   ├── contracts/
│   ├── golden/
│   ├── integration/
│   └── failure-injection/
├── state/                 # operational, access-controlled, Git-ignored
│   ├── runs/{run_id}/
│   ├── outbox/
│   └── locks/
└── artifacts/             # publishable finalized outputs only
~~~

### 11.2 Adapter responsibilities

An adapter declares:

- agent registration and task invocation;
- producer and independent-validator vendor/model/reasoning configuration;
- structured-output behavior;
- tool and credential binding;
- cross-vendor validation API/service binding and egress policy;
- validator packet minimization, residency, retention, and licensing controls;
- sandbox, network, and approval constraints;
- scheduler integration;
- concurrency and rate limits;
- timeout and retry behavior;
- supported schema and capability versions.

Platform limitations are explicit. They are not hidden behind a claim that all
adapters behave identically.

### 11.3 Codex adapter

For Codex:

- AGENTS.md contains durable repository guidance and verification commands;
- .codex/config.toml contains project-scoped configuration where supported;
- .codex/agents/*.toml defines custom agent roles and per-agent model,
  reasoning, sandbox, MCP, and skill settings;
- scheduled local execution is configured through the supported Codex/ChatGPT
  scheduling surface or an external service;
- unattended runs use narrow permissions and must tolerate unavailable
  interactive approvals;
- model-specific agent configuration is generated from validated tier mapping
  or checked for consistency in CI;
- output produced by an OpenAI model is sent to the configured Claude validator
  through the independent validation adapter;
- local production uses `codex exec` with verified ChatGPT subscription auth,
  while the validator alone receives `ANTHROPIC_API_KEY`;
- if the Claude validator is unavailable, the run may publish research with a
  warning but cannot mark any proposal execution-eligible;
- after the final committer succeeds, the host mail connector sends the
  trending-analysis and system-log emails, then acknowledges both outbox items.

### 11.4 Claude adapter

For Claude:

- local production uses `claude --print` with verified non-API subscription
  auth, tools disabled, and no session persistence;
- the producer subprocess removes `ANTHROPIC_API_KEY`, while the independent
  validator alone receives `OPENAI_API_KEY`;
- output is sent to the OpenAI Responses API validator;
- validator failure retains research artifacts but makes all proposals
  non-executable;
- after the final committer succeeds, the host mail connector sends the
  trending-analysis and system-log emails, then acknowledges both outbox items.

### 11.5 Tier capability mapping

tiers.yaml maps capabilities, not only names:

~~~yaml
tiers:
  T0:
    workload: extraction
    requires:
      structured_output: true
      tool_use: true
      minimum_context_tokens: 32000
  T1:
    workload: semantic_validation
    requires:
      structured_output: true
      independent_validation: true
      different_vendor: true
  T2:
    workload: research_reasoning
    requires:
      structured_output: true
      tool_use: true
      long_context: true

platforms:
  codex:
    producer_vendor: openai
    T0:
      model: TO_BE_APPROVED
      reasoning_effort: low
    T2:
      model: TO_BE_APPROVED
      reasoning_effort: high
  claude_cowork:
    producer_vendor: anthropic
    T0:
      model: TO_BE_APPROVED
      reasoning_effort: low
    T2:
      model: TO_BE_APPROVED
      reasoning_effort: high

validation:
  require_different_vendor: true
  unavailable_policy: research_only
  routes:
    openai:
      validator_vendor: anthropic
      validator_tier: T1
      validator_model: TO_BE_APPROVED_CLAUDE
    anthropic:
      validator_vendor: openai
      validator_tier: T1
      validator_model: TO_BE_APPROVED_OPENAI
~~~

Startup validates that configured models and platform capabilities satisfy tier
requirements. It also rejects any semantic-validator route whose vendor matches
the producer vendor. Unknown or unavailable validation capacity forces
research-only mode.

### 11.5 Conformance

Conformance means:

- identical contract versions and state-transition semantics;
- identical deterministic validator results;
- structurally valid outputs;
- semantic scores within declared tolerances;
- equivalent degraded-mode and side-effect behavior.

It does not require byte-identical model prose.

## 12. Storage and Git policy

- Source code, schemas, prompts, templates, policies, migrations, and test
  fixtures belong in Git.
- Operational run state, raw licensed data, credentials, locks, and order/account
  snapshots do not belong in Git.
- A finalized, redacted run manifest, publishable digest, and two email packages
  may be committed through the publisher outbox if repository publication is
  enabled.
- The deterministic artifact committer is last. Optional Git commits occur only
  after artifact QA, both email packages are generated, and the run is finalized.
- Large Parquet datasets use an approved local/object store with retention and
  backup policy.
- Run artifacts are immutable; corrections create a revision with lineage.
- Raw and normalized data access is auditable.

File-based state remains portable, but it is operational state rather than a Git
transport mechanism.

## 13. Observability and evaluation

### 13.1 Run traces

Each run records:

- input and output references, never secret values;
- component, prompt, model, tool, policy, and schema versions;
- timing, token, and cost data;
- validator results and reason codes;
- retries, quarantines, and degraded reasons;
- source freshness and coverage;
- state transitions and side-effect acknowledgements.

### 13.2 Golden set

The initial golden set targets at least 60 point-in-time market sessions and
must cover rising, falling, range-bound, high-volatility, earnings-heavy,
holiday/early-close, missing-data, split, and halted-symbol cases.

Golden replay tests:

- schema and migration compatibility;
- deterministic screen results;
- provenance and derived-fact calculations;
- required-source and degraded-mode decisions;
- claim-evidence support;
- proposal eligibility;
- rendering provenance;
- order-state transitions;
- duplicate suppression and recovery behavior.

### 13.3 Outcome methodology

Outcomes distinguish proposals, approved intents, submitted orders, and fills.
They record:

- decision, approval, submission, and fill timestamps;
- executable reference price and actual fill;
- spread, slippage, fees, and FX where applicable;
- benchmark and sector-relative return;
- dividends and corporate actions;
- maximum favorable and adverse excursion;
- realized and mark-to-market returns;
- configured T+1, T+5, and strategy-horizon observations;
- exit-rule outcome;
- missing-data and delisting status.

Missing observations remain explicit. They are not converted to zero or dropped
to satisfy a completeness threshold.

### 13.4 Calibration and release metrics

evaluation-policy.yaml defines numeric acceptance thresholds. Minimum safety
gates include:

- 100% schema validity for finalized outputs;
- 100% provenance coverage for actionable numeric claims;
- 100% cross-vendor semantic validation coverage for actionable proposals;
- zero duplicate delivery or order side effects in fault-injection tests;
- complete broker reconciliation before new submissions;
- successful kill-switch and circuit-breaker drills;
- no critical unresolved paper-mode incidents;
- minimum paper observation count and regime coverage;
- strategy performance evaluated net of configured costs;
- confidence calibration based on sample size and regime, not elapsed time alone.

Judge agreement is measured against a versioned human-labeled set. Cross-vendor
validation is mandatory for LLM semantic checks, but vendor separation is not a
substitute for calibration.

## 14. Rollout and live-readiness gates

### Phase 0 — policy and threat model

- Approve Section 2 strategy scope and signal vocabulary.
- Set required risk, approval, data, calendar, retention, and evaluation policy.
- Approve cross-vendor validator routing, credentials, data-sharing, residency,
  retention, and failure policy.
- Document failure modes, trust boundaries, and data licenses.
- Select one reference platform and one execution venue.

Exit gate: no required policy values remain unset.

### Phase 1 — deterministic foundation

- Implement schemas, migrations, run manifests, locks, and atomic checkpoints.
- Implement facts, derived facts, claims, provenance, and source normalization.
- Implement deterministic screening and validation CLIs.
- Build golden fixtures and failure-injection tests.

Exit gate: contract, provenance, idempotency, and failure-policy tests pass.

### Phase 2 — analysis-only reference workflow

- Implement market context, per-symbol analysis, and synthesis.
- Implement 100% actionable cross-vendor semantic validation.
- Implement deterministic rendering, artifact QA, and research publisher outbox.
- Run scheduled analysis with no broker order capability.

Exit gate: stable analysis runs, no unsupported actionable claims, and acceptable
golden-set results.

### Phase 3 — paper execution

- Implement risk engine, approvals, order manager, exit manager, and
  reconciliation.
- Run paper mode with live-equivalent state transitions.
- Test partial fills, rejects, disconnects, restarts, stale approvals, early
  closes, halts, and kill switches.
- Implement separate evaluation jobs.

Exit gate: all safety metrics in evaluation-policy.yaml pass for the required
sample and regime coverage.

### Phase 4 — constrained live pilot

- Obtain explicit human authorization to enable live configuration.
- Require human approval for every exposure-increasing or discretionary order;
  pre-authorized protective exits do not wait for a new approval.
- Use minimal approved exposure limits.
- Monitor reconciliation and exit management continuously.
- Auto-disable on any critical invariant breach.

Exit gate: separate review; progression is never automatic.

### Phase 5 — portability

- Generate and validate the second platform adapter.
- Run full conformance and failure-injection suites.
- Add the third adapter only after the second is stable.

Exit gate: contract, validation, degraded-mode, and side-effect semantics conform.

## 15. Priority-ordered implementation backlog

### P0 — required before analysis implementation

- [ ] Approve strategy.yaml and signal_type contract.
- [ ] Approve risk-policy.yaml, including fail-on-unset behavior.
- [ ] Approve approval-policy.yaml and the initial human-entry-approval plus
      pre-authorized-protective-exit rule.
- [ ] Approve evaluation-policy.yaml and point-in-time methodology.
- [ ] Approve cross-vendor validator routing and validator-data policy.
- [ ] Select reference platform, venue, market calendar, and data providers.
- [ ] Approve licensing, retention, and X access decisions.
- [ ] Create schemas v1 and compatibility rules.
- [ ] Implement run identity, manifest, locks, atomic writes, and invalidation.
- [ ] Implement facts, derived facts, claims, and provenance.
- [ ] Implement deterministic validators and required-source matrix.
- [ ] Implement untrusted-content wrapper for every external text source.
- [ ] Establish least-privilege tool and credential separation.

### P1 — analysis-only release

- [x] Implement the provider-neutral, read-only HTTPS source adapter boundary;
      concrete endpoint approval and enablement remain a deployment decision.
- [x] Implement the versioned industry-universe registry with semiconductor,
      memory/storage, power-infrastructure, and software buckets.
- [x] Implement `stocktrend source`, its immutable source-snapshot contract,
      and the per-bucket coverage report.
- [x] Enforce at least four fresh instruments per required bucket and 20 total;
      route incomplete coverage to `SOURCE_COVERAGE_INCOMPLETE` research-only.
- [x] Reject fixture provenance in production profiles and require explicit
      demo/test mode for repository fixtures.
- [x] Add deterministic within-bucket ranking, a three-candidate bucket cap,
      a 12-candidate total cap, and explicit zero-passing bucket rendering.
- [x] Add sourcer heartbeat and a stale-snapshot dead-man status command.
- [ ] Connect `live-analysis` to an approved calendar-aware scheduler and alert
      on nonzero `source-status --require-analysis-ready` results.
- [x] Implement deterministic screener.
- [ ] Implement model agents and tier capability checks.
- [ ] Implement complete cross-vendor semantic validation for potentially
      actionable output.
- [ ] Test validator outage, timeout, quota, disagreement, same-vendor rejection,
      and research-only fallback behavior.
- [ ] Implement deterministic Markdown and email rendering.
- [ ] Implement artifact QA and publisher outbox.
- [ ] Build 60-session golden set and failure-injection coverage.
- [ ] Add run traces, drift metrics, and per-tier cost tracking.
- [ ] Add analysis deadline and dead-man alerts.

### P2 — paper execution

- [ ] Implement proposal-to-intent risk and sizing engine.
- [ ] Implement durable approval queue and expiry.
- [ ] Implement complete order and exit state machines.
- [ ] Implement client order ID lineage and duplicate suppression.
- [ ] Implement startup and continuous broker reconciliation.
- [ ] Implement kill switch and circuit breakers.
- [ ] Implement paper broker adapter using live-equivalent paths.
- [ ] Implement evaluation jobs and calibrated outcome reporting.
- [ ] Run fault-injection and recovery drills.

### P3 — live-readiness review

- [ ] Meet minimum paper sample and regime requirements.
- [ ] Demonstrate net-of-cost evaluation under approved methodology.
- [ ] Pass reconciliation, duplicate, exit, and kill-switch gates.
- [ ] Complete security and data-governance review.
- [ ] Approve minimal live limits, human approval for exposure-increasing and
      discretionary orders, and pre-authorized protective exits.
- [ ] Obtain explicit authorization to set live_enabled=true.

### P4 — portability and expansion

- [ ] Add second platform adapter and conformance suite.
- [ ] Confirm Hyperagent configuration surface before implementation.
- [ ] Calibrate confidence before considering it for sizing.
- [ ] Add news clustering and digest memory.
- [ ] Add strategy-specific scenario trigger automation.
- [ ] Consider additional venues only with separate calendar, data, FX, and
      execution policies.

## 16. Decision register

These decisions remain open but now have explicit blocking stages:

| Decision | Must be resolved before |
|---|---|
| Reference platform and first venue | Phase 1 |
| Quote, corporate-action, news, and industry providers | Phase 1 |
| Universe classification source, review owner, and quarterly refresh process | Phase 1 |
| X official access, budget, quota, and retention | Social source activation |
| Concrete per-platform model and reasoning mappings | Phase 2 |
| Concrete Claude and OpenAI validator models, credentials, budget, and data residency | Phase 2 |
| Portfolio-specific exposure and loss limits | Phase 3 |
| Robinhood versus another paper/live broker interface | Phase 3 |
| Taiwan analysis-only, proxy, or separate execution venue | Any Taiwan execution work |
| Hyperagent agent/tool/model configuration surface | Phase 5 |
| Reduced human-approval tiers | Post-live calibration review |

## 17. Changes from v2.1

- Replaced one end-to-end clock with separate analysis, execution, and
  evaluation workflows.
- Replaced trade_date-only idempotency with versioned logical run identity,
  immutable attempts, locks, hashes, and atomic checkpoints.
- Made degraded runs structurally ineligible for execution.
- Added an industry-diversified sourcing universe, pre-screen coverage gate,
  fixture/production separation, and sourcer heartbeat requirement.
- Moved signal vocabulary, strategy policy, approvals, exits, and complete order
  lifecycle into P0/P2 prerequisites rather than post-executor enhancements.
- Required semantic validation of every potentially actionable proposal;
  sampling now applies only to research-quality monitoring.
- Required every LLM semantic validator to use a different model vendor from
  its producer, with OpenAI/Codex output routed to Claude and Claude output
  routed to OpenAI; validator unavailability now forces research-only mode.
- Replaced fact-ID existence and numeric regex checks with structured claims and
  derived-fact lineage.
- Expanded untrusted-content controls from social data to every external source.
- Converted renderer, email generator, committer, and outcome logger from model
  agents to deterministic components.
- Added least-privilege credentials, URL/HTML controls, data licensing, and
  retention policy.
- Corrected the Codex adapter to use AGENTS.md for guidance,
  .codex/config.toml for project configuration, and .codex/agents/*.toml for
  custom agent definitions.
- Changed conformance from byte-identical model output to contract,
  deterministic-validation, state-transition, and tolerance-based semantic
  equivalence.
- Removed raw operational state from Git while retaining portable file-based
  contracts and manifests.
- Replaced next-day-P&L-only feedback with point-in-time, cost-aware,
  multi-horizon evaluation.
- Added explicit paper and live readiness gates; live progression is never
  automatic.
