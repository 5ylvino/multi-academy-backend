import { buildPolicySnapshot, policySnapshotMatches } from './policy-snapshot.util';
import type { TenantRuntimeConfig } from './runtime-config.types';

function cfg(partial: Partial<TenantRuntimeConfig>): TenantRuntimeConfig {
  return {
    tenantId: 't1',
    status: 'active',
    configVersion: 3,
    features: { 'portal.parent': true, 'fees.gateway': false },
    quotas: {},
    enforcement: {
      schoolBlacklisted: false,
      blockedUserIds: [],
      blockedEmails: [],
      blockedIpCidrs: [],
      capabilityFreezes: [],
      denyLogin: false,
    },
    providers: {},
    killSwitches: [],
    updatedAt: new Date().toISOString(),
    subscription: null,
    ...partial,
  };
}

describe('policy-snapshot.util', () => {
  it('builds stable digest for enabled features', () => {
    const a = buildPolicySnapshot(cfg({}));
    const b = buildPolicySnapshot(cfg({}));
    expect(a.featuresDigest).toBe(b.featuresDigest);
    expect(a.tenantStatus).toBe('active');
  });

  it('matches when JWT snapshot equals cached config', () => {
    const peek = cfg({});
    const snap = buildPolicySnapshot(peek);
    expect(
      policySnapshotMatches(peek, {
        configVersion: snap.configVersion,
        featuresDigest: snap.featuresDigest,
      }),
    ).toBe(true);
  });

  it('rejects stale digest after feature change', () => {
    const peek = cfg({ features: { 'portal.parent': true, 'portal.student': true } });
    const old = buildPolicySnapshot(cfg({ features: { 'portal.parent': true } }));
    expect(
      policySnapshotMatches(peek, {
        configVersion: old.configVersion,
        featuresDigest: old.featuresDigest,
      }),
    ).toBe(false);
  });
});
