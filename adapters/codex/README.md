# Codex adapter

Codex repository guidance lives in AGENTS.md. Project agent definitions live in
.codex/agents. The core workflow is invoked through the stocktrend CLI.

Codex/OpenAI-produced structured output is validated by the Anthropic adapter.
The validator is an external API/service boundary; it is not assumed to be a
native Codex model. If that boundary is unavailable, proposals remain
research-only.
