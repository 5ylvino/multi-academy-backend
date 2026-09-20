import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { CommsService } from '../platform-config/comms.service';

@Injectable()
export class EmergencyBroadcastService {
  private readonly logger = new Logger(EmergencyBroadcastService.name);

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly comms: CommsService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS emergency_broadcasts (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        message text NOT NULL,
        channels varchar(128) NOT NULL DEFAULT 'sms,email',
        audience varchar(64) NOT NULL DEFAULT 'all_parents',
        created_by varchar(64) NULL,
        sent_count int NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async list(tenantId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'comms.emergency_broadcast');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId, false);
    return runDbQuery(
      ds,
      `SELECT id, title, message, channels, audience, sent_count as "sentCount",
              created_by as "createdBy", created_at as "createdAt"
       FROM emergency_broadcasts ORDER BY created_at DESC LIMIT 100`,
      [],
    );
  }

  async broadcast(
    tenantId: string,
    userId: string,
    body: {
      title: string;
      message: string;
      channels?: ('sms' | 'email')[];
      audience?: string;
      /** Optional explicit recipients override audience resolution */
      recipients?: { phone?: string; email?: string }[];
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.emergency_broadcast');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, userId, true);
    if (!body.title?.trim() || !body.message?.trim()) {
      throw new NotFoundException('Emergency title and message are required');
    }

    const channels = body.channels?.length ? body.channels : ['sms', 'email'];
    const recipients = body.recipients?.length
      ? body.recipients
      : await this.resolveRecipients(ds, body.audience || 'all_parents');

    let sent = 0;
    for (const r of recipients) {
      if (channels.includes('sms') && r.phone) {
        try {
          await this.comms.sendSms(tenantId, {
            to: r.phone,
            message: `[EMERGENCY] ${body.title}: ${body.message}`,
          });
          sent += 1;
        } catch (err) {
          this.logger.warn(`Emergency SMS failed: ${err instanceof Error ? err.message : err}`);
        }
      }
      if (channels.includes('email') && r.email) {
        try {
          await this.comms.sendEmail(tenantId, {
            to: r.email,
            subject: `[EMERGENCY] ${body.title}`,
            text: body.message,
            html: `<p><strong>EMERGENCY:</strong> ${body.message}</p>`,
          });
          sent += 1;
        } catch (err) {
          this.logger.warn(`Emergency email failed: ${err instanceof Error ? err.message : err}`);
        }
      }
    }

    const id = randomToken('emrg');
    await runDbQuery(
      ds,
      `INSERT INTO emergency_broadcasts (id, title, message, channels, audience, created_by, sent_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      [
        id,
        body.title,
        body.message,
        channels.join(','),
        body.audience || 'all_parents',
        userId,
        sent,
      ],
    );
    return {
      id,
      sentCount: sent,
      channels,
      recipientCount: recipients.length,
      note:
        sent === 0
          ? 'Broadcast recorded; no deliverable phone/email found for the selected audience.'
          : undefined,
    };
  }

  /** Resolve phones/emails from users table based on audience selector. */
  private async resolveRecipients(
    ds: any,
    audience: string,
  ): Promise<{ phone?: string; email?: string }[]> {
    let sql = '';
    switch (audience) {
      case 'all_staff':
        sql = `SELECT phone, email FROM users
               WHERE is_active = true
                 AND (roles LIKE '%"teacher"%' OR roles LIKE '%"staff"%'
                      OR roles LIKE '%"admin"%' OR roles LIKE '%"school_admin"%'
                      OR roles LIKE '%"director"%')
                 AND (phone IS NOT NULL OR email IS NOT NULL)`;
        break;
      case 'all_students':
        sql = `SELECT phone, email FROM users
               WHERE is_active = true AND roles LIKE '%"student"%'
                 AND (phone IS NOT NULL OR email IS NOT NULL)`;
        break;
      case 'all_users':
        sql = `SELECT phone, email FROM users
               WHERE is_active = true AND (phone IS NOT NULL OR email IS NOT NULL)`;
        break;
      case 'all_parents':
      default:
        sql = `SELECT phone, email FROM users
               WHERE is_active = true AND roles LIKE '%"parent"%'
                 AND (phone IS NOT NULL OR email IS NOT NULL)`;
        break;
    }
    const rows: any[] = await runDbQuery(ds, sql, []);
    const seen = new Set<string>();
    const out: { phone?: string; email?: string }[] = [];
    for (const row of rows || []) {
      const key = `${row.email || ''}|${row.phone || ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (row.phone || row.email) {
        out.push({ phone: row.phone || undefined, email: row.email || undefined });
      }
    }
    return out;
  }

  private async assertAccess(ds: any, actorId: string, write: boolean) {
    const rows: any[] = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [actorId]);
    const row = rows[0];
    let roles: unknown[] = row?.role ? [row.role] : [];
    if (row?.roles) {
      try { roles = typeof row.roles === 'string' ? JSON.parse(row.roles) : row.roles; } catch { /* fallback */ }
    }
    const allowed = ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher'];
    if (!Array.isArray(roles) || !roles.some((role) => allowed.includes(String(role).toLowerCase()))) {
      throw new NotFoundException(`Emergency broadcast ${write ? 'sending' : 'access'} is restricted`);
    }
  }
}
