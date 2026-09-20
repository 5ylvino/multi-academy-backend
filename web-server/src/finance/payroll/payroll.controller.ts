import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ok } from '../../common/types/api-response';
import { TenantId } from '../../common/auth/tenant-id.decorator';
import { RequireFeature } from '../../platform-config/require-feature.decorator';
import { RequirePermissions } from '../../common/auth/require-permissions.decorator';
import { CurrentUser } from '../../common/auth/current-user.decorator';
import { AuthUserClaims } from '../../common/auth/auth-user.interface';
import { PayrollService } from './payroll.service';

@Controller('finance/payroll')
@RequireFeature('finance.payroll_paye')
export class PayrollController {
  constructor(private readonly payroll: PayrollService) {}

  @Get('runs')
  @RequirePermissions('payroll:read')
  async list(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Payroll runs', await this.payroll.listRuns(tenantId, user.user_id || user.sub));
  }

  @Post('runs')
  @RequirePermissions('payroll:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
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
    return ok('Payroll run created', await this.payroll.createRun(tenantId, user.user_id || user.sub, body));
  }

  @Post('runs/:id/finalize')
  @RequirePermissions('payroll:manage')
  async approve(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok('Payroll approved', await this.payroll.approveRun(tenantId, user.user_id || user.sub, id));
  }

  @Post('runs/:id/payout-ready')
  @RequirePermissions('payroll:manage')
  async payoutReady(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok('Payroll marked payout-ready', await this.payroll.markPayoutReady(tenantId, user.user_id || user.sub, id));
  }
}
