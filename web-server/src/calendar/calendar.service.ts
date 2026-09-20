import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

@Injectable()
export class CalendarService {
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

  private async ensureTables(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS school_calendar_events (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        description text NULL,
        event_type varchar(64) NOT NULL DEFAULT 'general',
        starts_at TIMESTAMP NOT NULL,
        ends_at TIMESTAMP NULL,
        all_day boolean NOT NULL DEFAULT false,
        audience varchar(32) NOT NULL DEFAULT 'all',
        audience_ref varchar(64) NULL,
        location varchar(255) NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(
      `ALTER TABLE school_calendar_events ADD COLUMN IF NOT EXISTS audience_ref varchar(64) NULL`,
    );
  }

  async list(tenantId: string, from?: string, to?: string, userId?: string) {
    await this.flags.assertEnabled(tenantId, 'comms.calendar');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    let sql = `
      SELECT id, title, description, event_type as "eventType", starts_at as "startsAt",
             ends_at as "endsAt", all_day as "allDay", audience,
             audience_ref as "audienceRef", location,
             created_by as "createdBy", created_at as "createdAt"
      FROM school_calendar_events WHERE 1=1`;
    const params: any[] = [];
    if (from) {
      sql += ` AND starts_at >= ?`;
      params.push(from);
    }
    if (to) {
      sql += ` AND starts_at <= ?`;
      params.push(to);
    }
    sql += ` ORDER BY starts_at ASC LIMIT 500`;
    const events = await runDbQuery(ds, sql, params);
    if (!userId) return events;
    const users: any[] = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const roles = getRoles(users[0]);
    const visible: any[] = [];
    for (const event of events) {
      if (!canViewCalendarAudience(event.audience, roles)) continue;
      if (
        (event.audience === 'class' || event.audience === 'subject') &&
        !(await canViewRelationalAudience(ds, userId, event.audience, event.audienceRef))
      ) {
        continue;
      }
      visible.push(event);
    }
    return visible;
  }

  async create(
    tenantId: string,
    userId: string,
    body: {
      title: string;
      description?: string;
      eventType?: string;
      startsAt: string;
      endsAt?: string;
      allDay?: boolean;
      audience?: string;
      audienceRef?: string;
      location?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.calendar');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const audience = body.audience || 'all';
    if (['class', 'subject'].includes(audience) && !body.audienceRef?.trim()) {
      throw new NotFoundException('A class or subject target is required');
    }
    const id = randomToken('evt');
    await runDbQuery(
      ds,
      `INSERT INTO school_calendar_events
        (id, title, description, event_type, starts_at, ends_at, all_day, audience, audience_ref, location, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        body.title,
        body.description || null,
        body.eventType || 'general',
        body.startsAt,
        body.endsAt || null,
        body.allDay === true,
        audience,
        body.audienceRef?.trim() || null,
        body.location || null,
        userId,
      ],
    );
    return { id };
  }

  async remove(tenantId: string, id: string) {
    await this.flags.assertEnabled(tenantId, 'comms.calendar');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(ds, `DELETE FROM school_calendar_events WHERE id = ?`, [id]);
    return { id, deleted: true };
  }
}

export function canViewCalendarAudience(audience: string, roles: string[]): boolean {
  const normalizedAudience = String(audience || 'all').toLowerCase();
  if (normalizedAudience === 'all') return true;
  const normalizedRoles = roles.map((role) => String(role).toLowerCase());
  if (normalizedRoles.some((role) =>
    ['director', 'school_admin', 'it_admin', 'administrative_staff'].includes(role),
  )) return true;
  if (normalizedAudience === 'parents') return normalizedRoles.includes('parent');
  if (normalizedAudience === 'students') return normalizedRoles.includes('student');
  if (normalizedAudience === 'staff') {
    return normalizedRoles.some((role) =>
      !['parent', 'student'].includes(role),
    );
  }
  if (normalizedAudience === 'class' || normalizedAudience === 'subject') {
    return roles.some((role) => !['parent', 'student'].includes(String(role).toLowerCase()));
  }
  return false;
}

async function canViewRelationalAudience(
  ds: any,
  userId: string,
  audience: string,
  audienceRef?: string,
): Promise<boolean> {
  if (!audienceRef) return false;
  try {
    if (audience === 'class') {
      const rows = await runDbQuery(
        ds,
        `SELECT 1 FROM academic_class_teacher_links WHERE teacher_id = ? AND class_id = ?
         UNION SELECT 1 FROM student_class_enrollments WHERE student_id = ? AND class_id = ?
         UNION SELECT 1 FROM parent_student_links p JOIN student_class_enrollments e ON e.student_id = p.student_id
           WHERE p.parent_id = ? AND e.class_id = ? LIMIT 1`,
        [userId, audienceRef, userId, audienceRef, userId, audienceRef],
      );
      return rows.length > 0;
    }
    const rows = await runDbQuery(
      ds,
      `SELECT 1 FROM academic_subject_teacher_links WHERE teacher_id = ? AND subject_id = ?
       UNION SELECT 1 FROM academic_results WHERE student_id = ? AND subject_id = ?
       UNION SELECT 1 FROM parent_student_links p JOIN academic_results r ON r.student_id = p.student_id
         WHERE p.parent_id = ? AND r.subject_id = ? LIMIT 1`,
      [userId, audienceRef, userId, audienceRef, userId, audienceRef],
    );
    return rows.length > 0;
  } catch {
    return false;
  }
}

function getRoles(row: any): string[] {
  if (!row) return [];
  if (Array.isArray(row.roles)) return row.roles.map(String);
  if (typeof row.roles === 'string') {
    try {
      const parsed = JSON.parse(row.roles);
      if (Array.isArray(parsed)) return parsed.map(String);
    } catch {
      // Fall through to the legacy role column.
    }
  }
  return row.role ? [String(row.role)] : [];
}
