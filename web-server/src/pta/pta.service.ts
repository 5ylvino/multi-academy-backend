import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';

@Injectable()
export class PtaService {
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

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS pta_events (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        agenda text NULL,
        starts_at TIMESTAMP NOT NULL,
        location varchar(255) NULL,
        minutes text NULL,
        attendance_count int NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS pta_rsvps (
        id varchar(64) PRIMARY KEY,
        event_id varchar(64) NOT NULL,
        user_id varchar(64) NOT NULL,
        status varchar(32) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  async list(tenantId: string, userId?: string) {
    await this.flags.assertEnabled(tenantId, 'comms.pta');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const events: any[] = await runDbQuery(
      ds,
      `SELECT id, title, agenda, starts_at as "startsAt", location, minutes,
              attendance_count as "attendanceCount", created_at as "createdAt"
       FROM pta_events ORDER BY starts_at DESC LIMIT 100`,
      [],
    );
    const rsvps: any[] = await runDbQuery(
      ds,
      `SELECT event_id as "eventId", status, COUNT(*)::int as count
       FROM pta_rsvps GROUP BY event_id, status`,
      [],
    );
    const ownRsvps: any[] = userId
      ? await runDbQuery(
          ds,
          `SELECT event_id as "eventId", status
           FROM pta_rsvps WHERE user_id = ?`,
          [userId],
        )
      : [];
    return events.map((event) => ({
      ...event,
      rsvps: rsvps
        .filter((row) => row.eventId === event.id)
        .map((row) => ({ status: row.status, count: row.count })),
      myRsvp: ownRsvps.find((row) => row.eventId === event.id)?.status || null,
    }));
  }

  async create(
    tenantId: string,
    actorId: string,
    body: { title: string; agenda?: string; startsAt: string; location?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.pta');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const id = randomToken('pta');
    await runDbQuery(
      ds,
      `INSERT INTO pta_events (id, title, agenda, starts_at, location, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, NOW())`,
      [id, body.title, body.agenda || null, body.startsAt, body.location || null, actorId],
    );
    return { id };
  }

  async rsvp(tenantId: string, userId: string, eventId: string, status: string) {
    await this.flags.assertEnabled(tenantId, 'comms.pta');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const events = await runDbQuery(ds, `SELECT id FROM pta_events WHERE id = ? LIMIT 1`, [eventId]);
    if (!events.length) throw new NotFoundException('Event not found');
    const existing = await runDbQuery(
      ds,
      `SELECT id FROM pta_rsvps WHERE event_id = ? AND user_id = ? LIMIT 1`,
      [eventId, userId],
    );
    const normalized = ['yes', 'no', 'maybe'].includes(String(status).toLowerCase())
      ? String(status).toLowerCase()
      : 'yes';
    if (existing.length) {
      await runDbQuery(ds, `UPDATE pta_rsvps SET status = ? WHERE id = ?`, [
        normalized,
        existing[0].id,
      ]);
    } else {
      await runDbQuery(
        ds,
        `INSERT INTO pta_rsvps (id, event_id, user_id, status, created_at) VALUES (?, ?, ?, ?, NOW())`,
        [randomToken('rsv'), eventId, userId, normalized],
      );
    }
    return { eventId, status: normalized };
  }

  async getMinutes(tenantId: string, eventId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows = await runDbQuery(
      ds,
      `SELECT minutes, attendance_count as "attendanceCount" FROM pta_events WHERE id = ? LIMIT 1`,
      [eventId],
    );
    if (!rows.length) throw new NotFoundException('Event not found');
    return rows[0];
  }

  async saveMinutes(
    tenantId: string,
    actorId: string,
    eventId: string,
    body: { minutes?: string; attendanceCount?: number },
  ) {
    void actorId;
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await runDbQuery(
      ds,
      `UPDATE pta_events SET minutes = ?, attendance_count = ? WHERE id = ?`,
      [body.minutes || null, body.attendanceCount ?? null, eventId],
    );
    return { eventId };
  }
}
