# Cross-vendor validation adapter

The implementation is in stocktrend/providers.py and stocktrend/validation.py.

- Codex subscription producer -> Anthropic API validator
- Claude subscription producer -> OpenAI API validator
- Validator failure -> signal remains research-only with unavailable validation
- Same vendor -> unavailable validation and a degraded run

Only minimized evidence packets may cross this boundary.
The target includes 5-session, 21-session, and 63-session outlooks. The
validator checks their direction and stated uncertainty against the cited
claims; an outlook never becomes a calibrated historical win rate merely by
passing semantic validation.
