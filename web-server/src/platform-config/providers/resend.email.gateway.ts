import { Injectable, Logger } from '@nestjs/common';
import { ProviderSecretsService } from '../provider-secrets.service';
import { EmailGateway } from './provider.interfaces';

/**
 * Email gateway skeleton (Resend). Fail closed if vault secrets missing.
 * Alternate vendors (SES/SendGrid) can register under the same EmailGateway port.
 */
@Injectable()
export class ResendEmailGateway implements EmailGateway {
  readonly id = 'resend';
  private readonly logger = new Logger(ResendEmailGateway.name);
  private tenantId = '';

  constructor(private readonly secrets: ProviderSecretsService) {}

  bindTenant(tenantId: string) {
    this.tenantId = tenantId;
    return this;
  }

  async send(input: {
    to: string | string[];
    subject: string;
    html?: string;
    text?: string;
    tenantId?: string;
  }): Promise<{ messageId: string }> {
    const tenantId = input.tenantId || this.tenantId;
    if (!tenantId) throw new Error('tenantId required for email send');

    const payload = await this.secrets.getSecrets(tenantId, 'email');
    const apiKey = payload?.secrets?.api_key || payload?.secrets?.apiKey;
    const from =
      String(payload?.settings?.from || payload?.secrets?.from || '') ||
      'noreply@multi-academy.local';
    const fromName = String(
      payload?.settings?.fromName ||
        payload?.settings?.from_name ||
        payload?.secrets?.from_name ||
        '',
    ).trim();
    if (!apiKey) {
      throw new Error('Email api_key missing from control vault');
    }

    const to = Array.isArray(input.to) ? input.to : [input.to];
    const res = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: fromName && !from.includes("<") ? `${fromName} <${from}>` : from,
        to,
        subject: input.subject,
        html: input.html,
        text: input.text,
      }),
    });
    const json = (await res.json().catch(() => ({}))) as any;
    if (!res.ok) {
      this.logger.warn(`Resend failed: ${JSON.stringify(json)}`);
      throw new Error(json?.message || 'Email send failed');
    }
    return { messageId: String(json?.id || `email_${Date.now()}`) };
  }
}
