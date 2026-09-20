import { Injectable, Logger } from '@nestjs/common';
import { randomBytes } from 'crypto';
import {
  CheckoutSession,
  CreateCheckoutInput,
  PaymentGateway,
  PaymentVerification,
  WebhookResult,
  resolveVerifyArgs,
} from './provider.interfaces';
import { ProviderSecretsService } from '../provider-secrets.service';

/** OPay stub — returns checkout URL with reference; verify fails closed without secrets. */
@Injectable()
export class OpayGateway implements PaymentGateway {
  readonly id = 'opay';
  private readonly logger = new Logger(OpayGateway.name);

  constructor(private readonly secrets: ProviderSecretsService) {}

  private async credentials(tenantId?: string) {
    const payload = tenantId ? await this.secrets.getSecrets(tenantId, 'payment').catch(() => null) : null;
    const merchantId =
      payload?.secrets?.merchant_id ||
      payload?.secrets?.merchantId ||
      process.env.OPAY_MERCHANT_ID ||
      '';
    const secret =
      payload?.secrets?.secret_key ||
      payload?.secrets?.secretKey ||
      process.env.OPAY_SECRET_KEY ||
      '';
    return { merchantId, secret };
  }

  async createCheckout(input: CreateCheckoutInput): Promise<CheckoutSession> {
    const tenantId = String((input.metadata as any)?.tenantId || '');
    const { merchantId } = await this.credentials(tenantId || undefined);
    const checkoutUrl = `https://checkout.opayweb.com/stub/pay?ref=${encodeURIComponent(input.reference)}&amount=${input.amountMinor}${merchantId ? `&mid=${encodeURIComponent(merchantId)}` : ''}`;
    this.logger.debug(`OPay stub checkout ${input.reference}`);
    return {
      providerId: this.id,
      reference: input.reference,
      checkoutUrl,
    };
  }

  async verifyTransaction(
    referenceOrInput: string | { reference: string; tenantId: string },
    tenantIdArg?: string,
  ): Promise<PaymentVerification> {
    const { reference, tenantId } = resolveVerifyArgs(referenceOrInput, tenantIdArg);
    const { secret } = await this.credentials(tenantId);
    if (!secret) {
      this.logger.warn('OPay verify blocked — secret_key missing');
      return {
        providerId: this.id,
        reference,
        status: 'failed',
        amountMinor: 0,
        currency: 'NGN',
      };
    }
    // Stub: without live OPay API integration, treat as pending
    return {
      providerId: this.id,
      reference,
      status: 'pending',
      amountMinor: 0,
      currency: 'NGN',
    };
  }

  async handleWebhook(_headers: Record<string, string>, _rawBody: Buffer): Promise<WebhookResult> {
    return { reference: '', status: 'ignored' };
  }
}
