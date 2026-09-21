import { ForbiddenException, Injectable, Logger } from '@nestjs/common';
import { FeatureFlagService } from './feature-flag.service';
import { ProviderRegistryService } from './providers/provider-registry.service';
import { ControlApiClient } from './control-api.client';
import { CpanelEmailGateway } from './providers/cpanel.email.gateway';

@Injectable()
export class CommsService {
  private readonly logger = new Logger(CommsService.name);

  constructor(
    private readonly flags: FeatureFlagService,
    private readonly providers: ProviderRegistryService,
    private readonly controlApi: ControlApiClient,
  ) {}

  async sendSms(
    tenantId: string,
    input: { to: string; message: string; senderId?: string },
  ): Promise<{ messageId: string; providerId: string }> {
    await this.flags.assertEnabled(tenantId, 'comms.sms');
    const gw = await this.providers.resolveSms(tenantId);
    if (gw.id === 'disabled') {
      throw new ForbiddenException('SMS provider unavailable');
    }
    const result = await gw.send({
      to: input.to,
      message: input.message,
      senderId: input.senderId,
      ...( { tenantId } as any ),
    });
    await this.reportUsage(tenantId, 'sms_segments', 1);
    return { messageId: result.messageId, providerId: gw.id };
  }

  async sendEmail(
    tenantId: string,
    input: { to: string | string[]; subject: string; html?: string; text?: string },
  ): Promise<{ messageId: string; providerId: string }> {
    await this.flags.assertEnabled(tenantId, 'comms.email');
    const gw =
      tenantId === '__platform__'
        ? await this.providers.resolveEmailForOnboarding()
        : await this.providers.resolveEmail(tenantId);
    if (gw.id === 'disabled') {
      throw new ForbiddenException('Email provider unavailable');
    }
    const result = await (gw as any).send({ ...input, tenantId });
    await this.reportUsage(tenantId, 'email_sends', 1);
    return { messageId: result.messageId, providerId: gw.id };
  }

  /**
   * Verify the active email adapter can reach its vendor (SMTP auth for cPanel).
   * Control plane delegates here because SMTP must be tested from the school
   * server network — not from serverless control hosts that block ports 587/465.
   */
  async verifyEmailProvider(
    tenantId = '__platform__',
  ): Promise<{ ok: boolean; message: string; providerId: string }> {
    const gw =
      tenantId === '__platform__'
        ? await this.providers.resolveEmailForOnboarding()
        : await this.providers.resolveEmail(tenantId);
    if (gw.id === 'disabled') {
      return {
        ok: false,
        message: 'Email provider unavailable or not configured in control vault',
        providerId: 'disabled',
      };
    }
    if (gw instanceof CpanelEmailGateway) {
      try {
        await gw.verify(tenantId);
        return {
          ok: true,
          message: `cPanel SMTP authenticated (verified from school server)`,
          providerId: gw.id,
        };
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        const serverlessHint =
          process.env.VERCEL === '1'
            ? ' The hosted control plane cannot send outbound SMTP — deploy the Nest school server on a VPS (or use Resend).'
            : '';
        return {
          ok: false,
          message: `${msg}.${serverlessHint}`,
          providerId: gw.id,
        };
      }
    }
    return {
      ok: true,
      message: `Provider ${gw.id} registered (live verify not implemented on Nest)`,
      providerId: gw.id,
    };
  }

  /** Critical account email used before a tenant has any feature entitlements. */
  async sendTransactionalEmail(
    tenantId: string,
    input: { to: string | string[]; subject: string; html?: string; text?: string },
  ): Promise<{ messageId: string; providerId: string }> {
    const printDevelopmentEmail = (reason: string) => {
      if (process.env.NODE_ENV === 'production') return false;
      this.logger.log(
        `[DEV EMAIL] ${reason}\nTo: ${input.to}\nSubject: ${input.subject}\nText: ${input.text || '(html email)'}`,
      );
      return true;
    };
    const gw = await this.providers.resolveEmail(tenantId);
    if (gw.id === 'disabled' && printDevelopmentEmail('email provider unavailable')) {
      return { messageId: `dev_console_${Date.now()}`, providerId: 'console' };
    }
    if (gw.id === 'disabled') {
      throw new ForbiddenException('Transactional email provider unavailable');
    }
    try {
      const result = await (gw as any).send({ ...input, tenantId });
      return { messageId: result.messageId, providerId: gw.id };
    } catch (error) {
      if (printDevelopmentEmail(error instanceof Error ? error.message : 'email dispatch failed')) {
        return { messageId: `dev_console_${Date.now()}`, providerId: 'console' };
      }
      throw error;
    }
  }

  /** Best-effort notify — never throws to callers (payment settle path). */
  async notifyPaymentSafe(
    tenantId: string,
    opts: { phone?: string; email?: string; amount: number; reference: string },
  ) {
    const msg = `Payment of ${opts.amount} received. Ref: ${opts.reference}`;
    if (opts.phone) {
      try {
        if (await this.flags.resolve(tenantId, 'comms.sms')) {
          await this.sendSms(tenantId, { to: opts.phone, message: msg });
        }
      } catch (err) {
        this.logger.warn(`SMS notify failed: ${err instanceof Error ? err.message : err}`);
      }
    }
    if (opts.email) {
      try {
        if (await this.flags.resolve(tenantId, 'comms.email')) {
          await this.sendEmail(tenantId, {
            to: opts.email,
            subject: 'Fee payment received',
            text: msg,
            html: `<p>${msg}</p>`,
          });
        }
      } catch (err) {
        this.logger.warn(`Email notify failed: ${err instanceof Error ? err.message : err}`);
      }
    }
  }

  private async reportUsage(tenantId: string, meterKey: string, quantity: number) {
    if (!this.controlApi.isConfigured()) return;
    try {
      await this.controlApi.postUsage({
        tenantId,
        meterKey,
        quantity,
        eventId: `${meterKey}_${tenantId}_${Date.now()}`,
      });
    } catch {
      /* non-fatal */
    }
  }
}
