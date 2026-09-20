import {
  Body,
  Controller,
  Headers,
  Post,
  Req,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHmac, timingSafeEqual } from 'node:crypto';
import type { Request } from 'express';
import { ok } from '../common/types/api-response';
import { Public } from '../common/auth/public.decorator';
import { RuntimeConfigService } from './runtime-config.service';
import { ProviderSecretsService } from './provider-secrets.service';
import { AuditLogService } from '../common/audit/audit-log.service';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { NotificationsService } from '../notifications/notifications.service';
import { PushService } from '../push/push.service';
import { CommsService } from './comms.service';

/** Reject a signed request whose timestamp is older than this. */
const MAX_SIGNATURE_AGE_SECONDS = 300;

/**
 * Receives control-plane outbox invalidation webhooks.
 *
 * Auth: HMAC-SHA256 over `<timestamp>.<raw body>` in `X-Control-Signature`,
 * keyed by CONTROL_WEBHOOK_SECRET. Falls back to comparing the plain shared
 * secret header when the control plane has not been upgraded to sign yet.
 */
@Controller('internal/control')
export class ControlWebhookController {
  constructor(
    private readonly config: ConfigService,
    private readonly runtimeConfig: RuntimeConfigService,
    private readonly secrets: ProviderSecretsService,
    private readonly audit: AuditLogService,
    private readonly controlPlane: ControlPlaneService,
    private readonly notifications: NotificationsService,
    private readonly push: PushService,
    private readonly comms: CommsService,
  ) {}

  private safeEquals(a: string, b: string): boolean {
    const left = new Uint8Array(Buffer.from(a));
    const right = new Uint8Array(Buffer.from(b));
    // timingSafeEqual throws on a length mismatch, and the throw itself would
    // leak length, so compare lengths first and return a plain false.
    if (left.length !== right.length) return false;
    return timingSafeEqual(left, right);
  }

  private verify(
    req: Request,
    secret: string | undefined,
    signature: string | undefined,
    timestamp: string | undefined,
  ): void {
    const expected = (
      this.config.get<string>('CONTROL_WEBHOOK_SECRET') || ''
    ).trim();
    if (!expected) {
      // No secret configured: only tolerable outside production.
      if (this.config.get<string>('NODE_ENV') === 'production') {
        throw new UnauthorizedException(
          'CONTROL_WEBHOOK_SECRET is not configured',
        );
      }
      return;
    }

    if (signature && timestamp) {
      const age = Math.abs(Date.now() / 1000 - Number(timestamp));
      if (!Number.isFinite(age) || age > MAX_SIGNATURE_AGE_SECONDS) {
        throw new UnauthorizedException('Webhook timestamp outside tolerance');
      }
      // rawBody is populated by NestFactory.create({ rawBody: true }).
      const raw =
        (req as Request & { rawBody?: Buffer }).rawBody ??
        Buffer.from(JSON.stringify(req.body ?? {}));
      const computed = createHmac('sha256', expected)
        .update(`${timestamp}.`)
        .update(new Uint8Array(raw))
        .digest('hex');
      if (!this.safeEquals(computed, signature)) {
        throw new UnauthorizedException('Invalid webhook signature');
      }
      return;
    }

    if (!secret || !this.safeEquals(secret, expected)) {
      throw new UnauthorizedException('Invalid webhook secret');
    }
  }

  @Post('provider-email-ping')
  @Public()
  async providerEmailPing(
    @Req() req: Request,
    @Headers('x-control-webhook-secret') secret: string | undefined,
    @Headers('x-control-signature') signature: string | undefined,
    @Headers('x-control-timestamp') timestamp: string | undefined,
  ) {
    this.verify(req, secret, signature, timestamp);
    const result = await this.comms.verifyEmailProvider('__platform__');
    return {
      ok: result.ok,
      message: result.message,
      providerId: result.providerId,
      healthStatus: result.ok ? 'green' : 'red',
    };
  }

  @Post('config-changed')
  @Public()
  async configChanged(
    @Req() req: Request,
    @Headers('x-control-webhook-secret') secret: string | undefined,
    @Headers('x-control-signature') signature: string | undefined,
    @Headers('x-control-timestamp') timestamp: string | undefined,
    @Body()
    body: {
      eventType?: string;
      aggregateType?: string;
      aggregateId?: string;
      payload?: Record<string, unknown>;
      eventId?: number;
    },
  ) {
    this.verify(req, secret, signature, timestamp);

    if (
      body.eventType === 'trial.notification' ||
      body.eventType === 'billing.notification'
    ) {
      await this.deliverTrialNotification(body);
    }

    const tenantId =
      (body.payload?.tenantId as string) ||
      (body.payload?.tenant_id as string) ||
      (body.aggregateType === 'tenant' ? body.aggregateId : undefined);
    const invalidateAll = Boolean(body.payload?.invalidateAll) || !tenantId;

    if (invalidateAll) {
      this.runtimeConfig.invalidate();
      this.secrets.invalidate();
    } else if (tenantId) {
      this.runtimeConfig.invalidate(tenantId);
      this.secrets.invalidate(tenantId);
    }

    void this.audit
      .log({
        tenantId: tenantId || null,
        method: 'WEBHOOK',
        path: '/api/v1/internal/control/config-changed',
        statusCode: 200,
        payload: {
          event: 'control.outbox.received',
          eventType: body.eventType,
          aggregateType: body.aggregateType,
          aggregateId: body.aggregateId,
          eventId: body.eventId,
          signed: Boolean(signature),
          payload: body.payload,
        },
      })
      .catch(() => undefined);

    return ok('Config cache invalidated', {
      tenantId: tenantId || null,
      invalidated: true,
    });
  }

  private async deliverTrialNotification(body: {
    payload?: Record<string, unknown>;
    eventId?: number;
  }) {
    const payload = body.payload || {};
    const tenantId = String(payload.tenantId || payload.tenant_id || '');
    const email = String(payload.adminEmail || payload.admin_email || '');
    const title = String(payload.title || 'School account update');
    const message = String(payload.message || '');
    if (!tenantId || !message) return;

    const user = email
      ? await this.controlPlane.findUserByEmail(tenantId, email)
      : null;
    if (user) {
      await this.notifications.create({
        tenantId,
        userId: user.id,
        title,
        message,
        type: 'warning',
      });
      try {
        await this.push.sendToUser(tenantId, user.id, {
          title,
          body: message,
          data: { kind: 'trial', eventId: String(body.eventId || '') },
        });
      } catch {
        // Push is best-effort; the in-app notification remains available.
      }
    }
    if (email) {
      try {
        await this.comms.sendTransactionalEmail(tenantId, {
          to: email,
          subject: title,
          text: message,
          html: `<p>${message.replace(/\n/g, '<br />')}</p>`,
        });
      } catch {
        // Notification delivery must not prevent config invalidation.
      }
    }
  }
}
