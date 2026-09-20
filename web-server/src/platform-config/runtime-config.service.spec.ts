import { ConfigService } from '@nestjs/config';
import { RuntimeConfigService } from './runtime-config.service';
import type { ControlApiClient } from './control-api.client';
import type { AuditLogService } from '../common/audit/audit-log.service';
import type { RuntimeConfigRedisStore } from './runtime-config-redis.store';
import type { TenantRuntimeConfig } from './runtime-config.types';

function makeConfig(overrides: Partial<TenantRuntimeConfig> = {}): TenantRuntimeConfig {
  return {
    tenantId: 't1',
    status: 'active',
    features: { 'auth.login': true },
    quotas: {},
    providers: {},
    subscription: { type: 'catalog', planId: 'basic', billingCycle: 'monthly' },
    enforcement: {
      schoolBlacklisted: false,
      blockedUserIds: [],
      blockedEmails: [],
      blockedIpCidrs: [],
      capabilityFreezes: [],
      denyLogin: false,
    },
    killSwitches: [],
    updatedAt: '2026-07-28T00:00:00Z',
    configVersion: 1,
    ...overrides,
  };
}

function makeService(
  client: Partial<ControlApiClient>,
  env: Record<string, string> = {},
) {
  const config = {
    get: (key: string) => env[key],
  } as unknown as ConfigService;
  const audit = {
    log: jest.fn().mockResolvedValue(undefined),
  } as unknown as AuditLogService;
  const redisStore = {
    isEnabled: () => false,
    read: jest.fn().mockResolvedValue(null),
    write: jest.fn().mockResolvedValue(undefined),
    invalidate: jest.fn().mockResolvedValue(undefined),
  } as unknown as RuntimeConfigRedisStore;
  return new RuntimeConfigService(
    client as ControlApiClient,
    config,
    audit,
    redisStore,
  );
}

describe('RuntimeConfigService caching', () => {
  it('serves a warm cache hit without calling control again', async () => {
    const getRuntimeConfig = jest.fn().mockResolvedValue(makeConfig());
    const service = makeService({
      isConfigured: () => true,
      getRuntimeConfig,
    });

    await service.getConfig('t1');
    await service.getConfig('t1');
    expect(getRuntimeConfig).toHaveBeenCalledTimes(1);
  });

  it('coalesces concurrent cold misses onto one control fetch', async () => {
    let resolveFetch!: (v: TenantRuntimeConfig) => void;
    const getRuntimeConfig = jest.fn(
      () =>
        new Promise<TenantRuntimeConfig>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const service = makeService({
      isConfigured: () => true,
      getRuntimeConfig,
    });

    const a = service.getConfig('t1');
    const b = service.getConfig('t1');
    expect(getRuntimeConfig).toHaveBeenCalledTimes(1);
    resolveFetch(makeConfig());
    await Promise.all([a, b]);
  });

  it('invalidate forces the next read to refetch', async () => {
    const getRuntimeConfig = jest
      .fn()
      .mockResolvedValueOnce(makeConfig({ configVersion: 1 }))
      .mockResolvedValueOnce(makeConfig({ configVersion: 2 }));
    const service = makeService({
      isConfigured: () => true,
      getRuntimeConfig,
    });

    expect((await service.getConfig('t1')).configVersion).toBe(1);
    service.invalidate('t1');
    expect((await service.getConfig('t1')).configVersion).toBe(2);
    expect(getRuntimeConfig).toHaveBeenCalledTimes(2);
  });

  it('discards an in-flight write that races with invalidate', async () => {
    let resolveFetch!: (v: TenantRuntimeConfig) => void;
    const getRuntimeConfig = jest.fn(
      () =>
        new Promise<TenantRuntimeConfig>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const service = makeService({
      isConfigured: () => true,
      getRuntimeConfig,
    });

    const pending = service.getConfig('t1');
    service.invalidate('t1');
    resolveFetch(makeConfig({ configVersion: 1 }));
    await pending;

    getRuntimeConfig.mockResolvedValueOnce(makeConfig({ configVersion: 9 }));
    expect((await service.getConfig('t1')).configVersion).toBe(9);
    expect(getRuntimeConfig).toHaveBeenCalledTimes(2);
  });

  it('peekCached returns in-memory config without I/O', async () => {
    const getRuntimeConfig = jest.fn().mockResolvedValue(makeConfig({ configVersion: 7 }));
    const service = makeService({
      isConfigured: () => true,
      getRuntimeConfig,
    });
    expect(service.peekCached('t1')).toBeNull();
    await service.getConfig('t1');
    expect(service.peekCached('t1')?.configVersion).toBe(7);
    expect(getRuntimeConfig).toHaveBeenCalledTimes(1);
  });

  it('returns local fallback when control is not configured', async () => {
    const service = makeService({
      isConfigured: () => false,
      getRuntimeConfig: jest.fn(),
    });
    const cfg = await service.getConfig('school-a');
    expect(cfg.tenantId).toBe('school-a');
    expect(cfg.configVersion).toBe(0);
  });
});
