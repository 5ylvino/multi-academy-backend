from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import AiTopicMastery
from app.db.session import SessionLocal
from app.schemas.guidance import StudentSignal, StudentSignals
from app.schemas.tutor import TopicMasteryItem


def _topic_key(topic: str, subject_id: str | None = None) -> str:
    base = topic.strip().lower()
    if subject_id:
        return f"{subject_id}:{base}"
    return base


class MasteryService:
    def list_mastery(
        self,
        *,
        tenant_id: str,
        student_id: str,
        db: Session | None = None,
    ) -> list[TopicMasteryItem]:
        session = db or SessionLocal()
        owns = db is None
        try:
            rows = (
                session.query(AiTopicMastery)
                .filter(
                    AiTopicMastery.tenant_id == tenant_id,
                    AiTopicMastery.student_id == student_id,
                )
                .order_by(AiTopicMastery.mastery_pct.asc())
                .limit(50)
                .all()
            )
            return [
                TopicMasteryItem(
                    topicKey=row.topic_key,
                    subjectId=row.subject_id,
                    masteryPct=row.mastery_pct,
                    attemptCount=row.attempt_count,
                    correctCount=row.correct_count,
                )
                for row in rows
            ]
        finally:
            if owns:
                session.close()

    def record_quiz_result(
        self,
        *,
        tenant_id: str,
        student_id: str,
        subject_id: str | None,
        topic: str,
        correct: bool,
        db: Session | None = None,
    ) -> int:
        session = db or SessionLocal()
        owns = db is None
        key = _topic_key(topic, subject_id)
        try:
            row = (
                session.query(AiTopicMastery)
                .filter(
                    AiTopicMastery.tenant_id == tenant_id,
                    AiTopicMastery.student_id == student_id,
                    AiTopicMastery.topic_key == key,
                )
                .one_or_none()
            )
            if row is None:
                row = AiTopicMastery(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    student_id=student_id,
                    subject_id=subject_id,
                    topic_key=key,
                    mastery_pct=100 if correct else 25,
                    attempt_count=1,
                    correct_count=1 if correct else 0,
                    evidence_json={"lastSource": "tutor_quiz"},
                )
                session.add(row)
            else:
                row.attempt_count += 1
                if correct:
                    row.correct_count += 1
                row.mastery_pct = min(
                    100,
                    max(0, int(round((row.correct_count / row.attempt_count) * 100))),
                )
                row.evidence_json = {**(row.evidence_json or {}), "lastSource": "tutor_quiz"}
                row.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return row.mastery_pct
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()

    def rollup_from_signals(
        self,
        *,
        tenant_id: str,
        signals: StudentSignals | None,
        db: Session | None = None,
    ) -> None:
        if not signals:
            return
        session = db or SessionLocal()
        owns = db is None
        try:
            for sig in signals.signals:
                self._apply_signal(session, tenant_id, signals.student_id, sig)
            for topic in signals.weak_topics:
                self._ensure_weak_topic(session, tenant_id, signals.student_id, topic)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            if owns:
                session.close()

    def _apply_signal(
        self,
        session: Session,
        tenant_id: str,
        student_id: str,
        sig: StudentSignal,
    ) -> None:
        if sig.source not in {"cbt", "academic_results"} or not sig.topic:
            return
        subject_id = None
        key = _topic_key(sig.topic, subject_id)
        pct: int | None = None
        if sig.score is not None and sig.max_score and sig.max_score > 0:
            pct = int(round((sig.score / sig.max_score) * 100))
        row = (
            session.query(AiTopicMastery)
            .filter(
                AiTopicMastery.tenant_id == tenant_id,
                AiTopicMastery.student_id == student_id,
                AiTopicMastery.topic_key == key,
            )
            .one_or_none()
        )
        if row is None:
            row = AiTopicMastery(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                student_id=student_id,
                subject_id=subject_id,
                topic_key=key,
                mastery_pct=pct if pct is not None else 40,
                attempt_count=1,
                correct_count=1 if pct and pct >= 70 else 0,
                evidence_json={"source": sig.source, "detail": sig.detail},
            )
            session.add(row)
            return
        if pct is not None:
            blended = int(round((row.mastery_pct + pct) / 2))
            row.mastery_pct = blended
            row.evidence_json = {**(row.evidence_json or {}), "source": sig.source, "detail": sig.detail}
            row.updated_at = datetime.now(timezone.utc)

    def _ensure_weak_topic(
        self,
        session: Session,
        tenant_id: str,
        student_id: str,
        topic: str,
    ) -> None:
        key = _topic_key(topic)
        row = (
            session.query(AiTopicMastery)
            .filter(
                AiTopicMastery.tenant_id == tenant_id,
                AiTopicMastery.student_id == student_id,
                AiTopicMastery.topic_key == key,
            )
            .one_or_none()
        )
        if row is None:
            session.add(
                AiTopicMastery(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    student_id=student_id,
                    subject_id=None,
                    topic_key=key,
                    mastery_pct=35,
                    attempt_count=0,
                    correct_count=0,
                    evidence_json={"source": "weak_topic_signal"},
                )
            )
        elif row.mastery_pct > 50:
            row.mastery_pct = 50
            row.updated_at = datetime.now(timezone.utc)
