import re

TOKEN_RE = re.compile(r"[a-z0-9+#.-]{2,}", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s()./-]{6,}\d)(?!\w)")


def redact_contact_data(text: str, known_contact_values: list[str] | None = None) -> str:
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    for value in sorted(
        (item.strip() for item in known_contact_values or [] if item and item.strip()),
        key=len,
        reverse=True,
    ):
        redacted = re.sub(re.escape(value), "[REDACTED_CONTACT]", redacted, flags=re.IGNORECASE)
    return redacted


def relevant_excerpt(text: str, query: str, *, max_characters: int) -> str:
    if len(text) <= max_characters:
        return text
    query_terms = {token.casefold() for token in TOKEN_RE.findall(query)}
    blocks = [block.strip() for block in re.split(r"\n\s*\n|(?<=\.)\s*\n", text) if block.strip()]
    if not blocks:
        return text[:max_characters]

    scored: list[tuple[int, int, str]] = []
    for index, block in enumerate(blocks):
        block_terms = {token.casefold() for token in TOKEN_RE.findall(block)}
        score = len(query_terms & block_terms)
        scored.append((score, -index, block))
    selected: list[tuple[int, str]] = []
    used = 0
    for _, negative_index, block in sorted(scored, reverse=True):
        addition = len(block) + (2 if selected else 0)
        if used + addition > max_characters:
            continue
        selected.append((-negative_index, block))
        used += addition
    if not selected:
        return text[:max_characters]
    return "\n\n".join(block for _, block in sorted(selected))
