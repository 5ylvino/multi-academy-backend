import { Injectable, Logger } from '@nestjs/common';
import * as nodemailer from 'nodemailer';
import type SMTPTransport from 'nodemailer/lib/smtp-transport';
import { ProviderSecretsService } from '../provider-secrets.service';
import { EmailGateway } from './provider.interfaces';

type VaultPayload = {
  settings?: Record<string, unknown>;
  secrets?: Record<string, string>;
};

/**
 * cPanel email via SMTP (typical host: mail.yourdomain.com, port 465/587).
 * Vault secrets: username (or email), password.
 * Provider settings: host (or smtp_host), port, from, from_name, secure.
 */
@Injectable()
export class CpanelEmailGateway implements EmailGateway {
  readonly id = 'cpanel';
  private readonly logger = new Logger(CpanelEmailGateway.name);
  private tenantId = '';

  constructor(private readonly secrets: ProviderSecretsService) {}

  bindTenant(tenantId: string) {
    this.tenantId = tenantId;
    return this;
  }

  private resolveSmtp(payload: VaultPayload | null) {
    const secrets = payload?.secrets || {};
    const settings = payload?.settings || {};

    const password = secrets.password || secrets.smtp_password;
    const username =
      secrets.username ||
      secrets.email ||
      String(settings.username || settings.email || '');
    const host = String(
      settings.host ||
        settings.smtp_host ||
        secrets.host ||
        secrets.smtp_host ||
        '',
    ).trim();
    const port = Number(settings.port ?? secrets.port ?? 587);
    const secure =
      settings.secure !== undefined
        ? Boolean(settings.secure)
        : port === 465;
    const from = String(settings.from || secrets.from || username || '');
    const fromName = String(settings.from_name || secrets.from_name || '').trim();
    const tlsServername = String(
      settings.tls_servername || settings.tlsServername || host,
    ).trim();

    if (!host || !username || !password) {
      throw new Error(
        'cPanel SMTP host/username/password missing from control vault',
      );
    }
    if (!from) {
      throw new Error('cPanel SMTP from address missing from control vault');
    }

    return {
      host,
      port,
      secure,
      auth: { user: username, pass: password },
      from,
      fromName,
      tlsServername,
    };
  }

  private createTransport(
    smtp: ReturnType<CpanelEmailGateway['resolveSmtp']>,
  ): nodemailer.Transporter<SMTPTransport.SentMessageInfo> {
    return nodemailer.createTransport({
      host: smtp.host,
      port: smtp.port,
      secure: smtp.secure,
      requireTLS: !smtp.secure && smtp.port === 587,
      auth: smtp.auth,
      connectionTimeout: 20_000,
      greetingTimeout: 20_000,
      socketTimeout: 30_000,
      tls: {
        servername: smtp.tlsServername,
        minVersion: 'TLSv1.2',
      },
    });
  }

  /** Read-only SMTP auth check — used by control-plane health ping via Nest. */
  async verify(tenantId?: string): Promise<void> {
    const resolvedTenant = tenantId || this.tenantId;
    if (!resolvedTenant) {
      throw new Error('tenantId required for cPanel SMTP verify');
    }
    const payload = await this.secrets.getSecrets(resolvedTenant, 'email');
    const smtp = this.resolveSmtp(payload);
    const transporter = this.createTransport(smtp);
    try {
      await transporter.verify();
    } finally {
      transporter.close();
    }
  }

  async send(input: {
    to: string | string[];
    subject: string;
    html?: string;
    text?: string;
    tenantId?: string;
  }): Promise<{ messageId: string }> {
    const tenantId = input.tenantId || this.tenantId;
    if (!tenantId) throw new Error('tenantId required for cPanel email send');

    const payload = await this.secrets.getSecrets(tenantId, 'email');
    const smtp = this.resolveSmtp(payload);
    const transporter = this.createTransport(smtp);

    const recipients = Array.isArray(input.to) ? input.to : [input.to];
    const from = smtp.fromName
      ? `"${smtp.fromName}" <${smtp.from}>`
      : smtp.from;

    try {
      const info = await transporter.sendMail({
        from,
        to: recipients,
        subject: input.subject,
        html: input.html,
        text: input.text,
      });
      return { messageId: String(info.messageId || `cpanel_${Date.now()}`) };
    } catch (error) {
      this.logger.warn(
        `cPanel SMTP send failed: ${
          error instanceof Error ? error.message : error
        }`,
      );
      throw error instanceof Error ? error : new Error('cPanel email send failed');
    } finally {
      transporter.close();
    }
  }
}
