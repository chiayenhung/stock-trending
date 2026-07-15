# Stock Trend Workflow

This repository is the analysis-first reference implementation of
[stock-trend-workflow-plan-v2_2.md](stock-trend-workflow-plan-v2_2.md).

It provides:

- versioned JSON contracts and policy files;
- atomic run manifests, locks, hashes, and state transitions;
- point-in-time fact normalization and deterministic screening;
- OpenAI and Anthropic structured-output model adapters;
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
operational files under state/ and publishable output under artifacts/.

## Real cross-vendor run

Set OPENAI_API_KEY and ANTHROPIC_API_KEY without committing them, then run:

~~~bash
.venv/bin/stocktrend run \
  --input tests/fixtures/demo_observations.json \
  --producer openai \
  --validator anthropic
~~~

For a Claude producer with an OpenAI validator:

~~~bash
.venv/bin/stocktrend run \
  --input tests/fixtures/demo_observations.json \
  --producer anthropic \
  --validator openai
~~~

The provider models are configured with STOCKTREND_OPENAI_MODEL and
STOCKTREND_ANTHROPIC_MODEL. If the independent validator is unavailable, the
research artifact is retained with a warning and all proposals remain
non-executable.

## Important boundaries

- API access is separate from Codex or Claude desktop subscription access.
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
tests/                  Contract, workflow, and failure tests
state/                  Runtime state, ignored by Git
artifacts/              Finalized publishable output, ignored by Git
~~~
