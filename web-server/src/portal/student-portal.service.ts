import {
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { AcademicService } from '../academic/academic.service';
import { hasPortalRole } from './portal-role.util';
import { PortalReadServiceClient } from './portal-read-service.client';

@Injectable()
export class StudentPortalService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly academic: AcademicService,
    private readonly portalRead: PortalReadServiceClient,
  ) {}

  private async getDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async assertStudent(tenantId: string, userId: string) {
    await this.flags.assertEnabled(tenantId, 'portal.student');
    const ds = await this.getDs(tenantId);
    const rows = await runDbQuery(
      ds,
      `SELECT id, role, roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    if (!rows.length) throw new NotFoundException('User not found');
    // Student data is self-scoped; admin permissions alone must not expose
    // the student portal identity endpoints.
    const allowed = hasPortalRole(rows[0].role, rows[0].roles, 'student');
    if (!allowed) {
      throw new ForbiddenException('Student portal requires student role');
    }
  }

  /** Aggregated student portal home — delegates to portal-read when configured. */
  async getOverview(tenantId: string, studentId: string) {
    if (this.portalRead.isEnabled()) {
      return this.portalRead.get(
        'student/overview',
        tenantId,
        { userId: studentId, roles: ['student'] },
        ['portal.student'],
      );
    }
    return this.getOverviewLocal(tenantId, studentId);
  }

  /** Aggregated student portal home — results, assignments, and schedule in one call. */
  async getOverviewLocal(tenantId: string, studentId: string) {
    await this.assertStudent(tenantId, studentId);
    const [results, assignments, schedule] = await Promise.all([
      this.fetchResults(tenantId, studentId),
      this.getAssignmentsForStudent(tenantId, studentId),
      this.getScheduleForStudent(tenantId, studentId),
    ]);
    return { results, assignments, schedule };
  }

  async getResults(tenantId: string, studentId: string) {
    await this.assertStudent(tenantId, studentId);
    return this.fetchResults(tenantId, studentId);
  }

  private async fetchResults(tenantId: string, studentId: string) {
    const ds = await this.getDs(tenantId);
    const rows = await runDbQuery(
      ds,
      `SELECT agr.id, agr.session_id as "sessionId", agr.term_id as "termId",
          agr.class_id as "classId", agr.student_name as "studentName",
          agr.student_class_name as "className", agr.admission_no as "admissionNo",
          agr.student_subject_records as "studentSubjectRecords",
          agr.release_status as "releaseStatus", agr.generated_at as "generatedAt",
          ses.name as "sessionName", trm.name as "termName"
       FROM academic_generated_results agr
       LEFT JOIN academic_sessions ses ON ses.id = agr.session_id
       LEFT JOIN academic_terms trm ON trm.id = agr.term_id
       WHERE agr.student_id = ? AND agr.release_status = 'approved'
       ORDER BY ses.start_date DESC NULLS LAST, trm.start_date DESC NULLS LAST`,
      [studentId],
    );
    return rows.map((row: any) => ({
      ...row,
      studentSubjectRecords: typeof row.studentSubjectRecords === 'string'
        ? JSON.parse(row.studentSubjectRecords)
        : row.studentSubjectRecords || [],
    }));
  }

  async getAssignments(tenantId: string, studentId: string) {
    await this.assertStudent(tenantId, studentId);
    return this.getAssignmentsForStudent(tenantId, studentId);
  }

  /**
   * Returns the student-safe assignment view after the caller has performed
   * its own ownership check (the parent portal uses this for a linked ward).
   */
  async getAssignmentsForStudent(tenantId: string, studentId: string) {
    const ds = await this.getDs(tenantId);
    const enrollments: any[] = await runDbQuery(
      ds,
      `SELECT class_id as "classId" FROM student_class_enrollments WHERE student_id = ?`,
      [studentId],
    );
    const classIds = new Set(enrollments.map((e) => e.classId));
    const assignments = await this.academic.listAssignments(tenantId);
    return (assignments as any[]).filter(
      (a) => !a.classId || classIds.has(a.classId),
    );
  }

  /**
   * Weekly schedule built from published timetable slots for the classes the
   * student is enrolled in. Falls back to an empty list (rather than synthetic
   * periods) when the timetable feature is off or nothing has been published.
   */
  async getSchedule(tenantId: string, studentId: string) {
    await this.assertStudent(tenantId, studentId);
    return this.getScheduleForStudent(tenantId, studentId);
  }

  /**
   * Returns the student-safe timetable view after the caller has performed
   * its own ownership check (the parent portal uses this for a linked ward).
   */
  async getScheduleForStudent(tenantId: string, studentId: string) {
    const ds = await this.getDs(tenantId);
    const classes: any[] = await runDbQuery(
      ds,
      `SELECT c.id, c.name, c.code
       FROM student_class_enrollments e
       JOIN academic_classes c ON c.id = e.class_id
       WHERE e.student_id = ?`,
      [studentId],
    );

    if (!classes?.length) {
      return {
        studentId,
        schedule: [],
        note: 'You are not enrolled in any class yet.',
      };
    }

    const timetableOn = await this.flags.resolve(
      tenantId,
      'academic.timetable',
    );
    if (!timetableOn) {
      return {
        studentId,
        schedule: [],
        note: 'The timetable module is not enabled for this school.',
      };
    }

    const classNames = new Map<string, string>(
      classes.map((c) => [c.id, c.name || c.code]),
    );
    const placeholders = classes.map(() => '?').join(', ');
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT t.id, t.class_id as "classId", t.day_of_week as "dayOfWeek",
              t.start_time as "startTime", t.end_time as "endTime", t.room,
              s.name as "subjectName", u.name as "teacherName"
       FROM timetable_slots t
       LEFT JOIN academic_subjects s ON s.id = t.subject_id
       LEFT JOIN users u ON u.id = t.teacher_id
       WHERE t.class_id IN (${placeholders})
       ORDER BY t.day_of_week ASC, t.start_time ASC`,
      classes.map((c) => c.id),
    ).catch(() => [] as any[]);

    const days = [
      'Sunday',
      'Monday',
      'Tuesday',
      'Wednesday',
      'Thursday',
      'Friday',
      'Saturday',
    ];
    const schedule = (rows || []).map((row) => ({
      id: row.id,
      day: days[Number(row.dayOfWeek)] ?? String(row.dayOfWeek),
      dayOfWeek: Number(row.dayOfWeek),
      startTime: row.startTime,
      endTime: row.endTime,
      period: `${row.startTime}–${row.endTime}`,
      subject: row.subjectName || 'Unassigned subject',
      teacher: row.teacherName || null,
      room: row.room || null,
      className: classNames.get(row.classId) || row.classId,
    }));

    return {
      studentId,
      schedule,
      note: schedule.length
        ? undefined
        : 'No timetable has been published for your class yet.',
    };
  }

  async submitAssignment(
    tenantId: string,
    studentId: string,
    assignmentId: string,
    note?: string,
  ) {
    await this.assertStudent(tenantId, studentId);
    const assignments = await this.getAssignments(tenantId, studentId);
    if (!(assignments as any[]).some((a) => a.id === assignmentId)) {
      throw new ForbiddenException(
        'Assignment is not available for this student',
      );
    }
    return this.academic.upsertAssignmentSubmission(tenantId, assignmentId, {
      studentId,
      status: 'submitted',
      note: note || null,
    });
  }
}
