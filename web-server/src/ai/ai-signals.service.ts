import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';

export type StudentSignal = {
  source: string;
  subject?: string | null;
  topic?: string | null;
  score?: number | null;
  maxScore?: number | null;
  detail?: string | null;
};

export type StudentSignals = {
  studentId: string;
  weakTopics: string[];
  strongTopics: string[];
  schemeTopics: string[];
  signals: StudentSignal[];
};

@Injectable()
export class AiSignalsService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  async buildStudentSignals(tenantId: string, studentId: string): Promise<StudentSignals> {
    const ds = await this.getTenantDs(tenantId);
    const weakTopics: string[] = [];
    const strongTopics: string[] = [];
    const schemeTopics: string[] = [];
    const signals: StudentSignal[] = [];

    try {
      const cbtRows: any[] = await runDbQuery(
        ds,
        `SELECT e.title as "examTitle", sub.name as "subjectName",
                a.score, a.max_score as "maxScore", a.submitted_at as "submittedAt"
         FROM cbt_attempts a
         JOIN cbt_exams e ON e.id = a.exam_id
         LEFT JOIN academic_subjects sub ON sub.id = e.subject_id
         WHERE a.student_id = ? AND a.status = 'submitted' AND a.max_score > 0
         ORDER BY a.submitted_at DESC
         LIMIT 20`,
        [studentId],
      );
      for (const row of cbtRows) {
        const score = Number(row.score);
        const maxScore = Number(row.maxScore);
        const pct = maxScore > 0 ? (score / maxScore) * 100 : 0;
        const topic = String(row.examTitle || 'CBT practice');
        signals.push({
          source: 'cbt',
          subject: row.subjectName || null,
          topic,
          score,
          maxScore,
          detail: `CBT attempt ${Math.round(pct)}%`,
        });
        if (pct < 60) weakTopics.push(topic);
        else if (pct >= 80) strongTopics.push(topic);
      }
    } catch {
      /* CBT tables may be absent on some tenants */
    }

    try {
      const resultRows: any[] = await runDbQuery(
        ds,
        `SELECT sub.name as "subjectName", AVG(r.total_score) as "avgScore"
         FROM academic_results r
         LEFT JOIN academic_subjects sub ON sub.id = r.subject_id
         WHERE r.student_id = ? AND r.total_score IS NOT NULL
         GROUP BY sub.name
         HAVING COUNT(*) >= 1`,
        [studentId],
      );
      for (const row of resultRows) {
        const avg = Number(row.avgScore);
        const subject = String(row.subjectName || 'Subject');
        signals.push({
          source: 'academic_results',
          subject,
          topic: subject,
          score: avg,
          maxScore: 100,
          detail: `Average result ${Math.round(avg)}%`,
        });
        if (avg < 50) weakTopics.push(subject);
        else if (avg >= 75) strongTopics.push(subject);
      }
    } catch {
      /* results optional */
    }

    try {
      const schemeRows: any[] = await runDbQuery(
        ds,
        `SELECT sub.name as "subjectName", st.title, st.is_next as "isNext"
         FROM student_class_enrollments e
         JOIN academic_schemes_of_work s ON s.class_id = e.class_id
         JOIN academic_scheme_topics st ON st.scheme_id = s.id
         JOIN academic_subjects sub ON sub.id = s.subject_id
         WHERE e.student_id = ?
         ORDER BY st.is_next DESC, st.order_index ASC
         LIMIT 8`,
        [studentId],
      );
      for (const row of schemeRows) {
        const label = `${row.subjectName || 'Subject'}: ${row.title}`;
        schemeTopics.push(label);
        if (row.isNext) {
          signals.push({
            source: 'scheme',
            subject: row.subjectName || null,
            topic: String(row.title),
            detail: 'Next scheme topic',
          });
        }
      }
    } catch {
      /* scheme optional */
    }

    const dedupe = (items: string[]) => [...new Set(items.map((item) => item.trim()).filter(Boolean))];

    return {
      studentId,
      weakTopics: dedupe(weakTopics).slice(0, 8),
      strongTopics: dedupe(strongTopics).slice(0, 8),
      schemeTopics: dedupe(schemeTopics).slice(0, 8),
      signals: signals.slice(0, 24),
    };
  }
}
