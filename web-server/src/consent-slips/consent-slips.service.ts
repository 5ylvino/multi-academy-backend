import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

@Injectable()
export class ConsentSlipsService {
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
      CREATE TABLE IF NOT EXISTS consent_slips (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        body text NOT NULL,
        slip_type varchar(64) NOT NULL DEFAULT 'permission',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS consent_responses (
        id varchar(64) PRIMARY KEY,
        slip_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        guardian_name varchar(255) NULL,
        status varchar(32) NOT NULL DEFAULT 'pending',
        signed_at TIMESTAMP NULL,
        notes text NULL
      );
    `);
  }

  async list(tenantId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'comms.consent_slips');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertCanRead(ds, actorId);
    return runDbQuery(
      ds,
      `SELECT id, title, body, slip_type as "slipType", created_by as "createdBy",
              created_at as "createdAt" FROM consent_slips ORDER BY created_at DESC LIMIT 200`,
      [],
    );
  }

  async create(
    tenantId: string,
    userId: string,
    body: { title: string; body: string; slipType?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.consent_slips');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertCanManage(ds, userId);
    if (!body.title?.trim() || !body.body?.trim()) {
      throw new NotFoundException('Consent title and body are required');
    }
    const id = randomToken('consent');
    await runDbQuery(
      ds,
      `INSERT INTO consent_slips (id, title, body, slip_type, created_by) VALUES (?, ?, ?, ?, ?)`,
      [id, body.title, body.body, body.slipType || 'permission', userId],
    );
    return { id, title: body.title, slipType: body.slipType || 'permission' };
  }

  async respond(
    tenantId: string,
    actorId: string,
    body: {
      slipId: string;
      studentId: string;
      guardianName?: string;
      status: 'approved' | 'denied';
      notes?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.consent_slips');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertCanRespond(ds, actorId, body.studentId);
    const slips: any[] = await runDbQuery(
      ds,
      `SELECT id FROM consent_slips WHERE id = ?`,
      [body.slipId],
    );
    if (!slips.length) throw new NotFoundException('Consent slip not found');
    const id = randomToken('cresp');
    await runDbQuery(
      ds,
      `INSERT INTO consent_responses (id, slip_id, student_id, guardian_name, status, signed_at, notes)
       VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)`,
      [
        id,
        body.slipId,
        body.studentId,
        body.guardianName || null,
        body.status,
        body.notes || null,
      ],
    );
    return { id, status: body.status };
  }

  private async getRoles(ds: any, actorId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [actorId]);
    const row = rows[0];
    let roles: unknown[] = row?.role ? [row.role] : [];
    if (row?.roles) {
      try { roles = typeof row.roles === 'string' ? JSON.parse(row.roles) : row.roles; } catch { /* fallback */ }
    }
    return Array.isArray(roles) ? roles.map((role) => String(role).toLowerCase()) : [];
  }

  private async assertCanRead(ds: any, actorId: string) {
    const roles = await this.getRoles(ds, actorId);
    if (!roles.some((role) => ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff', 'bursar', 'parent', 'student'].includes(role))) {
      throw new NotFoundException('Consent access is restricted');
    }
  }

  private async assertCanManage(ds: any, actorId: string) {
    const roles = await this.getRoles(ds, actorId);
    if (!roles.some((role) => ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff'].includes(role))) {
      throw new NotFoundException('Consent management is restricted');
    }
  }

  private async assertCanRespond(ds: any, actorId: string, studentId: string) {
    const roles = await this.getRoles(ds, actorId);
    if (roles.includes('parent')) {
      const linked: any[] = await runDbQuery(
        ds,
        `SELECT 1 FROM parent_student_links WHERE parent_id = ? AND student_id = ? LIMIT 1`,
        [actorId, studentId],
      );
      if (!linked.length) throw new NotFoundException('Parent is not linked to this student');
      return;
    }
    if (!roles.some((role) => ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff'].includes(role))) {
      throw new NotFoundException('Consent response is restricted');
    }
  }
}
