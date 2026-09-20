import { Injectable, Logger } from '@nestjs/common';
import { ProviderSecretsService } from '../provider-secrets.service';
import { SmsGateway } from './provider.interfaces';

/**
 * Africa's Talking SMS adapter.
 * Vault secrets expected: api_key, username; optional sender_id in settings.
 */
@Injectable()
export class AfricasTalkingGateway implements SmsGateway {
  readonly id = 'africas_talking';
  private readonly logger = new Logger(AfricasTalkingGateway.name);
  private tenantId = '';

  constructor(private readonly secrets: ProviderSecretsService) {}

  /** Bind tenant for the next send (registry sets this before resolve return). */
  bindTenant(tenantId: string) {
    this.tenantId = tenantId;
    return this;
  }

  async send(input: {
    to: string;
    message: string;
    senderId?: string;
    tenantId?: string;
  }): Promise<{ messageId: string }> {
    const tenantId = input.tenantId || this.tenantId;
    if (!tenantId) throw new Error('tenantId required for Africa\'s Talking send');

    const payload = await this.secrets.getSecrets(tenantId, 'sms');
    const apiKey = payload?.secrets?.api_key || payload?.secrets?.apiKey;
    const username = payload?.secrets?.username || payload?.settings?.username;
    if (!apiKey || !username) {
      throw new Error("Africa's Talking api_key/username missing from control vault");
    }
    const from =
      input.senderId ||
      String(payload?.settings?.senderId || payload?.secrets?.sender_id || '');

    const body = new URLSearchParams();
    body.set('username', String(username));
    body.set('to', input.to);
    body.set('message', input.message);
    if (from) body.set('from', from);

    const res = await fetch('https://api.africastalking.com/version1/messaging', {
      method: 'POST',
      headers: {
        apiKey: String(apiKey),
        'Content-Type': 'application/x-www-form-urlencoded',
        Accept: 'application/json',
      },
      body,
    });
    const json = (await res.json().catch(() => ({}))) as any;
    if (!res.ok) {
      this.logger.warn(`AT SMS failed: ${JSON.stringify(json)}`);
      throw new Error(json?.message || 'Africa\'s Talking send failed');
    }
    const messageId =
      json?.SMSMessageData?.Recipients?.[0]?.messageId ||
      json?.SMSMessageData?.Message ||
      `at_${Date.now()}`;
    return { messageId: String(messageId) };
  }
}
