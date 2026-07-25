"""Eval corpus: sentence types Claude Code replies actually contain."""

CORPUS = [
    ("short_ack", "Done. All tests pass."),
    ("technical", "The Stop hook posts the final assistant message to a local daemon on port eight seven six five."),
    ("numbers", "Latency dropped from twelve hundred milliseconds to eighty five milliseconds, a ninety three percent improvement."),
    ("code_speech", "Rename get user by id to fetch user, then update the three call sites."),
    ("brainstorm", "Here is another angle. Instead of polling the queue, we could push events over a web socket, which removes the latency floor entirely. The tradeoff is connection management, but a heartbeat every thirty seconds keeps it honest."),
    ("punctuation", "Wait, really? Yes: retries, backoff, and jitter; all three matter."),
    ("acronyms", "The API returns JSON over HTTP, and the CLI caches OAuth tokens in SQLite."),
    ("hard_words", "Kubernetes orchestration with idempotent retries and deterministic serialization."),
    ("question", "Should we go with the MLX build, or stick with PyTorch for now?"),
    ("homographs", "I read the docs yesterday, and now I will read them again before we record a new record."),
]
