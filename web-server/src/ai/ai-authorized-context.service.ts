import { ForbiddenException, Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import {
  getVisibleStudentIds,
  loadUserRoles,
} from '../common/auth/teacher-scope.util';

export type AiActorScope = {
  userId: string;
  roles: string[];
};

export type AiContextSource = {
  type: string;
  id: string;
  label: string;
  recordCount: number;
  freshness: string;
};

export type AuthorizedAiContext = {
  access: {
    roles: string[];
    visibleStudentIds: string[] | null;
    sensitiveDomains: string[];
    excludedDomains: string[];
  };
  data: Record<string, unknown>;
  sources: AiContextSource[];
  limitations: string[];
};

@Injectable()
export class AiAuthorizedContextService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async query(ds: any, sql: string, params: unknown[] = []) {
    try {
      return await runDbQuery(ds, sql, params);
    } catch {
      // Tenant databases can be on different migration levels. A missing
      // optional domain must be reported as unavailable, never as empty truth.
      return null;
    }
  }

  async assertStudentVisible(
    tenantId: string,
    actorId: string,
    studentId: string,
  ): Promise<void> {
    const ds = await this.getTenantDs(tenantId);
    const visible = await getVisibleStudentIds(ds, actorId);
    if (visible !== null && !visible.includes(String(studentId))) {
      throw new ForbiddenException('Student is outside the actor visibility scope');
    }
  }

  async build(
    tenantId: string,
    actor: AiActorScope,
  ): Promise<AuthorizedAiContext> {
    const ds = await this.getTenantDs(tenantId);
    const loadedRoles = await loadUserRoles(ds, actor.userId);
    const roles = (loadedRoles.length ? loadedRoles : actor.roles).map((role) =>
      role.toLowerCase(),
    );
    const visibleStudentIds = await getVisibleStudentIds(ds, actor.userId);
    const tenantWide = visibleStudentIds === null;
    const sensitiveDomains = [
      ...(['director', 'school_admin', 'it_admin', 'bursar', 'accountant', 'finance'].some(
        (role) => roles.includes(role),
      )
        ? ['finance']
        : []),
      ...(['director', 'school_admin', 'it_admin', 'clinic_staff', 'nurse'].some(
        (role) => roles.includes(role),
      )
        ? ['clinic']
        : []),
      ...(['director', 'school_admin', 'it_admin', 'safeguarding_lead'].some(
        (role) => roles.includes(role),
      )
        ? ['safeguarding']
        : []),
    ];
    const excludedDomains = ['payroll', 'private_messages'];
    const limitations: string[] = [];
    const sources: AiContextSource[] = [];
    const data: Record<string, unknown> = {};
    const now = new Date().toISOString();

    const add = (
      key: string,
      type: string,
      label: string,
      rows: unknown[] | null,
    ) => {
      if (rows === null) {
        limitations.push(`${label} is unavailable for this school database.`);
        return;
      }
      data[key] = rows;
      sources.push({
        type,
        id: `${tenantId}:${key}`,
        label,
        recordCount: rows.length,
        freshness: now,
      });
    };

    const [sessions, terms, classes, calendar, announcements, timetable] =
      await Promise.all([
        this.query(
          ds,
          `SELECT name, start_date as "startDate", end_date as "endDate"
           FROM academic_sessions WHERE is_current = ? LIMIT 1`,
          [true],
        ),
        this.query(
          ds,
          `SELECT name, code, start_date as "startDate", end_date as "endDate"
           FROM academic_terms WHERE is_current = ? LIMIT 1`,
          [true],
        ),
        this.query(
          ds,
          `SELECT id, name, code, school_level as "schoolLevel"
           FROM academic_classes WHERE is_active = ? ORDER BY name LIMIT 200`,
          [true],
        ),
        this.query(
          ds,
          `SELECT id, title, description, event_type as "eventType",
                  starts_at as "startsAt", ends_at as "endsAt", audience, location
           FROM school_calendar_events
           WHERE starts_at >= ? ORDER BY starts_at LIMIT 200`,
          [now],
        ),
        this.query(
          ds,
          `SELECT id, title, body, audience, published_at as "publishedAt"
           FROM announcements WHERE is_active = ? ORDER BY created_at DESC LIMIT 50`,
          [true],
        ),
        tenantWide || roles.includes('teacher') || roles.includes('class_teacher') ||
        roles.includes('subject_teacher')
          ? this.query(
              ds,
              `SELECT id, class_id as "classId", subject_id as "subjectId",
                      teacher_id as "teacherId", day_of_week as "dayOfWeek",
                      start_time as "startTime", end_time as "endTime", room
               FROM timetable_slots ORDER BY day_of_week, start_time LIMIT 500`,
            )
          : Promise.resolve([]),
      ]);
    add('currentSession', 'academic_session', 'Current academic session', sessions);
    add('currentTerm', 'academic_term', 'Current academic term', terms);
    add('activeClasses', 'academic_class', 'Active classes', classes);
    add('upcomingCalendarEvents', 'calendar_event', 'Upcoming calendar events', calendar);
    add('recentAnnouncements', 'announcement', 'Recent announcements', announcements);
    add('timetable', 'timetable_slot', 'Accessible timetable slots', timetable);

    const studentFilter =
      visibleStudentIds && visibleStudentIds.length
        ? ` AND student_id IN (${visibleStudentIds.map(() => '?').join(',')})`
        : visibleStudentIds
          ? ' AND 1 = 0'
          : '';
    const studentParams = visibleStudentIds || [];
    const [results, attendance, enrollments] = await Promise.all([
      this.query(
        ds,
        `SELECT student_id as "studentId", subject_id as "subjectId",
                total_score as "totalScore", grade, term_id as "termId"
         FROM academic_results WHERE total_score IS NOT NULL${studentFilter}
         ORDER BY created_at DESC LIMIT 500`,
        studentParams,
      ),
      this.query(
        ds,
        `SELECT student_id as "studentId", attendance_date as "date",
                status, class_id as "classId"
         FROM attendance_students WHERE 1 = 1${studentFilter}
         ORDER BY attendance_date DESC LIMIT 500`,
        studentParams,
      ),
      this.query(
        ds,
        `SELECT student_id as "studentId", class_id as "classId",
                session_id as "sessionId"
         FROM student_class_enrollments WHERE 1 = 1${studentFilter}
         ORDER BY student_id LIMIT 500`,
        studentParams,
      ),
    ]);
    add('academicResults', 'academic_result', 'Accessible academic results', results);
    add('studentAttendance', 'attendance_record', 'Accessible student attendance', attendance);
    add('studentEnrollments', 'class_enrollment', 'Accessible class enrollments', enrollments);

    if (sensitiveDomains.includes('finance')) {
      const finance = await this.query(
        ds,
        `SELECT id, name, school_level as "schoolLevel", amount, term,
                due_date as "dueDate"
         FROM financial_fee_structures WHERE is_active = ? ORDER BY school_level, name LIMIT 200`,
        [true],
      );
      add('feeStructures', 'fee_structure', 'Authorized fee structures', finance);
    } else if (roles.includes('parent') || roles.includes('student')) {
      limitations.push('Detailed finance records are restricted for this role.');
    }

    if (!tenantWide && visibleStudentIds === null) {
      limitations.push('Student-specific records are limited to the actor scope.');
    }
    if (excludedDomains.length) {
      limitations.push(`Excluded domains: ${excludedDomains.join(', ')}.`);
    }

    return { access: { roles, visibleStudentIds, sensitiveDomains, excludedDomains }, data, sources, limitations };
  }
}
