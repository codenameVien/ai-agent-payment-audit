class DomainNotRegisteredError(LookupError):
    """The requested purchase domain has not been composed into this app."""


class AuthenticationError(ValueError):
    """Authentication evidence is invalid, expired, or already consumed."""


class DuplicateEventError(RuntimeError):
    """An append-only event could not be serialized after bounded retries."""


class EvidenceIntegrityError(RuntimeError):
    """A stored evidence chain no longer matches its canonical hashes."""


class EvidenceTransitionError(RuntimeError):
    """An append lost its expected-head race or violates a singleton transition."""


class PaymentEvidenceError(RuntimeError):
    """Stored purchase evidence cannot authorize a payment transition."""


class PaymentPolicyError(RuntimeError):
    """A payment violates a request or wallet spending policy."""


class PaymentConflictError(RuntimeError):
    """A payment idempotency key is already bound to different immutable data."""


class ConfigurationError(RuntimeError):
    """Required secure configuration is absent or malformed."""
