import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

export type StudentBalanceSnapshot = {
  studentId: string;
  arrears: number;
  prepaidCredits: number;
  netBalance: number;
  hasArrears: boolean;
  frozen: boolean;
  message?: string;
};

@Injectable()
export class BalanceFreezeService {
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

  /** Sum unpaid/partial invoices minus advance credits for a student. */
  async computeStudentBalance(
    tenantId: string,
    studentId: string,
  ): Promise<StudentBalanceSnapshot> {
    const ds = await this.getTenantDs(tenantId);

    const invoiceRows: any[] = await runDbQuery(
      ds,
      `SELECT COALESCE(SUM(amount), 0) as total
       FROM financial_invoices
       WHERE student_id = ? AND status IN ('unpaid', 'partial')`,
      [studentId],
    );
    const creditRows: any[] = await runDbQuery(
      ds,
      `SELECT COALESCE(SUM(amount), 0) as total
       FROM financial_advance_credits
       WHERE student_id = ?`,
      [studentId],
    ).catch(() => [{ total: 0 }]);

    const arrears = Number(invoiceRows?.[0]?.total ?? 0);
    const prepaidCredits = Number(creditRows?.[0]?.total ?? 0);
    const netBalance = prepaidCredits - arrears;
    const hasArrears = arrears > 0 && netBalance < 0;

    const freezeEnabled = await this.flags.resolve(tenantId, 'fees.balance_freeze');
    const frozen = freezeEnabled && hasArrears;

    return {
      studentId,
      arrears,
      prepaidCredits,
      netBalance,
      hasArrears,
      frozen,
      message: frozen
        ? 'Portal access is restricted due to outstanding fee arrears. Please settle balances to continue.'
        : undefined,
    };
  }

  /** Aggregate balance across all wards for a parent. */
  async computeParentBalance(
    tenantId: string,
    parentId: string,
  ): Promise<{ frozen: boolean; message?: string; wards: StudentBalanceSnapshot[] }> {
    const ds = await this.getTenantDs(tenantId);
    const wardRows: any[] = await runDbQuery(
      ds,
      `SELECT student_id as "studentId" FROM parent_student_links WHERE parent_id = ?`,
      [parentId],
    );
    const wards: StudentBalanceSnapshot[] = [];
    for (const row of wardRows || []) {
      wards.push(await this.computeStudentBalance(tenantId, row.studentId));
    }
    const frozen = wards.some((w) => w.frozen);
    return {
      frozen,
      message: frozen
        ? 'One or more wards have outstanding fee arrears. Some portal actions are restricted until fees are settled.'
        : undefined,
      wards,
    };
  }
}
