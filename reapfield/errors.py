"""Error types. Everything the CLI turns into a non-zero exit lives here."""


class ReapfieldError(Exception):
    """Base for every error this package raises deliberately."""


class RobotsDisallowed(ReapfieldError):
    """robots.txt forbids fetching this URL. Nothing was fetched."""


class BlockedError(ReapfieldError):
    """The site actively refused automated access. We report and stop."""


class TerminalHTTPError(ReapfieldError):
    """A non-retryable HTTP status (404/410/401/403)."""


class BudgetExceeded(ReapfieldError):
    """The per-run LLM derivation cap was hit."""


class DerivationFailed(ReapfieldError):
    """The LLM could not be reached. Distinct from "the field is not on the page"."""


class GatedPlatform(ReapfieldError):
    """A login-gated platform with no registered official-API adapter."""


class ModeConflict(ReapfieldError):
    """--one/--many demanded a cardinality the page cannot satisfy."""


class UnsafeURL(ReapfieldError):
    """MCP-only: the URL failed the trust-boundary check."""
