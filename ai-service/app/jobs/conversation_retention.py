from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models import AiConversation, AiMessage
from app.db.session import SessionLocal


class ConversationRetentionJob:
    def __init__(self, retention_days: int = 90) -> None:
        self._retention_days = retention_days

    def run(self, *, tenant_id: str | None = None, db: Session | None = None) -> dict[str, int]:
        session = db or SessionLocal()
        owns = db is None
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        deleted_messages = 0
        deleted_conversations = 0
        try:
            conv_query = session.query(AiConversation).filter(AiConversation.created_at < cutoff)
            if tenant_id:
                conv_query = conv_query.filter(AiConversation.tenant_id == tenant_id)
            stale_ids = [row.id for row in conv_query.limit(500).all()]
            if stale_ids:
                deleted_messages = (
                    session.query(AiMessage)
                    .filter(AiMessage.conversation_id.in_(stale_ids))
                    .delete(synchronize_session=False)
                )
                deleted_conversations = (
                    session.query(AiConversation)
                    .filter(AiConversation.id.in_(stale_ids))
                    .delete(synchronize_session=False)
                )
            session.commit()
            return {
                "conversationsDeleted": deleted_conversations,
                "messagesDeleted": deleted_messages,
                "retentionDays": self._retention_days,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()
