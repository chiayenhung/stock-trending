"""Typed workflow errors."""


class StockTrendError(Exception):
    """Base error for the workflow."""


class ConfigurationError(StockTrendError):
    """Configuration is missing or violates an invariant."""


class ContractError(StockTrendError):
    """A document failed its versioned contract."""


class StateTransitionError(StockTrendError):
    """A state-machine transition was not permitted."""


class ProviderError(StockTrendError):
    """A model provider failed or returned unusable output."""


class SafetyViolation(StockTrendError):
    """A requested action crossed a safety boundary."""


class VendorSeparationError(SafetyViolation):
    """Producer and semantic validator used the same model vendor."""


class LockUnavailableError(StockTrendError):
    """The logical run is locked by another process."""
