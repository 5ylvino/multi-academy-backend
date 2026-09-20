import { Injectable, Logger } from '@nestjs/common';
import {
  CheckoutSession,
  CreateCheckoutInput,
  PaymentGateway,
  PaymentVerification,
  resolveVerifyArgs,
  WebhookResult,
} from './provider.interfaces';
import { ProviderSecretsService } from '../provider-secrets.service';

/**
 * MTN MoMo Collection API stub — provider id `momo`.
 * Returns deterministic checkout URLs when vault secrets are missing (dev/sandbox).
 */
@Injectable()
export class MomoGateway implements PaymentGateway {
  readonly id = 'momo';
  private readonly logger = new Logger(MomoGateway.name);

  constructor(private readonly secrets: ProviderSecretsService) {}

  private async credentials(tenantId: string) {
    const payload = await this.secrets.getSecrets(tenantId, 'payment');
    return {
      subscriptionKey:
        payload?.secrets?.subscription_key ||
        payload?.secrets?.subscriptionKey ||
        process.env.MOMO_SUBSCRIPTION_KEY ||
        '',
      apiUser:
        payload?.secrets?.api_user ||
        payload?.secrets?.apiUser ||
        process.env.MOMO_API_USER ||
        '',
      apiKey:
        payload?.secrets?.api_key ||
        payload?.secrets?.apiKey ||
        process.env.MOMO_API_KEY ||
        '',
      targetEnvironment: payload?.mode || process.env.MOMO_ENV || 'sandbox',
    };
  }

  async createCheckout(input: CreateCheckoutInput): Promise<CheckoutSession> {
    const tenantId = String(input.metadata?.tenantId || '');
    const creds = tenantId ? await this.credentials(tenantId) : { subscriptionKey: '', apiUser: '', apiKey: '', targetEnvironment: 'sandbox' };

    if (!creds.subscriptionKey || !creds.apiUser) {
      this.logger.warn('MoMo credentials missing');
      throw new Error('Payment gateway is not configured');
    }

    return {
      providerId: this.id,
      reference: input.reference,
      checkoutUrl: `https://sandbox.momodeveloper.mtn.com/pay?ref=${encodeURIComponent(input.reference)}&amount=${input.amountMinor}&currency=${input.currency || 'NGN'}`,
      accessCode: `momo_${input.reference.slice(-12)}`,
    };
  }

  async verifyTransaction(
    referenceOrInput: string | import('./provider.interfaces').VerifyTransactionInput,
    tenantId?: string,
  ): Promise<PaymentVerification> {
    const { reference } = resolveVerifyArgs(referenceOrInput, tenantId);
    this.logger.log(`MoMo verify stub for ${reference}`);
    return {
      providerId: this.id,
      reference,
      status: 'pending',
      amountMinor: 0,
      currency: 'NGN',
      providerReference: reference,
    };
  }

  async handleWebhook(_headers: Record<string, string>, rawBody: Buffer): Promise<WebhookResult> {
    try {
      const payload = JSON.parse(rawBody.toString('utf8'));
      const ref = payload?.externalId || payload?.reference || '';
      const status = payload?.status === 'SUCCESSFUL' ? 'success' : 'ignored';
      return {
        reference: ref,
        status,
        amountMinor: payload?.amount ? Math.round(Number(payload.amount) * 100) : undefined,
        currency: payload?.currency || 'NGN',
      };
    } catch {
      return { reference: '', status: 'ignored' };
    }
  }
}
