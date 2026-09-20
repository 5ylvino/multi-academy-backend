import { ForbiddenException } from '@nestjs/common';
import { FeatureFlagService } from './feature-flag.service';
import type { RuntimeConfigService } from './runtime-config.service';
import type { TenantRuntimeConfig } from './runtime-config.types';

/**
 * Flag resolution decides whether a school can use a paid feature, and whether a
 * killed feature really stays off. The precedence is:
 *   kill switch → capability freeze → explicit control value → foundation default
 */
function makeEnforcement(
  overrides: Partial<TenantRuntimeConfig['enforcement']> = {},
): TenantRuntimeConfig['enforcement'] {
  return {
    schoolBlacklisted: false,
    blockedUserIds: [],
    blockedEmails: [],
    blockedIpCidrs: [],
    capabilityFreezes: [],
    denyLogin: false,
    ...overrides,
  };
}

function makeConfig(
  overrides: Partial<TenantRuntimeConfig> = {},
): TenantRuntimeConfig {
  return {
    tenantId: 'tenant-1',
    slug: 'tenant-1',
    status: 'active',
    features: {},
    quotas: {},
    killSwitches: [],
    providers: {},
    subscription: null,
    enforcement: makeEnforcement(),
    maintenance: null,
    updatedAt: '2026-07-28T00:00:00Z',
    configVersion: 1,
    ...overrides,
  };
}

function makeService(config: TenantRuntimeConfig) {
  const runtimeConfig = {
    getConfig: jest.fn().mockResolvedValue(config),
    peekCached: jest.fn().mockReturnValue(config),
  } as unknown as RuntimeConfigService;
  return new FeatureFlagService(runtimeConfig);
}

describe('FeatureFlagService.resolve', () => {
  it('enables a foundation feature when control returns nothing', async () => {
    const service = makeService(makeConfig());
    await expect(service.resolve('t1', 'auth.login')).resolves.toBe(true);
    await expect(
      service.resolve('t1', 'academic.grading_matrix'),
    ).resolves.toBe(true);
  });

  it('leaves a gated feature off when control returns nothing', async () => {
    const service = makeService(makeConfig());
    await expect(service.resolve('t1', 'ai.tutor')).resolves.toBe(false);
  });

  it('uses peekCached when JWT config_version matches — no getConfig call', async () => {
    const runtimeConfig = {
      getConfig: jest.fn(),
      peekCached: jest.fn().mockReturnValue(
        makeConfig({ configVersion: 5, features: { 'ai.tutor': true } }),
      ),
    } as unknown as RuntimeConfigService;
    const service = new FeatureFlagService(runtimeConfig);
    await expect(
      service.resolve('t1', 'ai.tutor', { config_version: 5 }),
    ).resolves.toBe(true);
    expect(runtimeConfig.getConfig).not.toHaveBeenCalled();
  });

  it('honours an explicit enable from control', async () => {
    const service = makeService(makeConfig({ features: { 'ai.tutor': true } }));
    await expect(service.resolve('t1', 'ai.tutor')).resolves.toBe(true);
  });

  it('honours an explicit disable even for a foundation feature', async () => {
    const service = makeService(
      makeConfig({ features: { 'auth.login': false } }),
    );
    await expect(service.resolve('t1', 'auth.login')).resolves.toBe(false);
  });

  describe('kill switches', () => {
    it('override an explicit enable', async () => {
      const service = makeService(
        makeConfig({
          features: { 'ai.tutor': true },
          killSwitches: ['ai.tutor'],
        }),
      );
      await expect(service.resolve('t1', 'ai.tutor')).resolves.toBe(false);
    });

    it('override a foundation default', async () => {
      const service = makeService(makeConfig({ killSwitches: ['auth.login'] }));
      await expect(service.resolve('t1', 'auth.login')).resolves.toBe(false);
    });
  });

  describe('capability freezes', () => {
    it('disable an exact key', async () => {
      const service = makeService(
        makeConfig({
          features: { 'fees.payments_online': true },
          enforcement: makeEnforcement({
            capabilityFreezes: ['fees.payments_online'],
          }),
        }),
      );
      await expect(service.resolve('t1', 'fees.payments_online')).resolves.toBe(
        false,
      );
    });

    it('disable a whole namespace via a wildcard', async () => {
      const service = makeService(
        makeConfig({
          features: { 'fees.invoices': true, 'fees.refunds': true },
          enforcement: makeEnforcement({ capabilityFreezes: ['fees.*'] }),
        }),
      );
      await expect(service.resolve('t1', 'fees.invoices')).resolves.toBe(false);
      await expect(service.resolve('t1', 'fees.refunds')).resolves.toBe(false);
    });

    it('do not leak past the namespace boundary', async () => {
      const service = makeService(
        makeConfig({
          features: { 'academic.results': true },
          enforcement: makeEnforcement({ capabilityFreezes: ['fees.*'] }),
        }),
      );
      await expect(service.resolve('t1', 'academic.results')).resolves.toBe(
        true,
      );
    });
  });

  it('tolerates a config missing the optional collections', async () => {
    const bare = {
      tenantId: 't1',
      status: 'active',
      features: {},
      configVersion: 1,
    } as unknown as TenantRuntimeConfig;
    const service = makeService(bare);
    await expect(service.resolve('t1', 'auth.login')).resolves.toBe(true);
  });
});

describe('FeatureFlagService.assertEnabled', () => {
  it('passes silently when enabled', async () => {
    const service = makeService(makeConfig());
    await expect(
      service.assertEnabled('t1', 'auth.login'),
    ).resolves.toBeUndefined();
  });

  it('throws Forbidden naming the feature when disabled', async () => {
    const service = makeService(makeConfig());
    await expect(service.assertEnabled('t1', 'ai.tutor')).rejects.toThrow(
      ForbiddenException,
    );
    await expect(service.assertEnabled('t1', 'ai.tutor')).rejects.toThrow(
      'ai.tutor',
    );
  });
});

describe('FeatureFlagService.getEffectiveFeatures', () => {
  it('includes every foundation default as enabled', async () => {
    const service = makeService(makeConfig());
    const { features } = await service.getEffectiveFeatures('t1');
    expect(features['auth.login']).toBe(true);
    expect(features['fees.invoices']).toBe(true);
    expect(features['portal.parent']).toBe(true);
  });

  it('lets control override a foundation default', async () => {
    const service = makeService(
      makeConfig({ features: { 'auth.login': false } }),
    );
    const { features } = await service.getEffectiveFeatures('t1');
    expect(features['auth.login']).toBe(false);
  });

  it('applies kill switches last so they cannot be overridden', async () => {
    const service = makeService(
      makeConfig({
        features: { 'ai.tutor': true },
        killSwitches: ['ai.tutor'],
      }),
    );
    const { features } = await service.getEffectiveFeatures('t1');
    expect(features['ai.tutor']).toBe(false);
  });

  it('applies wildcard freezes across the resolved map', async () => {
    const service = makeService(
      makeConfig({
        enforcement: makeEnforcement({ capabilityFreezes: ['fees.*'] }),
      }),
    );
    const { features } = await service.getEffectiveFeatures('t1');
    expect(features['fees.invoices']).toBe(false);
    expect(features['fees.payments_manual']).toBe(false);
    expect(features['auth.login']).toBe(true);
  });

  it('surfaces the config version so clients can detect staleness', async () => {
    const service = makeService(makeConfig({ configVersion: 42 }));
    const result = await service.getEffectiveFeatures('t1');
    expect(result.configVersion).toBe(42);
  });

  it('normalises a missing maintenance window to null', async () => {
    const service = makeService(makeConfig({ maintenance: undefined }));
    const result = await service.getEffectiveFeatures('t1');
    expect(result.maintenance).toBeNull();
  });
});
