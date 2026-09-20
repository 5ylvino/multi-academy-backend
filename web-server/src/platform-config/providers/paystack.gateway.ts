import { Injectable, Logger } from '@nestjs/common';
import * as crypto from 'crypto';
import { ProviderSecretsService } from '../provider-secrets.service';
import {
  CheckoutSession,
  CreateCheckoutInput,
  PaymentGateway,
  PaymentVerification,
  VerifyTransactionInput,
  WebhookResult,
  resolveVerifyArgs,
} from './provider.interfaces';

/**
 * Paystack PaymentGateway adapter.
 * Secrets (secret_key) come from control vault via ProviderSecretsService.
 */
@Injectable()
export class PaystackGateway implements PaymentGateway {
  readonly id = 'paystack';
  private readonly logger = new Logger(PaystackGateway.name);

  constructor(private readonly secrets: ProviderSecretsService) {}

  private async secretKey(tenantId: string): Promise<string> {
    const payload = await this.secrets.getSecrets(tenantId, 'payment');
    const key =
      payload?.secrets?.secret_key ||
      payload?.secrets?.secretKey ||
      payload?.secrets?.SECRET_KEY;
    if (!key) {
      throw new Error('Paystack secret_key missing from control vault');
    }
    return key;
  }

  async createCheckout(input: CreateCheckoutInput): Promise<CheckoutSession> {
    const tenantId = String(input.metadata?.tenantId || '');
    if (!tenantId) throw new Error('tenantId required in checkout metadata');
    const secret = await this.secretKey(
      input.metadata?.kind === 'saas_subscription' ? '__platform__' : tenantId,
    );

    const res = await fetch('https://api.paystack.co/transaction/initialize', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${secret}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        email: input.email,
        amount: input.amountMinor,
        currency: (input.currency || 'NGN').toUpperCase(),
        reference: input.reference,
        callback_url: input.callbackUrl,
        metadata: input.metadata || {},
      }),
    });
    const json = (await res.json()) as any;
    if (!res.ok || !json?.status) {
      this.logger.warn(`Paystack init failed: ${JSON.stringify(json)}`);
      throw new Error(json?.message || 'Paystack initialize failed');
    }
    return {
      providerId: this.id,
      reference: input.reference,
      checkoutUrl: json.data.authorization_url,
      accessCode: json.data.access_code,
    };
  }

  async verifyTransaction(
    referenceOrInput: string | VerifyTransactionInput,
    tenantId?: string,
  ): Promise<PaymentVerification> {
    const { reference, tenantId: tid } = resolveVerifyArgs(referenceOrInput, tenantId);
    if (!tid) throw new Error('tenantId required for Paystack verify');
    const secret = await this.secretKey(tid);
    const res = await fetch(
      `https://api.paystack.co/transaction/verify/${encodeURIComponent(reference)}`,
      { headers: { Authorization: `Bearer ${secret}` } },
    );
    const json = (await res.json()) as any;
    if (!res.ok || !json?.status) {
      return {
        providerId: this.id,
        reference,
        status: 'failed',
        amountMinor: 0,
        currency: 'NGN',
        raw: json,
      };
    }
    const data = json.data;
    const status =
      data.status === 'success'
        ? 'success'
        : data.status === 'failed'
          ? 'failed'
          : 'pending';
    return {
      providerId: this.id,
      reference: data.reference || reference,
      status,
      amountMinor: Number(data.amount || 0),
      currency: (data.currency || 'NGN').toUpperCase(),
      providerReference: String(data.id || data.reference || ''),
      raw: data,
    };
  }

  async handleWebhook(
    headers: Record<string, string>,
    rawBody: Buffer,
  ): Promise<WebhookResult> {
    // Signature verified by GatewayPaymentsService / WebhooksController using vault secret.
    const payload = JSON.parse(rawBody.toString('utf8'));
    const event = payload?.event;
    const data = payload?.data;
    if (!data?.reference) {
      return { reference: '', status: 'ignored' };
    }
    if (event === 'charge.success' || data.status === 'success') {
      return {
        reference: data.reference,
        status: 'success',
        amountMinor: Number(data.amount || 0),
        currency: (data.currency || 'NGN').toUpperCase(),
      };
    }
    if (data.status === 'failed') {
      return {
        reference: data.reference,
        status: 'failed',
        amountMinor: Number(data.amount || 0),
        currency: (data.currency || 'NGN').toUpperCase(),
      };
    }
    return { reference: data.reference, status: 'ignored' };
  }

  /** Verify x-paystack-signature header. */
  static verifySignature(rawBody: Buffer, signature: string, secretKey: string): boolean {
    const hash = crypto
      .createHmac('sha512', secretKey)
      .update(rawBody.toString('utf8'))
      .digest('hex');
    return hash === signature;
  }
}
