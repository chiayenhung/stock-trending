# Claude Cowork adapter

Claude-produced structured output runs through `claude --print` with saved
Claude subscription authentication and is validated by the OpenAI Responses
API adapter. `ANTHROPIC_API_KEY` is removed from the producer subprocess so the
producer cannot silently use API billing.

Same-vendor fallback is prohibited. Provider credentials and data sharing
controls are configured outside prompts. The adapter produces research signals
and exposes no transaction capability. After the final committer succeeds,
the host sends the separate trending-analysis and system-log emails through an
authenticated mail connector and acknowledges both durable email outbox items.
The connector must send each `.html` body using the request's
`body_mime_type` (`text/html; charset=utf-8`) and preserve the declared
attachments. Ranked research sections contain only independently validated
signals.
