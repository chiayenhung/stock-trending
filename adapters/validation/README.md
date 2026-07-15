# Cross-vendor validation adapter

The implementation is in stocktrend/providers.py and stocktrend/validation.py.

- OpenAI producer -> Anthropic validator
- Anthropic producer -> OpenAI validator
- Validator failure -> research-only
- Same vendor -> configuration failure and research-only

Only minimized evidence packets may cross this boundary.
