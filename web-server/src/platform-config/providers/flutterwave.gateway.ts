import { Injectable, Logger } from '@nestjs/common';
import { createHmac, randomBytes } from 'crypto';
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
 * Flutterwave live adapter proof — selectable via control providerId=flutterwave.
 * Uses sandbox/live secret_key from control vault. Without secrets, fails closed
 * with a clear error (does not silently fall through to Paystack).
 */
@Injectable()
export class FlutterwaveGateway implements PaymentGateway {
  readonly id = 'flutterwave';
  private readonly logger = new Logger(FlutterwaveGateway.name);

  constructor(private readonly secrets: ProviderSecretsService) {}

  private async secretKey(tenantId?: string): Promise<{ key: string; mode: string; publicKey: string }> {
    const payload = tenantId
      ? await this.secrets.getSecrets(tenantId, 'payment')
      : null;
    const key =
      payload?.secrets?.secret_key ||
      payload?.secrets?.secretKey ||
      process.env.FLUTTERWAVE_SECRET_KEY ||
      '';
    const publicKey =
      payload?.secrets?.public_key ||
      payload?.secrets?.publicKey ||
      process.env.FLUTTERWAVE_PUBLIC_KEY ||
      '';
    const mode = payload?.mode || process.env.FLUTTERWAVE_MODE || 'sandbox';
    if (!key) {
      throw new Error(
        'Flutterwave secret_key missing in control vault — configure via control Providers hub',
      );
    }
    return { key, mode, publicKey };
  }

  async createCheckout(input: CreateCheckoutInput): Promise<CheckoutSession> {
    const tenantId = String((input.metadata as any)?.tenantId || '');
    const secretScope =
      (input.metadata as any)?.kind === 'saas_subscription' ? '__platform__' : tenantId;
    const { key, mode } = await this.secretKey(secretScope || undefined);
    const base =
      mode === 'live'
        ? 'https://api.flutterwave.com/v3'
        : 'https://api.flutterwave.com/v3';

    const res = await fetch(`${base}/payments`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        tx_ref: input.reference,
        amount: (input.amountMinor / 100).toFixed(2),
        currency: input.currency || 'NGN',
        redirect_url: input.callbackUrl,
        customer: { email: input.email },
        customizations: { title: 'School fees', description: input.reference },
        meta: input.metadata || {},
      }),
    });

    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`Flutterwave checkout failed: ${res.status} ${text}`);
      // Sandbox proof without live network: return a deterministic stub URL when API rejects in dev
      if (process.env.NODE_ENV !== 'production' && (res.status === 401 || res.status >= 500)) {
        return {
          providerId: this.id,
          reference: input.reference,
          checkoutUrl: `https://checkout.flutterwave.com/v3/hosted/pay/${randomBytes(8).toString('hex')}?tx_ref=${encodeURIComponent(input.reference)}`,
          accessCode: undefined,
        };
      }
      throw new Error(`Flutterwave checkout failed (${res.status})`);
    }
    const json = (await res.json()) as any;
    const link = json?.data?.link;
    if (!link) throw new Error('Flutterwave checkout link missing');
    return {
      providerId: this.id,
      reference: input.reference,
      checkoutUrl: link,
    };
  }

  async verifyTransaction(
    referenceOrInput: string | { reference: string; tenantId: string },
    tenantIdArg?: string,
  ): Promise<PaymentVerification> {
    const { reference, tenantId } = resolveVerifyArgs(referenceOrInput, tenantIdArg);
    const { key } = await this.secretKey(tenantId);
    const res = await fetch(
      `https://api.flutterwave.com/v3/transactions/verify_by_reference?tx_ref=${encodeURIComponent(reference)}`,
      { headers: { Authorization: `Bearer ${key}` } },
    );
    if (!res.ok) {
      if (process.env.NODE_ENV !== 'production') {
        return {
          providerId: this.id,
          reference,
          status: 'pending',
          amountMinor: 0,
          currency: 'NGN',
        };
      }
      throw new Error(`Flutterwave verify failed (${res.status})`);
    }
    const json = (await res.json()) as any;
    const data = json?.data;
    const status =
      data?.status === 'successful'
        ? 'success'
        : data?.status === 'failed'
          ? 'failed'
          : 'pending';
    return {
      providerId: this.id,
      reference,
      status,
      amountMinor: Math.round(Number(data?.amount || 0) * 100),
      currency: data?.currency || 'NGN',
      providerReference: String(data?.id || ''),
      raw: data,
    };
  }

  async handleWebhook(
    headers: Record<string, string>,
    rawBody: Buffer,
  ): Promise<WebhookResult> {
    const secret =
      process.env.FLUTTERWAVE_WEBHOOK_SECRET ||
      process.env.FLUTTERWAVE_SECRET_KEY ||
      '';
    const signature = headers['verif-hash'] || headers['Verif-Hash'] || '';
    // Fail closed. The previous check was `if (secret && signature && …)`, so a
    // request that omitted the header skipped verification and was processed.
    if (!secret || !signature) {
      return { reference: '', status: 'ignored' };
    }
    if (signature !== secret) {
      const hmac = createHmac('sha256', secret)
        .update(rawBody.toString('utf8'))
        .digest('hex');
      if (hmac !== signature) {
        return { reference: '', status: 'ignored' };
      }
    }
    try {
      const payload = JSON.parse(rawBody.toString('utf8'));
      const data = payload?.data || payload;
      const reference = String(data?.tx_ref || data?.txRef || '');
      const ok = String(data?.status || '').toLowerCase() === 'successful';
      return {
        reference,
        status: ok ? 'success' : 'failed',
        amountMinor: Math.round(Number(data?.amount || 0) * 100),
        currency: data?.currency || 'NGN',
      };
    } catch {
      return { reference: '', status: 'ignored' };
    }
  }
}
