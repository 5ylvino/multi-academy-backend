import {
  Controller,
  Headers,
  Param,
  Post,
  Req,
  UnauthorizedException,
} from '@nestjs/common';
import type { Request } from 'express';
import { Public } from '../common/auth/public.decorator';
import { ok } from '../common/types/api-response';
import { GatewayPaymentsService } from './gateway-payments.service';

@Controller('webhooks')
export class WebhooksController {
  constructor(private readonly gatewayPayments: GatewayPaymentsService) {}

  @Public()
  @Post('payments/:providerId')
  async paymentWebhook(
    @Param('providerId') providerId: string,
    @Headers() headers: Record<string, string>,
    @Req() req: Request,
  ) {
    const rawBody = Buffer.isBuffer((req as any).rawBody)
      ? (req as any).rawBody
      : Buffer.from(JSON.stringify(req.body || {}));

    try {
      const result = await this.gatewayPayments.settleFromWebhook(
        providerId,
        headers,
        rawBody,
      );
      return ok('Webhook processed', result);
    } catch (err) {
      throw new UnauthorizedException(
        err instanceof Error ? err.message : 'Webhook verification failed',
      );
    }
  }
}
