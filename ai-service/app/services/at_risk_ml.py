from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AtRiskFeatures:
    current_avg: float
    previous_avg: float
    attendance_rate: float
    weak_topic_count: int


class AtRiskMlScorer:
    """Lightweight logistic-style scorer (sklearn-compatible weights, no hard dependency)."""

    def score(self, features: AtRiskFeatures) -> tuple[float, str]:
        grade_delta = features.previous_avg - features.current_avg
        att_gap = max(0.0, 0.85 - features.attendance_rate)
        topic_pressure = min(features.weak_topic_count, 5) * 0.08
        logit = (
            -1.2
            + (0.06 * max(0.0, 55 - features.current_avg))
            + (0.05 * max(0.0, grade_delta))
            + (1.8 * att_gap)
            + topic_pressure
        )
        probability = 1 / (1 + pow(2.718281828, -logit))
        if probability >= 0.75 or features.current_avg < 40:
            level = "high"
        elif probability >= 0.45:
            level = "medium"
        else:
            level = "low"
        return round(probability, 3), level

    def enrich_student(self, row: dict) -> dict:
        features = AtRiskFeatures(
            current_avg=float(row.get("currentAvg") or row.get("current_avg") or 70),
            previous_avg=float(row.get("previousAvg") or row.get("previous_avg") or 70),
            attendance_rate=float(row.get("attendanceRate") or row.get("attendance_rate") or 1) / (
                100 if float(row.get("attendanceRate") or 0) > 1 else 1
            ),
            weak_topic_count=len(row.get("weakTopics") or row.get("weak_topics") or []),
        )
        prob, level = self.score(features)
        return {**row, "mlRiskScore": prob, "mlRiskLevel": level}
