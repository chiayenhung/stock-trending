# Codex adapter

Codex repository guidance lives in AGENTS.md. Project agent definitions live in
.codex/agents. The core workflow is invoked through the stocktrend CLI.

Codex/OpenAI-produced structured output runs through `codex exec` with saved
ChatGPT subscription authentication and is validated by the Anthropic API
adapter. OpenAI API-key variables are removed from the producer subprocess.

The validator is an external API/service boundary; it is not assumed to be a
native Codex model. If that boundary is unavailable, research signals retain an
unavailable validation status. The adapter exposes no transaction capability.
After the final committer succeeds, Codex sends the separate
trending-analysis and system-log emails through its authenticated mail
connector and acknowledges both durable email outbox items.
