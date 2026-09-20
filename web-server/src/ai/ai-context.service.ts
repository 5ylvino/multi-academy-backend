import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

/**
 * Builds a small, read-only snapshot for the assistant. The model receives
 * tenant data explicitly; it is never given a database connection or SQL
 * access. Failed optional queries are ignored so older tenant schemas remain
 * usable.
 */
@Injectable()
export class AiContextService {
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

  private async withTimeout<T>(promise: Promise<T>, fallback: T, ms = 1500): Promise<T> {
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<T>((resolve) => {
      timer = setTimeout(() => resolve(fallback), ms);
    });
    try {
      return await Promise.race([promise, timeout]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  private async query(ds: any, sql: string, params: any[] = []) {
    return this.withTimeout(runDbQuery(ds, sql, params), [], 1200).catch(() => []);
  }

  async build(tenantId: string): Promise<string> {
    const ds = await this.withTimeout(this.getTenantDs(tenantId), null, 2500);
    if (!ds) return JSON.stringify({ unavailable: 'School context is temporarily unavailable.' });
    const now = new Date().toISOString();
    const until = new Date(Date.now() + 30 * 86400000).toISOString();
    const [academicOn, feesOn, calendarOn, announcementsOn] = await Promise.all([
      this.withTimeout(this.flags.resolve(tenantId, 'academic.sessions_terms'), false),
      this.withTimeout(this.flags.resolve(tenantId, 'fees.structures'), false),
      this.withTimeout(this.flags.resolve(tenantId, 'comms.calendar'), false),
      this.withTimeout(this.flags.resolve(tenantId, 'comms.announcements'), false),
    ]);
    const [performanceOn, riskOn, timetableOn] = await Promise.all([
      this.withTimeout(this.flags.resolve(tenantId, 'ai.performance_detection'), false),
      this.withTimeout(this.flags.resolve(tenantId, 'ai.risk_analytics'), false),
      this.withTimeout(this.flags.resolve(tenantId, 'ai.timetable_solver'), false),
    ]);

    const [session, term, classes, fees, events, announcements, performance, risk, timetable] =
      await Promise.all([
      academicOn
        ? this.query(
        ds,
        `SELECT name, start_date as "startDate", end_date as "endDate"
         FROM academic_sessions WHERE is_current = ? LIMIT 1`,
        [true],
      )
        : [],
      academicOn
        ? this.query(
        ds,
        `SELECT name, code, start_date as "startDate", end_date as "endDate"
         FROM academic_terms WHERE is_current = ? LIMIT 1`,
        [true],
      )
        : [],
      academicOn
        ? this.query(
        ds,
        `SELECT name, code, school_level as "schoolLevel"
         FROM academic_classes WHERE is_active = ? ORDER BY name LIMIT 100`,
        [true],
      )
        : [],
      feesOn
        ? this.query(
        ds,
        `SELECT name, school_level as "schoolLevel", amount, term, due_date as "dueDate"
         FROM financial_fee_structures WHERE is_active = ? ORDER BY school_level, name LIMIT 100`,
        [true],
      )
        : [],
      calendarOn
        ? this.query(
        ds,
        `SELECT title, description, event_type as "eventType", starts_at as "startsAt",
                ends_at as "endsAt", audience, location
         FROM school_calendar_events
         WHERE starts_at >= ? AND starts_at <= ?
         ORDER BY starts_at LIMIT 100`,
        [now, until],
      )
        : [],
      announcementsOn
        ? this.query(
        ds,
        `SELECT title, body, audience, published_at as "publishedAt"
         FROM announcements WHERE is_active = ? ORDER BY created_at DESC LIMIT 20`,
        [true],
      )
        : [],
      performanceOn
        ? this.query(
            ds,
            `SELECT COUNT(*) as "resultCount",
                    AVG(total_score) as "averageScore",
                    SUM(CASE WHEN total_score < 50 THEN 1 ELSE 0 END) as "belowPassMark"
             FROM academic_results WHERE total_score IS NOT NULL`,
          )
        : [],
      riskOn
        ? this.query(
            ds,
            `SELECT COUNT(*) as "recentResultCount"
             FROM academic_results
             WHERE created_at >= ? AND total_score IS NOT NULL`,
            [new Date(Date.now() - 60 * 86400000).toISOString()],
          )
        : [],
      timetableOn
        ? this.query(
            ds,
            `SELECT class_id as "classId", subject_id as "subjectId", teacher_id as "teacherId",
                    day_of_week as "dayOfWeek", start_time as "startTime", end_time as "endTime",
                    room
             FROM timetable_slots ORDER BY day_of_week, start_time LIMIT 300`,
          )
        : [],
    ]);

    return JSON.stringify(
      {
        currentSession: session[0] ?? null,
        currentTerm: term[0] ?? null,
        activeClasses: classes,
        activeFeeStructures: fees,
        upcomingCalendarEvents: events,
        recentAnnouncements: announcements,
        aiPerformance: { performanceSignals: performance, riskSummary: risk },
        aiTools: { timetableSlots: timetable, essayGrading: 'Uses the essay and rubric supplied in the current request.' },
      },
      (_key, value) => (typeof value === 'string' ? value.slice(0, 1000) : value),
    );
  }
}
