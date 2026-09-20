import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';

export type ClassStudentMastery = {
  studentId: string;
  topicKey: string;
  masteryPct: number;
  subjectId?: string | null;
};

@Injectable()
export class AiClassInsightsService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  async listClasses(tenantId: string): Promise<Array<{ id: string; name: string }>> {
    const ds = await this.getTenantDs(tenantId);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, name FROM academic_classes ORDER BY name ASC LIMIT 100`,
      [],
    );
    return rows.map((row) => ({ id: String(row.id), name: String(row.name || row.id) }));
  }

  async buildClassMastery(tenantId: string, classId: string, subjectId?: string): Promise<ClassStudentMastery[]> {
    const ds = await this.getTenantDs(tenantId);
    const rows: ClassStudentMastery[] = [];

    try {
      const cbtRows: any[] = await runDbQuery(
        ds,
        `SELECT a.student_id as "studentId", e.title as "examTitle", sub.id as "subjectId",
                a.score, a.max_score as "maxScore"
         FROM cbt_attempts a
         JOIN cbt_exams e ON e.id = a.exam_id
         JOIN student_class_enrollments en ON en.student_id = a.student_id
         LEFT JOIN academic_subjects sub ON sub.id = e.subject_id
         WHERE en.class_id = ? AND a.status = 'submitted' AND a.max_score > 0
         ${subjectId ? 'AND e.subject_id = ?' : ''}
         ORDER BY a.submitted_at DESC
         LIMIT 200`,
        subjectId ? [classId, subjectId] : [classId],
      );
      for (const row of cbtRows) {
        const pct = Math.round((Number(row.score) / Number(row.maxScore)) * 100);
        const topic = String(row.examTitle || 'CBT topic');
        rows.push({
          studentId: String(row.studentId),
          topicKey: row.subjectId ? `${row.subjectId}:${topic.toLowerCase()}` : topic.toLowerCase(),
          masteryPct: pct,
          subjectId: row.subjectId || null,
        });
      }
    } catch {
      /* optional */
    }

    try {
      const resultRows: any[] = await runDbQuery(
        ds,
        `SELECT r.student_id as "studentId", sub.id as "subjectId", sub.name as "subjectName",
                AVG(r.total_score) as "avgScore"
         FROM academic_results r
         JOIN student_class_enrollments en ON en.student_id = r.student_id
         LEFT JOIN academic_subjects sub ON sub.id = r.subject_id
         WHERE en.class_id = ? AND r.total_score IS NOT NULL
         ${subjectId ? 'AND r.subject_id = ?' : ''}
         GROUP BY r.student_id, sub.id, sub.name`,
        subjectId ? [classId, subjectId] : [classId],
      );
      for (const row of resultRows) {
        rows.push({
          studentId: String(row.studentId),
          topicKey: row.subjectId
            ? `${row.subjectId}:${String(row.subjectName || 'subject').toLowerCase()}`
            : String(row.subjectName || 'subject').toLowerCase(),
          masteryPct: Math.round(Number(row.avgScore)),
          subjectId: row.subjectId || null,
        });
      }
    } catch {
      /* optional */
    }

    return rows;
  }

  async buildSchemeIngestItems(tenantId: string, classId?: string) {
    const ds = await this.getTenantDs(tenantId);
    const params: any[] = [];
    let sql = `
      SELECT s.id as "schemeId", s.class_id as "classId", s.subject_id as "subjectId",
             sub.name as "subjectName", st.id as "topicId", st.title, st.objective, st.timeframe
      FROM academic_schemes_of_work s
      JOIN academic_scheme_topics st ON st.scheme_id = s.id
      JOIN academic_subjects sub ON sub.id = s.subject_id`;
    if (classId) {
      sql += ` WHERE s.class_id = ?`;
      params.push(classId);
    }
    sql += ` ORDER BY st.order_index ASC LIMIT 100`;
    const rows: any[] = await runDbQuery(ds, sql, params);
    return rows.map((row) => ({
      sourceType: 'scheme_topic',
      sourceId: String(row.topicId),
      title: `${row.subjectName}: ${row.title}`,
      text: [row.title, row.objective, row.timeframe].filter(Boolean).join('\n'),
      visibility: 'staff',
      subjectId: row.subjectId ? String(row.subjectId) : undefined,
      classId: row.classId ? String(row.classId) : undefined,
    }));
  }
}
