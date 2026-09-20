import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../../control-plane/control-plane.service';
import { TenantConnectionService } from '../../database/tenant-connection.service';
import { randomToken } from '../../common/utils/id.util';
import { runDbQuery } from '../../database/db-driver.util';
import { FeatureFlagService } from '../../platform-config/feature-flag.service';

@Injectable()
export class BudgetsService {
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
      CREATE TABLE IF NOT EXISTS budget_lines (
        id varchar(64) PRIMARY KEY,
        category varchar(128) NOT NULL,
        term varchar(32) NOT NULL,
        session_label varchar(64) NULL,
        planned_amount decimal(15,2) NOT NULL DEFAULT 0,
        spent_amount decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async list(tenantId: string, actorId: string, term?: string) {
    await this.flags.assertEnabled(tenantId, 'finance.budgets');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId, false);
    if (term) {
      return runDbQuery(
        ds,
        `SELECT id, category, term, session_label as "sessionLabel",
                planned_amount as "plannedAmount", spent_amount as "spentAmount",
                currency, created_at as "createdAt"
         FROM budget_lines WHERE term = ? ORDER BY category`,
        [term],
      );
    }
    return runDbQuery(
      ds,
      `SELECT id, category, term, session_label as "sessionLabel",
              planned_amount as "plannedAmount", spent_amount as "spentAmount",
              currency, created_at as "createdAt"
       FROM budget_lines ORDER BY created_at DESC LIMIT 200`,
      [],
    );
  }

  async create(
    tenantId: string,
    actorId: string,
    body: { category: string; term: string; sessionLabel?: string; plannedAmount: number; currency?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'finance.budgets');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId, true);
    if (!body.category?.trim() || !body.term?.trim() || !Number.isFinite(Number(body.plannedAmount)) || Number(body.plannedAmount) <= 0) {
      throw new NotFoundException('Budget line values are invalid');
    }
    const id = randomToken('bud');
    await runDbQuery(
      ds,
      `INSERT INTO budget_lines (id, category, term, session_label, planned_amount, spent_amount, currency, created_at)
       VALUES (?, ?, ?, ?, ?, 0, ?, NOW())`,
      [id, body.category, body.term, body.sessionLabel || null, body.plannedAmount, body.currency || 'NGN'],
    );
    return { id };
  }

  async recordSpend(tenantId: string, actorId: string, id: string, amount: number) {
    await this.flags.assertEnabled(tenantId, 'finance.budgets');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId, true);
    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0) throw new NotFoundException('Spend amount is invalid');
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id FROM budget_lines WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!rows.length) throw new NotFoundException('Budget line not found');
    await runDbQuery(
      ds,
      `UPDATE budget_lines SET spent_amount = spent_amount + ? WHERE id = ?`,
      [value, id],
    );
    return { id };
  }

  private async assertAccess(ds: any, actorId: string, write: boolean) {
    const roles = await this.getUserRoles(ds, actorId);
    const allowed = ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff', 'bursar'];
    if (!roles.some((role) => allowed.includes(role))) {
      throw new NotFoundException('Budget access is restricted to authorized finance staff');
    }
    if (write && !roles.some((role) => allowed.includes(role))) {
      throw new NotFoundException('Budget writes are restricted to authorized finance staff');
    }
  }

  private async getUserRoles(ds: any, userId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [userId]);
    const row = rows[0];
    if (!row) return [];
    if (Array.isArray(row.roles)) return row.roles.map((role: string) => String(role).toLowerCase());
    if (typeof row.roles === 'string') {
      try {
        const parsed = JSON.parse(row.roles);
        if (Array.isArray(parsed)) return parsed.map((role: string) => String(role).toLowerCase());
      } catch {}
    }
    return row.role ? [String(row.role).toLowerCase()] : [];
  }
}
