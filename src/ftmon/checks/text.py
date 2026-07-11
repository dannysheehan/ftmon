"""Untrusted plugin text normalization."""

MESSAGE_LIMIT = 2048


def clean_message(value: str) -> str:
    # Preserve useful non-ASCII text, but remove ASCII controls that could forge
    # terminal/log structure. Newlines never reach here from the protocol adapters.
    cleaned = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127)
    return cleaned.encode("utf-8")[:MESSAGE_LIMIT].decode("utf-8", errors="ignore")
