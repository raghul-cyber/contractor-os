class RateLimitError(Exception):
    """Raised when an LLM provider returns a rate limit (429) status."""
    pass

class AllProvidersFailedError(Exception):
    """Raised when the LLMRouter exhausts its configured provider sequence without success."""
    pass

# We will reuse the built-in TimeoutError and ConnectionError for the others.
