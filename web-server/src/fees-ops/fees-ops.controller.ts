import { Body, Controller, Get, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { FeesOpsService } from './fees-ops.service';

@Controller('fees-ops')
export class FeesOpsController {
  constructor(private readonly feesOps: FeesOpsService) {}

  @Get('splits')
  @RequireFeature('fees.split_settlement')
  @RequirePermissions('fees:view', 'payments:view', 'fees:manage')
  async listSplits(@TenantId() tenantId: string) {
    return ok('Split settlements', await this.feesOps.listSplits(tenantId));
  }

  @Post('splits')
  @RequireFeature('fees.split_settlement')
  @RequirePermissions('fees:manage', 'payments:record')
  async createSplit(
    @TenantId() tenantId: string,
    @Body()
    body: {
      paymentReference: string;
      grossMinor: number;
      saasShareBps?: number;
      currency?: string;
    },
  ) {
    return ok('Split created', await this.feesOps.createSplit(tenantId, body));
  }

  @Get('recon')
  @RequireFeature('fees.bank_reconciliation')
  @RequirePermissions('fees:view', 'payments:view')
  async listRecon(@TenantId() tenantId: string) {
    return ok('Bank recon', await this.feesOps.listRecon(tenantId));
  }

  @Post('recon')
  @RequireFeature('fees.bank_reconciliation')
  @RequirePermissions('fees:manage', 'payments:record')
  async ingestRecon(
    @TenantId() tenantId: string,
    @Body()
    body: {
      bankReference: string;
      amountMinor: number;
      currency?: string;
      matchedPaymentRef?: string;
      notes?: string;
    },
  ) {
    return ok('Recon row ingested', await this.feesOps.ingestBankRow(tenantId, body));
  }
}
