# Claude host guidance

This repository implements the fail-closed workflow in
`stock-trend-workflow-plan-v2_2.md`.

When the outer task runs under Claude:

- Use the saved Claude subscription as producer and `OPENAI_API_KEY` only for
  the independent validator: `stocktrend run --host claude ...`.
- Never place `ANTHROPIC_API_KEY` into the Claude subscription producer
  subprocess.
- Same-vendor validation is prohibited. Validator failure makes every proposal
  research-only.
- Live trading remains disabled.
- After a run reaches `committed`, read both pending email requests reported by
  the CLI: `trending_analysis` and `system_logs`. Send each body and its
  attachments to the exact configured recipient through the authenticated mail
  connector, and run `stocktrend email-ack` for each request only after the
  connector confirms success.
- Completion logs must exclude credentials, raw provider prompts, and raw
  provider responses.

Verification before reporting completion:

~~~bash
pytest
stocktrend demo
git diff --check
~~~
