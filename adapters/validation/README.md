# Cross-vendor validation adapter

The implementation is in stocktrend/providers.py and stocktrend/validation.py.

- Codex subscription producer -> Anthropic API validator
- Claude subscription producer -> OpenAI API validator
- Validator failure -> signal remains research-only with unavailable validation
- Same vendor -> unavailable validation and a degraded run

Only minimized evidence packets may cross this boundary.
