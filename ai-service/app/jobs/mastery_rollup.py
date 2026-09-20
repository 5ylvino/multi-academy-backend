from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import AiTopicMasteryRollup
from app.db.session import SessionLocal
from app.schemas.insights import (
    ClassStudentMastery,
    MasteryRollupRequest,
    MasteryRollupResponse,
    TopicHeatmapCell,
)


def _severity(below: int, total: int) -> str:
    if total <= 0:
        return "low"
    ratio = below / total
    if ratio >= 0.5 or below >= 8:
        return "high"
    if ratio >= 0.25 or below >= 4:
        return "medium"
    return "low"


class MasteryRollupJob:
    def compute_cells(
        self,
        *,
        student_mastery: list[ClassStudentMastery],
        threshold: int,
        subject_id: str | None = None,
    ) -> list[TopicHeatmapCell]:
        grouped: dict[tuple[str, str | None], list[int]] = defaultdict(list)
        for row in student_mastery:
            if subject_id and row.subject_id and row.subject_id != subject_id:
                continue
            grouped[(row.topic_key, row.subject_id)].append(row.mastery_pct)

        cells: list[TopicHeatmapCell] = []
        for (topic_key, sub_id), values in grouped.items():
            below = sum(1 for v in values if v < threshold)
            avg = int(round(sum(values) / len(values))) if values else 0
            cells.append(
                TopicHeatmapCell(
                    topicKey=topic_key,
                    subjectId=sub_id,
                    studentCount=len(values),
                    belowThresholdCount=below,
                    avgMasteryPct=avg,
                    severity=_severity(below, len(values)),
                )
            )
        cells.sort(key=lambda c: (-c.below_threshold_count, c.avg_mastery_pct))
        return cells

    def run(
        self,
        *,
        tenant_id: str,
        payload: MasteryRollupRequest,
        db: Session | None = None,
    ) -> MasteryRollupResponse:
        session = db or SessionLocal()
        owns = db is None
        cells = self.compute_cells(
            student_mastery=payload.student_mastery,
            threshold=payload.mastery_threshold,
            subject_id=payload.subject_id,
        )
        try:
            for cell in cells:
                existing = (
                    session.query(AiTopicMasteryRollup)
                    .filter(
                        AiTopicMasteryRollup.tenant_id == tenant_id,
                        AiTopicMasteryRollup.class_id == payload.class_id,
                        AiTopicMasteryRollup.subject_id == cell.subject_id,
                        AiTopicMasteryRollup.topic_key == cell.topic_key,
                    )
                    .one_or_none()
                )
                if existing is None:
                    session.add(
                        AiTopicMasteryRollup(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            class_id=payload.class_id,
                            subject_id=cell.subject_id,
                            topic_key=cell.topic_key,
                            student_count=cell.student_count,
                            below_threshold_count=cell.below_threshold_count,
                            avg_mastery_pct=cell.avg_mastery_pct,
                            computed_at=datetime.now(timezone.utc),
                        )
                    )
                else:
                    existing.student_count = cell.student_count
                    existing.below_threshold_count = cell.below_threshold_count
                    existing.avg_mastery_pct = cell.avg_mastery_pct
                    existing.computed_at = datetime.now(timezone.utc)
            session.commit()
            return MasteryRollupResponse(
                classId=payload.class_id,
                topicsProcessed=len(cells),
                rollups=cells,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()
