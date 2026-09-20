import { Injectable, Logger } from '@nestjs/common';
import {
  CheckoutSession,
  CreateCheckoutInput,
  PaymentGateway,
  PaymentVerification,
  WebhookResult,
  resolveVerifyArgs,
} from './provider.interfaces';
import { ProviderSecretsService } from '../provider-secrets.service';

/** PalmPay stub — returns checkout URL with reference; verify fails closed without secrets. */
@Injectable()
export class PalmPayGateway implements PaymentGateway {
  readonly id = 'palmpay';
  private readonly logger = new Logger(PalmPayGateway.name);

  constructor(private readonly secrets: ProviderSecretsService) {}

  private async credentials(tenantId?: string) {
    const payload = tenantId ? await this.secrets.getSecrets(tenantId, 'payment').catch(() => null) : null;
    const appId =
      payload?.secrets?.app_id ||
      payload?.secrets?.appId ||
      process.env.PALMPAY_APP_ID ||
      '';
    const secret =
      payload?.secrets?.secret_key ||
      payload?.secrets?.secretKey ||
      process.env.PALMPAY_SECRET_KEY ||
      '';
    return { appId, secret };
  }

  async createCheckout(input: CreateCheckoutInput): Promise<CheckoutSession> {
    const tenantId = String((input.metadata as any)?.tenantId || '');
    const { appId } = await this.credentials(tenantId || undefined);
    const checkoutUrl = `https://checkout.palmpay.com/stub/pay?ref=${encodeURIComponent(input.reference)}&amount=${input.amountMinor}${appId ? `&appId=${encodeURIComponent(appId)}` : ''}`;
    this.logger.debug(`PalmPay stub checkout ${input.reference}`);
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
      this.logger.warn('PalmPay verify blocked — secret_key missing');
      return {
        providerId: this.id,
        reference,
        status: 'failed',
        amountMinor: 0,
        currency: 'NGN',
      };
    }
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
