import { Body, Controller, Get, Param, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { GatewayPaymentsService } from './gateway-payments.service';
import {
  CreateAdvanceCheckoutDto,
  CreateGatewayCheckoutDto,
  CreateInstallmentPlanDto,
  VerifyGatewayPaymentDto,
} from './dto/gateway-payments.dto';

@Controller('financial/gateway')
export class GatewayPaymentsController {
  constructor(private readonly gateway: GatewayPaymentsService) {}

  @Post('checkout')
  @RequireFeature('fees.gateway')
  @RequirePermissions('payments:process', 'fees:manage')
  async checkout(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateGatewayCheckoutDto,
  ) {
    return ok(
      'Checkout created',
      await this.gateway.createCheckout(
        tenantId,
        {
          studentId: body.studentId,
          invoiceId: body.invoiceId,
          installmentId: body.installmentId,
          amount: body.amount,
          email: body.email,
          callbackUrl: body.callbackUrl,
          kind: body.kind || 'invoice',
          term: body.term,
          sessionLabel: body.sessionLabel,
        },
        user.user_id,
      ),
    );
  }

  @Post('checkout/advance')
  @RequireFeature('fees.advance_payment')
  @RequirePermissions('payments:process', 'fees:manage')
  async advanceCheckout(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateAdvanceCheckoutDto,
  ) {
    return ok(
      'Advance checkout created',
      await this.gateway.createCheckout(
        tenantId,
        {
          studentId: body.studentId,
          amount: body.amount,
          email: body.email,
          callbackUrl: body.callbackUrl,
          kind: 'advance',
          term: body.term,
          sessionLabel: body.sessionLabel,
        },
        user.user_id,
      ),
    );
  }

  @Post('verify')
  @RequireFeature('fees.gateway')
  @RequirePermissions('payments:process', 'payments:view', 'fees:manage')
  async verify(
    @TenantId() tenantId: string,
    @Body() body: VerifyGatewayPaymentDto,
  ) {
    return ok('Payment verified', await this.gateway.verify(tenantId, body.reference));
  }

  @Post('installment-plans')
  @RequirePermissions('fees:manage', 'payments:process')
  async createPlan(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateInstallmentPlanDto,
  ) {
    return ok(
      'Installment plan created',
      await this.gateway.createInstallmentPlan(tenantId, body, user.user_id),
    );
  }

  @Get('installment-plans')
  @RequirePermissions('fees:view', 'payments:view', 'fees:manage')
  async listPlans(
    @TenantId() tenantId: string,
    @Query('studentId') studentId?: string,
  ) {
    return ok(
      'Installment plans',
      await this.gateway.listInstallmentPlans(tenantId, studentId),
    );
  }

  @Get('advance-credits')
  @RequireFeature('fees.advance_payment')
  @RequirePermissions('fees:view', 'payments:view', 'fees:manage')
  async listCredits(
    @TenantId() tenantId: string,
    @Query('studentId') studentId?: string,
  ) {
    return ok(
      'Advance credits',
      await this.gateway.listAdvanceCredits(tenantId, studentId),
    );
  }

  @Get('sessions/:reference')
  @RequireFeature('fees.gateway')
  @RequirePermissions('payments:view', 'payments:process')
  async verifyGet(
    @TenantId() tenantId: string,
    @Param('reference') reference: string,
  ) {
    return ok('Payment verified', await this.gateway.verify(tenantId, reference));
  }

  @Post('reminders/run')
  @RequireFeature('fees.invoices')
  @RequirePermissions('fees:manage', 'payments:process')
  async runReminders(@TenantId() tenantId: string) {
    return ok('Fee reminders sent', await this.gateway.runFeeReminders(tenantId));
  }
}
