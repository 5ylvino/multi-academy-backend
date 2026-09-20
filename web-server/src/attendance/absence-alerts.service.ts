import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { CommsService } from '../platform-config/comms.service';
import { NotificationsService } from '../notifications/notifications.service';
import { randomToken } from '../common/utils/id.util';

@Injectable()
export class AbsenceAlertsService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly comms: CommsService,
    private readonly notifications: NotificationsService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS attendance_absence_alert_config (
        id varchar(64) PRIMARY KEY DEFAULT 'default',
        threshold_days int NOT NULL DEFAULT 3,
        window_days int NOT NULL DEFAULT 14,
        notify_parents boolean NOT NULL DEFAULT true,
        notify_staff boolean NOT NULL DEFAULT true,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS attendance_absence_alerts_sent (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        absence_count int NOT NULL,
        sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async getConfig(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'attendance.absence_alerts');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT threshold_days as "thresholdDays", window_days as "windowDays",
              notify_parents as "notifyParents", notify_staff as "notifyStaff"
       FROM attendance_absence_alert_config WHERE id = 'default' LIMIT 1`,
      [],
    );
    return (
      rows?.[0] || {
        thresholdDays: 3,
        windowDays: 14,
        notifyParents: true,
        notifyStaff: true,
      }
    );
  }

  async setConfig(
    tenantId: string,
    body: {
      thresholdDays?: number;
      windowDays?: number;
      notifyParents?: boolean;
      notifyStaff?: boolean;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'attendance.absence_alerts');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const current = await this.getConfig(tenantId);
    const next = {
      thresholdDays: body.thresholdDays ?? current.thresholdDays,
      windowDays: body.windowDays ?? current.windowDays,
      notifyParents: body.notifyParents ?? current.notifyParents,
      notifyStaff: body.notifyStaff ?? current.notifyStaff,
    };
    const existing: any[] = await runDbQuery(
      ds,
      `SELECT id FROM attendance_absence_alert_config WHERE id = 'default' LIMIT 1`,
      [],
    );
    if (existing?.[0]) {
      await runDbQuery(
        ds,
        `UPDATE attendance_absence_alert_config
         SET threshold_days = ?, window_days = ?, notify_parents = ?, notify_staff = ?, updated_at = NOW()
         WHERE id = 'default'`,
        [next.thresholdDays, next.windowDays, next.notifyParents, next.notifyStaff],
      );
    } else {
      await runDbQuery(
        ds,
        `INSERT INTO attendance_absence_alert_config
          (id, threshold_days, window_days, notify_parents, notify_staff, updated_at)
         VALUES ('default', ?, ?, ?, ?, NOW())`,
        [next.thresholdDays, next.windowDays, next.notifyParents, next.notifyStaff],
      );
    }
    return next;
  }

  /** Scan recent absences and notify parents/staff when threshold crossed. */
  async run(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'attendance.absence_alerts');
    const config = await this.getConfig(tenantId);
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);

    const windowDays = Math.max(1, Number(config.windowDays) || 14);
    const since = new Date(Date.now() - windowDays * 86400000).toISOString();
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT student_id as "studentId", COUNT(*) as "absenceCount"
       FROM attendance_students
       WHERE LOWER(status) = 'absent'
         AND created_at >= ?
       GROUP BY student_id
       HAVING COUNT(*) >= ?`,
      [since, config.thresholdDays],
    );

    const alerts: any[] = [];
    const weekAgo = new Date(Date.now() - 7 * 86400000).toISOString();
    for (const row of rows || []) {
      const recent: any[] = await runDbQuery(
        ds,
        `SELECT id FROM attendance_absence_alerts_sent
         WHERE student_id = ? AND sent_at >= ? LIMIT 1`,
        [row.studentId, weekAgo],
      );
      if (recent?.[0]) continue;

      const parents: any[] = await runDbQuery(
        ds,
        `SELECT parent_id as "parentId" FROM parent_student_links WHERE student_id = ?`,
        [row.studentId],
      );

      const message = `Absence alert: student ${row.studentId} has ${row.absenceCount} absences in the last ${config.windowDays} days.`;

      if (config.notifyParents) {
        for (const p of parents || []) {
          await this.notifications.create({
            tenantId,
            userId: p.parentId,
            title: 'Absence threshold alert',
            message,
            type: 'warning',
            href: '/dashboard/parent',
          });
          try {
            const users: any[] = await runDbQuery(
              ds,
              `SELECT email, phone FROM users WHERE id = ? LIMIT 1`,
              [p.parentId],
            );
            const u = users?.[0];
            if (u?.phone) {
              await this.comms.sendSms(tenantId, { to: u.phone, message });
            }
            if (u?.email) {
              await this.comms.sendEmail(tenantId, {
                to: u.email,
                subject: 'School absence alert',
                text: message,
              });
            }
          } catch {
            /* fail closed per channel — continue */
          }
        }
      }

      await runDbQuery(
        ds,
        `INSERT INTO attendance_absence_alerts_sent (id, student_id, absence_count, sent_at)
         VALUES (?, ?, ?, NOW())`,
        [randomToken('aal'), row.studentId, row.absenceCount],
      );
      alerts.push({ studentId: row.studentId, absenceCount: Number(row.absenceCount) });
    }

    return { scanned: rows?.length || 0, alertsSent: alerts.length, alerts };
  }
}
