from app.jobs.mastery_rollup import MasteryRollupJob
from app.schemas.insights import ClassStudentMastery


def test_compute_cells_groups_by_topic() -> None:
    job = MasteryRollupJob()
    cells = job.compute_cells(
        student_mastery=[
            ClassStudentMastery(studentId="s1", topicKey="fractions", masteryPct=30),
            ClassStudentMastery(studentId="s2", topicKey="fractions", masteryPct=80),
            ClassStudentMastery(studentId="s3", topicKey="algebra", masteryPct=90),
        ],
        threshold=60,
    )
    assert len(cells) == 2
    fractions = next(c for c in cells if c.topic_key == "fractions")
    assert fractions.below_threshold_count == 1
    assert fractions.avg_mastery_pct == 55
    assert fractions.severity == "high"
