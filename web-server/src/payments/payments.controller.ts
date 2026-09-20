import {
  Body,
  Controller,
  ForbiddenException,
  Get,
  Param,
  Patch,
  Req,
  ServiceUnavailableException,
} from '@nestjs/common';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { ok } from '../common/types/api-response';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { PaymentServiceClient } from './payment-service.client';

@Controller('payments')
export class PaymentsController {
  constructor(
    private readonly paymentClient: PaymentServiceClient,
    private readonly flags: FeatureFlagService,
  ) {}

  private actor(user: AuthUserClaims) {
    return {
      userId: user.sub || user.user_id || 'system',
      roles: user.roles || [],
    };
  }

  private assertPaymentService() {
    if (!this.paymentClient.isEnabled()) {
      throw new ServiceUnavailableException('Payment service is not available');
    }
  }

  private async featureList(tenantId: string): Promise<string[]> {
    const keys = [
      'fees.gateway',
      'fees.installments',
      'fees.advance_payment',
      'tutoring.payments',
    ];
    const enabled: string[] = [];
    for (const key of keys) {
      if (await this.flags.resolve(tenantId, key)) enabled.push(key);
    }
    return enabled;
  }

  @Get('contexts')
  async listContexts(@Req() req: { user: AuthUserClaims }) {
    this.assertPaymentService();
    const tenantId = req.user.tenant_id;
    const data = await this.paymentClient.get<{ items: unknown[] }>(
      '/v1/contexts',
      tenantId,
      this.actor(req.user),
      await this.featureList(tenantId),
    );
    return ok('Payment contexts', data);
  }

  @Patch('contexts/:contextKey')
  @RequirePermissions('fees:manage')
  async updateContext() {
    throw new ForbiddenException(
      'Payment settings are managed in the control platform console. Contact your platform administrator.',
    );
  }

  @Get('gateways')
  async listGateways(@Req() req: { user: AuthUserClaims }) {
    this.assertPaymentService();
    const tenantId = req.user.tenant_id;
    const data = await this.paymentClient.get<{ items: unknown[] }>(
      '/v1/gateways',
      tenantId,
      this.actor(req.user),
      await this.featureList(tenantId),
    );
    return ok('Payment gateways', data);
  }

  @Patch('gateways/:gatewayId')
  @RequirePermissions('fees:manage')
  async updateGateway() {
    throw new ForbiddenException(
      'Payment settings are managed in the control platform console. Contact your platform administrator.',
    );
  }
}
