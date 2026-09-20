import { UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHmac } from 'node:crypto';
import type { Request } from 'express';
import { ControlWebhookController } from './control-webhook.controller';
import type { RuntimeConfigService } from './runtime-config.service';
import type { ProviderSecretsService } from './provider-secrets.service';
import type { AuditLogService } from '../common/audit/audit-log.service';

const SECRET = 'control-webhook-secret-for-tests';

type Body = {
  eventType?: string;
  aggregateType?: string;
  aggregateId?: string;
  payload?: Record<string, unknown>;
  eventId?: number;
};

function build(env: Record<string, string | undefined> = {}) {
  const config = {
    get: (key: string) => ({ CONTROL_WEBHOOK_SECRET: SECRET, ...env })[key],
  } as unknown as ConfigService;

  const runtimeConfig = {
    invalidate: jest.fn(),
  } as unknown as RuntimeConfigService;
  const secrets = { invalidate: jest.fn() } as unknown as ProviderSecretsService;
  const audit = {
    log: jest.fn().mockResolvedValue(undefined),
  } as unknown as AuditLogService;

  const controller = new ControlWebhookController(
    config,
    runtimeConfig,
    secrets,
    audit,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );
  return { controller, runtimeConfig, secrets, audit };
}

function request(body: Body): Request {
  const raw = Buffer.from(JSON.stringify(body));
  return { body, rawBody: raw } as unknown as Request;
}

function sign(body: Body, timestamp: string, secret = SECRET) {
  return createHmac('sha256', secret)
    .update(`${timestamp}.`)
    .update(new Uint8Array(Buffer.from(JSON.stringify(body))))
    .digest('hex');
}

const now = () => String(Math.floor(Date.now() / 1000));

describe('ControlWebhookController auth', () => {
  const body: Body = {
    eventType: 'config.invalidate',
    aggregateType: 'tenant',
    aggregateId: '7',
    payload: { tenantId: 'tenant-7' },
  };

  it('accepts a correctly signed request', async () => {
    const { controller, runtimeConfig } = build();
    const ts = now();
    await expect(
      controller.configChanged(request(body), undefined, sign(body, ts), ts, body),
    ).resolves.toBeDefined();
    expect(runtimeConfig.invalidate).toHaveBeenCalledWith('tenant-7');
  });

  it('rejects a tampered body under a valid signature', async () => {
    const { controller } = build();
    const ts = now();
    const signature = sign(body, ts);
    const tampered = { ...body, payload: { tenantId: 'tenant-999' } };
    await expect(
      controller.configChanged(request(tampered), undefined, signature, ts, tampered),
    ).rejects.toThrow(UnauthorizedException);
  });

  it('rejects a signature made with the wrong secret', async () => {
    const { controller } = build();
    const ts = now();
    await expect(
      controller.configChanged(
        request(body),
        undefined,
        sign(body, ts, 'attacker-secret'),
        ts,
        body,
      ),
    ).rejects.toThrow(UnauthorizedException);
  });

  it('rejects a replayed request outside the timestamp window', async () => {
    const { controller } = build();
    const stale = String(Math.floor(Date.now() / 1000) - 3600);
    await expect(
      controller.configChanged(request(body), undefined, sign(body, stale), stale, body),
    ).rejects.toThrow('timestamp outside tolerance');
  });

  it('rejects a non-numeric timestamp', async () => {
    const { controller } = build();
    await expect(
      controller.configChanged(request(body), undefined, 'deadbeef', 'not-a-number', body),
    ).rejects.toThrow(UnauthorizedException);
  });

  it('falls back to the shared secret header when unsigned', async () => {
    const { controller } = build();
    await expect(
      controller.configChanged(request(body), SECRET, undefined, undefined, body),
    ).resolves.toBeDefined();
  });

  it('rejects a wrong shared secret', async () => {
    const { controller } = build();
    await expect(
      controller.configChanged(request(body), 'wrong-secret', undefined, undefined, body),
    ).rejects.toThrow(UnauthorizedException);
  });

  it('rejects a missing secret when one is configured', async () => {
    const { controller } = build();
    await expect(
      controller.configChanged(request(body), undefined, undefined, undefined, body),
    ).rejects.toThrow(UnauthorizedException);
  });

  it('does not throw on a secret of a different length', async () => {
    // timingSafeEqual throws on length mismatch; the controller must return a
    // clean 401 rather than a 500.
    const { controller } = build();
    await expect(
      controller.configChanged(request(body), 'short', undefined, undefined, body),
    ).rejects.toThrow(UnauthorizedException);
  });

  it('refuses unauthenticated calls in production when no secret is configured', async () => {
    const { controller } = build({
      CONTROL_WEBHOOK_SECRET: '',
      NODE_ENV: 'production',
    });
    await expect(
      controller.configChanged(request(body), undefined, undefined, undefined, body),
    ).rejects.toThrow('not configured');
  });

  it('allows an unconfigured secret outside production', async () => {
    const { controller } = build({
      CONTROL_WEBHOOK_SECRET: '',
      NODE_ENV: 'development',
    });
    await expect(
      controller.configChanged(request(body), undefined, undefined, undefined, body),
    ).resolves.toBeDefined();
  });
});

describe('ControlWebhookController invalidation targeting', () => {
  const ts = now();

  async function invoke(body: Body) {
    const ctx = build();
    await ctx.controller.configChanged(
      request(body),
      undefined,
      sign(body, ts),
      ts,
      body,
    );
    return ctx;
  }

  it('invalidates a single tenant from payload.tenantId', async () => {
    const { runtimeConfig, secrets } = await invoke({
      eventType: 'config.invalidate',
      payload: { tenantId: 'tenant-3' },
    });
    expect(runtimeConfig.invalidate).toHaveBeenCalledWith('tenant-3');
    expect(secrets.invalidate).toHaveBeenCalledWith('tenant-3');
  });

  it('accepts the snake_case payload key', async () => {
    const { runtimeConfig } = await invoke({
      eventType: 'config.invalidate',
      payload: { tenant_id: 'tenant-4' },
    });
    expect(runtimeConfig.invalidate).toHaveBeenCalledWith('tenant-4');
  });

  it('derives the tenant from a tenant-aggregate event', async () => {
    const { runtimeConfig } = await invoke({
      eventType: 'tenant.status',
      aggregateType: 'tenant',
      aggregateId: 'tenant-5',
    });
    expect(runtimeConfig.invalidate).toHaveBeenCalledWith('tenant-5');
  });

  it('flushes everything when no tenant can be identified', async () => {
    const { runtimeConfig, secrets } = await invoke({ eventType: 'config.invalidate' });
    expect(runtimeConfig.invalidate).toHaveBeenCalledWith();
    expect(secrets.invalidate).toHaveBeenCalledWith();
  });

  it('flushes everything when invalidateAll is set', async () => {
    const { runtimeConfig } = await invoke({
      eventType: 'config.invalidate',
      payload: { tenantId: 'tenant-6', invalidateAll: true },
    });
    expect(runtimeConfig.invalidate).toHaveBeenCalledWith();
  });

  it('records an audit entry noting whether the call was signed', async () => {
    const { audit } = await invoke({
      eventType: 'config.invalidate',
      payload: { tenantId: 'tenant-8' },
    });
    expect(audit.log).toHaveBeenCalledWith(
      expect.objectContaining({
        tenantId: 'tenant-8',
        payload: expect.objectContaining({ signed: true }),
      }),
    );
  });
});
