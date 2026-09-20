import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../../control-plane/control-plane.service';
import { TenantConnectionService } from '../../database/tenant-connection.service';
import { randomToken } from '../../common/utils/id.util';
import { runDbQuery } from '../../database/db-driver.util';
import { FeatureFlagService } from '../../platform-config/feature-flag.service';
import { NotificationsService } from '../../notifications/notifications.service';
import { SafeguardingService } from '../../safeguarding/safeguarding.service';
import { parseRoleList } from '../../common/auth/teacher-scope.util';

@Injectable()
export class GateService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly notifications: NotificationsService,
    private readonly safeguarding: SafeguardingService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS gate_pickup_tokens (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        guardian_name varchar(255) NULL,
        token_code varchar(32) NOT NULL UNIQUE,
        valid_until TIMESTAMP NOT NULL,
        used_at TIMESTAMP NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async list(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'ops.gate_security');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", guardian_name as "guardianName",
              token_code as "tokenCode", valid_until as "validUntil",
              used_at as "usedAt", created_by as "createdBy", created_at as "createdAt"
       FROM gate_pickup_tokens ORDER BY created_at DESC LIMIT 200`,
      [],
    );
  }

  async issueToken(
    tenantId: string,
    userId: string,
    body: { studentId: string; guardianName?: string; validHours?: number },
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.gate_security');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const actor = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const roles = parseRoleList(actor[0]?.roles).concat(
      actor[0]?.role ? [String(actor[0].role).toLowerCase()] : [],
    );
    if (roles.includes('guard')) {
      throw new NotFoundException('Guards may redeem pickup codes only');
    }
    if (roles.includes('parent')) {
      const linked = await runDbQuery(
        ds,
        `SELECT 1 FROM parent_student_links WHERE parent_id = ? AND student_id = ? LIMIT 1`,
        [userId, body.studentId],
      );
      if (!linked.length) throw new NotFoundException('Student is not linked to this parent');
    }
    const id = randomToken('gpt');
    const tokenCode = String(Math.floor(100000 + Math.random() * 900000));
    const hours = Math.min(Math.max(Number(body.validHours ?? 4), 1), 72);
    const validUntil = new Date(Date.now() + hours * 3600000).toISOString();
    await runDbQuery(
      ds,
      `INSERT INTO gate_pickup_tokens
        (id, student_id, guardian_name, token_code, valid_until, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, NOW())`,
      [id, body.studentId, body.guardianName || null, tokenCode, validUntil, userId],
    );
    return { id, tokenCode, validUntil };
  }

  async redeem(tenantId: string, userId: string, tokenCode: string) {
    await this.flags.assertEnabled(tenantId, 'ops.gate_security');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const actor = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const roles = parseRoleList(actor[0]?.roles).concat(
      actor[0]?.role ? [String(actor[0].role).toLowerCase()] : [],
    );
    if (
      !roles.some((role) =>
        ['guard', 'director', 'school_admin', 'it_admin', 'principal', 'head_teacher'].includes(role),
      )
    ) {
      throw new NotFoundException('You are not authorized to redeem pickup codes');
    }

    const pass = await this.safeguarding.redeemPass(tenantId, userId, tokenCode);
    if (pass) return pass;

    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", guardian_name as "guardianName",
              used_at as "usedAt", valid_until as "validUntil"
       FROM gate_pickup_tokens WHERE token_code = ? LIMIT 1`,
      [tokenCode],
    );
    if (!rows.length) {
      await this.notifications.create({
        tenantId,
        userId,
        title: 'Unknown exit pass',
        message: `Code ${tokenCode} was not recognized.`,
        type: 'warning',
        href: '/dashboard/ops/gate',
      });
      throw new NotFoundException('Token not found');
    }
    const row = rows[0];
    if (row.usedAt) {
      return { id: row.id, studentId: row.studentId, status: 'already_used' };
    }
    if (new Date(row.validUntil) < new Date()) {
      return { id: row.id, studentId: row.studentId, status: 'expired' };
    }
    await runDbQuery(ds, `UPDATE gate_pickup_tokens SET used_at = NOW() WHERE id = ?`, [row.id]);
    const parents = await runDbQuery(
      ds,
      `SELECT parent_id as "parentId" FROM parent_student_links WHERE student_id = ?`,
      [row.studentId],
    );
    for (const parent of parents as any[]) {
      await this.notifications.create({
        tenantId,
        userId: parent.parentId || parent.parentid,
        title: 'Student left school',
        message: `${row.guardianName || 'An authorized adult'} collected the student.`,
        type: 'warning',
        href: '/dashboard/parent',
      });
    }
    return { id: row.id, studentId: row.studentId, status: 'redeemed' };
  }
}
