from app.schemas.chat import ChatMessage


def safe_messages(messages: list[ChatMessage] | None, *, max_messages: int = 20, max_chars: int = 4000) -> list[ChatMessage]:
    if not messages:
        return []
    cleaned: list[ChatMessage] = []
    for message in messages:
        role = message.role.strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = (message.content or "").strip()[:max_chars]
        if not content:
            continue
        cleaned.append(ChatMessage(role=role, content=content))
    return cleaned[-max_messages:]
