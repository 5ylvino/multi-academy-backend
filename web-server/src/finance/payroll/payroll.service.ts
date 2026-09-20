import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../../control-plane/control-plane.service';
import { TenantConnectionService } from '../../database/tenant-connection.service';
import { randomToken } from '../../common/utils/id.util';
import { runDbQuery } from '../../database/db-driver.util';
import { FeatureFlagService } from '../../platform-config/feature-flag.service';

export function payrollNet(gross: number, allowances = 0, deductions = 0, paye?: number) {
  const tax = paye == null ? Math.max(0, (gross + allowances - deductions) * 0.1) : paye;
  return { paye: tax, net: gross + allowances - deductions - tax };
}

export function canAdvancePayrollStatus(status: string, next: 'approved' | 'payout_ready') {
  return next === 'approved' ? status === 'draft' : status === 'approved';
}

@Injectable()
export class PayrollService {
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
      CREATE TABLE IF NOT EXISTS payroll_runs (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        period_start varchar(32) NOT NULL,
        period_end varchar(32) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'draft',
        total_gross decimal(15,2) NOT NULL DEFAULT 0,
        total_net decimal(15,2) NOT NULL DEFAULT 0,
        salary_account_ref varchar(255) NULL,
        operating_account_ref varchar(255) NULL,
        approved_by varchar(64) NULL,
        approved_at TIMESTAMP NULL,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS payroll_paye_lines (
        id varchar(64) PRIMARY KEY,
        run_id varchar(64) NOT NULL,
        staff_id varchar(64) NOT NULL,
        gross decimal(15,2) NOT NULL DEFAULT 0,
        paye decimal(15,2) NOT NULL DEFAULT 0,
        allowances decimal(15,2) NOT NULL DEFAULT 0,
        deductions decimal(15,2) NOT NULL DEFAULT 0,
        net decimal(15,2) NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    for (const statement of [
      `ALTER TABLE payroll_runs ADD COLUMN IF NOT EXISTS total_net decimal(15,2) NOT NULL DEFAULT 0`,
      `ALTER TABLE payroll_runs ADD COLUMN IF NOT EXISTS salary_account_ref varchar(255) NULL`,
      `ALTER TABLE payroll_runs ADD COLUMN IF NOT EXISTS operating_account_ref varchar(255) NULL`,
      `ALTER TABLE payroll_runs ADD COLUMN IF NOT EXISTS approved_by varchar(64) NULL`,
      `ALTER TABLE payroll_runs ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP NULL`,
      `ALTER TABLE payroll_paye_lines ADD COLUMN IF NOT EXISTS allowances decimal(15,2) NOT NULL DEFAULT 0`,
      `ALTER TABLE payroll_paye_lines ADD COLUMN IF NOT EXISTS deductions decimal(15,2) NOT NULL DEFAULT 0`,
    ]) {
      await ds.query(statement).catch(() => undefined);
    }
  }

  async listRuns(tenantId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'finance.payroll_paye');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId);
    const runs = await runDbQuery(
      ds,
      `SELECT id, title, period_start as "periodStart", period_end as "periodEnd",
              status, total_gross as "totalGross", total_net as "totalNet",
              salary_account_ref as "salaryAccountRef", operating_account_ref as "operatingAccountRef",
              approved_by as "approvedBy", approved_at as "approvedAt",
              currency, created_at as "createdAt"
       FROM payroll_runs ORDER BY created_at DESC LIMIT 100`,
      [],
    );
    const out = [];
    for (const run of runs) {
      const lines = await runDbQuery(
        ds,
        `SELECT id, staff_id as "staffId", gross, paye, allowances, deductions, net
         FROM payroll_paye_lines WHERE run_id = ?`,
        [run.id],
      );
      out.push({ ...run, lines });
    }
    return out;
  }

  async createRun(
    tenantId: string,
    actorId: string,
    body: {
      title: string;
      periodStart: string;
      periodEnd: string;
      currency?: string;
      lines?: { staffId: string; gross: number; paye?: number; allowances?: number; deductions?: number }[];
      salaryAccountRef?: string;
      operatingAccountRef?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'finance.payroll_paye');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId);
    if (!body.title?.trim() || !body.periodStart || !body.periodEnd || body.periodEnd < body.periodStart) {
      throw new NotFoundException('Payroll period is invalid');
    }
    const id = randomToken('prun');
    let totalGross = 0;
    let totalNet = 0;
    for (const line of body.lines || []) {
      const gross = Number(line.gross);
      const allowances = Number(line.allowances ?? 0);
      const deductions = Number(line.deductions ?? 0);
      const paye = payrollNet(gross, allowances, deductions, line.paye == null ? undefined : Number(line.paye)).paye;
      if (!line.staffId || !Number.isFinite(gross) || gross <= 0 || allowances < 0 || deductions < 0 || paye < 0) {
        throw new NotFoundException('Payroll employee line is invalid');
      }
      totalGross += gross + allowances;
      totalNet += gross + allowances - deductions - paye;
    }
    await runDbQuery(
      ds,
      `INSERT INTO payroll_runs
       (id, title, period_start, period_end, status, total_gross, total_net,
        salary_account_ref, operating_account_ref, currency, created_at)
       VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, NOW())`,
      [id, body.title.trim(), body.periodStart, body.periodEnd, totalGross, totalNet,
        body.salaryAccountRef || null, body.operatingAccountRef || null, body.currency || 'NGN'],
    );
    for (const line of body.lines || []) {
      const gross = Number(line.gross);
      const allowances = Number(line.allowances ?? 0);
      const deductions = Number(line.deductions ?? 0);
      const calculated = payrollNet(gross, allowances, deductions, line.paye == null ? undefined : Number(line.paye));
      const paye = calculated.paye;
      const net = calculated.net;
      await runDbQuery(
        ds,
        `INSERT INTO payroll_paye_lines
         (id, run_id, staff_id, gross, paye, allowances, deductions, net, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW())`,
        [randomToken('paye'), id, line.staffId, gross, paye, allowances, deductions, net],
      );
    }
    return { id, totalGross };
  }

  async approveRun(tenantId: string, actorId: string, runId: string) {
    await this.flags.assertEnabled(tenantId, 'finance.payroll_paye');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId);
    const rows: any[] = await runDbQuery(ds, `SELECT status FROM payroll_runs WHERE id = ? LIMIT 1`, [runId]);
    if (!rows.length) throw new NotFoundException('Payroll run not found');
    if (!canAdvancePayrollStatus(rows[0].status, 'approved')) throw new NotFoundException('Only draft payroll runs can be approved');
    await runDbQuery(ds, `UPDATE payroll_runs SET status = 'approved', approved_by = ?, approved_at = NOW() WHERE id = ?`, [actorId, runId]);
    return { id: runId, status: 'approved' };
  }

  async markPayoutReady(tenantId: string, actorId: string, runId: string) {
    await this.flags.assertEnabled(tenantId, 'finance.payroll_paye');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId);
    const rows: any[] = await runDbQuery(ds, `SELECT status, salary_account_ref as "salaryAccountRef" FROM payroll_runs WHERE id = ? LIMIT 1`, [runId]);
    if (!rows.length) throw new NotFoundException('Payroll run not found');
    if (!canAdvancePayrollStatus(rows[0].status, 'payout_ready')) throw new NotFoundException('Payroll must be approved first');
    if (!rows[0].salaryAccountRef) throw new NotFoundException('Salary account reference is required');
    await runDbQuery(ds, `UPDATE payroll_runs SET status = 'payout_ready' WHERE id = ?`, [runId]);
    return { id: runId, status: 'payout_ready', provider: 'adapter-required' };
  }

  private async assertAccess(ds: any, actorId: string) {
    const rows: any[] = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [actorId]);
    const row = rows[0];
    let roles: unknown[] = row?.role ? [row.role] : [];
    if (row?.roles) {
      try { roles = typeof row.roles === 'string' ? JSON.parse(row.roles) : row.roles; } catch { /* fallback */ }
    }
    const allowed = ['director', 'school_admin', 'it_admin', 'principal', 'bursar', 'administrative_staff'];
    if (!Array.isArray(roles) || !roles.some((role) => allowed.includes(String(role).toLowerCase()))) {
      throw new NotFoundException('Payroll access is restricted to authorized finance staff');
    }
  }
}
