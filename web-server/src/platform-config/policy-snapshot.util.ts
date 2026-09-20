import { createHash } from 'node:crypto';
import type { TenantRuntimeConfig } from './runtime-config.types';

export type PolicySnapshot = {
  configVersion: number;
  tenantStatus: string;
  featuresDigest: string;
};

/** Compact digest of enabled features + config version for JWT fast path. */
export function buildPolicySnapshot(config: TenantRuntimeConfig): PolicySnapshot {
  const enabled = Object.entries(config.features || {})
    .filter(([, on]) => Boolean(on))
    .map(([key]) => key)
    .sort();
  const featuresDigest = createHash('sha256')
    .update(JSON.stringify({ v: config.configVersion, f: enabled }))
    .digest('hex')
    .slice(0, 16);
  return {
    configVersion: config.configVersion,
    tenantStatus: config.status,
    featuresDigest,
  };
}

export function policySnapshotMatches(
  peek: TenantRuntimeConfig | null,
  snapshot?: Pick<PolicySnapshot, 'configVersion' | 'featuresDigest'>,
): peek is TenantRuntimeConfig {
  if (!peek || !snapshot) return false;
  const tokenVersion = Number(snapshot.configVersion);
  if (!Number.isFinite(tokenVersion) || tokenVersion <= 0) return false;
  if (!snapshot.featuresDigest) {
    return peek.configVersion === tokenVersion;
  }
  const expected = buildPolicySnapshot(peek);
  return (
    expected.configVersion === snapshot.configVersion &&
    expected.featuresDigest === snapshot.featuresDigest
  );
}
