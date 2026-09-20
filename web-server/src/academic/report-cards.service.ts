import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { buildReportPdf } from '../reports/report-file.util';

@Injectable()
export class ReportCardsService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  async getData(tenantId: string, studentId: string, termId: string) {
    await this.flags.assertEnabled(tenantId, 'academic.report_cards');
    const ds = await this.getTenantDs(tenantId);
    const students: any[] = await runDbQuery(
      ds,
      `SELECT id, full_name as "fullName", email FROM users WHERE id = ? LIMIT 1`,
      [studentId],
    );
    const student = students?.[0];
    if (!student) throw new NotFoundException('Student not found');

    const results: any[] = await runDbQuery(
      ds,
      `SELECT r.subject_id as "subjectId", s.name as "subjectName",
              r.ca_score as "caScore", r.exam_score as "examScore",
              r.total_score as "totalScore", r.grade,
              r.approval_comment as "approvalComment", r.class_id as "classId"
       FROM academic_results r
       LEFT JOIN academic_subjects s ON s.id = r.subject_id
       WHERE r.student_id = ? AND r.term_id = ? AND r.status = 'approved'
       ORDER BY s.name ASC`,
      [studentId, termId],
    );

    let position: number | null = null;
    let classSize: number | null = null;
    const rankingOn = await this.flags.resolve(
      tenantId,
      'academic.term_ranking',
    );
    if (rankingOn && results[0]?.classId) {
      const ranks: any[] = await runDbQuery(
        ds,
        `SELECT student_id as "studentId", AVG(total_score) as avg
         FROM academic_results
         WHERE class_id = ? AND term_id = ? AND status = 'approved' AND total_score IS NOT NULL
         GROUP BY student_id
         ORDER BY avg DESC`,
        [results[0].classId, termId],
      );
      classSize = ranks.length;
      const idx = ranks.findIndex((r) => r.studentId === studentId);
      if (idx >= 0) {
        const score = Number(ranks[idx].avg || 0);
        position =
          ranks
            .slice(0, idx)
            .filter((rank) => Number(rank.avg || 0) > score).length + 1;
      }
    }

    const avg =
      results.length > 0
        ? results.reduce((s, r) => s + Number(r.totalScore || 0), 0) /
          results.length
        : 0;

    return {
      student,
      termId,
      subjects: results,
      average: Math.round(avg * 100) / 100,
      position,
      classSize,
      principalComment:
        results.find((result) => result.approvalComment)?.approvalComment || null,
    };
  }

  async pdf(
    tenantId: string,
    studentId: string,
    termId: string,
  ): Promise<Buffer> {
    const data = await this.getData(tenantId, studentId, termId);
    return buildReportPdf({
      report: {
        title: `Report Card — ${data.student.fullName || studentId}`,
        type: 'report_card',
        category: 'academic',
        scope: `term:${termId}`,
        period: termId,
        generatedDate: new Date().toISOString(),
        status: 'final',
      },
      columns: ['Subject', 'CA', 'Exam', 'Total', 'Grade'],
      rows: data.subjects.map((s) => ({
        Subject: s.subjectName || s.subjectId,
        CA: s.caScore,
        Exam: s.examScore,
        Total: s.totalScore,
        Grade: s.grade,
      })),
      summary: {
        Average: data.average,
        Position: data.position ?? '—',
        "Principal's comment": data.principalComment || '—',
      },
    });
  }

  async transcriptData(tenantId: string, studentId: string) {
    const ds = await this.getTenantDs(tenantId);
    const students: any[] = await runDbQuery(
      ds,
      `SELECT id, name as "fullName", email, admission_no as "admissionNo"
       FROM users WHERE id = ? LIMIT 1`,
      [studentId],
    );
    if (!students?.[0]) throw new NotFoundException('Student not found');
    const organizations: any[] = await runDbQuery(
      ds,
      `SELECT name, address, city, state, country, phone,
              brand_color as "brandColor"
       FROM business_org LIMIT 1`,
    );
    const history = await runDbQuery(
      ds,
      `SELECT agr.id, agr.class_id as "classId",
              agr.student_class_name as "className",
              agr.term_id as "termId", trm.name as "termName",
              ses.name as "sessionName",
              agr.student_subject_records as "studentSubjectRecords"
       FROM academic_generated_results agr
       LEFT JOIN academic_terms trm ON trm.id = agr.term_id
       LEFT JOIN academic_sessions ses ON ses.id = agr.session_id
       WHERE agr.student_id = ? AND agr.release_status = 'approved'
       ORDER BY ses.start_date NULLS LAST, trm.start_date NULLS LAST`,
      [studentId],
    );
    const historyRows = history.flatMap((result: any) => {
      const records = typeof result.studentSubjectRecords === 'string'
        ? JSON.parse(result.studentSubjectRecords)
        : result.studentSubjectRecords || [];
      return (Array.isArray(records) ? records : []).map((subject: any) => ({
        ...result,
        subjectName: subject.subject || subject.subjectName || subject.subjectId,
        caScore: subject.caScore,
        examScore: subject.examScore,
        totalScore: subject.totalScore,
        grade: subject.grade_title || subject.grade,
      }));
    });
    return { student: students[0], history: historyRows, organization: organizations[0] || null };
  }

  async transcriptPdf(tenantId: string, studentId: string): Promise<Buffer> {
    const data = await this.transcriptData(tenantId, studentId);
    const generatedAt = new Intl.DateTimeFormat('en-NG', {
      timeZone: 'Africa/Lagos',
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    }).format(new Date());
    return buildReportPdf({
      report: {
        title: `Academic Transcript — ${data.student.fullName || studentId}`,
        type: 'transcript',
        category: 'academic',
        scope: data.student.admissionNo || '—',
        scopeLabel: 'Admission No',
        period: 'cumulative',
        generatedDate: `${generatedAt} WAT`,
        status: 'final',
      },
      organization: data.organization,
      columns: [
        'Session',
        'Term',
        'Class',
        'Subject',
        'CA',
        'Exam',
        'Total',
        'Grade',
      ],
      rows: data.history.map((r: any) => ({
        Session: r.sessionName || '—',
        Term: r.termName || r.termId,
        Class: r.className || r.classId,
        Subject: r.subjectName || r.subjectId,
        CA: r.caScore,
        Exam: r.examScore,
        Total: r.totalScore,
        Grade: r.grade,
      })),
      groupBy: ['Session', 'Term', 'Class'],
      footerText: 'Signature, Stamp & Date',
    });
  }
}
