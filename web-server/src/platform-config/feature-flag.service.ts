import { ForbiddenException, Injectable } from '@nestjs/common';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RuntimeConfigService } from './runtime-config.service';
import { policySnapshotMatches } from './policy-snapshot.util';
import type { TenantRuntimeConfig } from './runtime-config.types';

type PolicyClaims = Pick<
  AuthUserClaims,
  'config_version' | 'features_digest' | 'tenant_status'
>;

/**
 * Foundation features that stay on when control returns an empty feature map
 * (local-dev fallback without FastAPI). Gated / Phase-2+ features stay off.
 */
const FOUNDATION_DEFAULTS_ON = new Set([
  'auth.login',
  'auth.register',
  'auth.password_reset',
  'auth.biometric_webauthn',
  'auth.remember_me',
  'users.management',
  'users.passport_photo',
  'org.profile',
  'org.branding',
  'org.subscription_view',
  'audit.logs',
  'rbac.permissions',
  'academic.sessions_terms',
  'academic.classes',
  'academic.subjects',
  'academic.enrollments',
  'academic.assignments',
  'academic.results',
  'academic.results_approve',
  'academic.scheme_of_work',
  'academic.grading_matrix',
  'attendance.students',
  'attendance.staff',
  'attendance.manual_codes',
  'attendance.geo_verification',
  'attendance.auto_absent_eod',
  'fees.structures',
  'fees.invoices',
  'fees.payments_manual',
  'fees.scholarships',
  'fees.refunds',
  'comms.announcements',
  'comms.in_app_notifications',
  'reports.view',
  'users.parent_activation',
  'ops.safeguarding',
  'comms.inbox',
  'comms.messaging',
  'comms.pta',
  'portal.parent',
]);

@Injectable()
export class FeatureFlagService {
  constructor(private readonly runtimeConfig: RuntimeConfigService) {}

  resolveFromConfig(config: TenantRuntimeConfig, featureKey: string): boolean {
    if (config.killSwitches?.includes(featureKey)) {
      return false;
    }
    if (config.enforcement?.capabilityFreezes?.includes(featureKey)) {
      return false;
    }
    for (const freeze of config.enforcement?.capabilityFreezes || []) {
      if (freeze.endsWith('*') && featureKey.startsWith(freeze.slice(0, -1))) {
        return false;
      }
    }

    if (Object.prototype.hasOwnProperty.call(config.features, featureKey)) {
      return Boolean(config.features[featureKey]);
    }

    return FOUNDATION_DEFAULTS_ON.has(featureKey);
  }

  async resolve(
    tenantId: string,
    featureKey: string,
    claims?: PolicyClaims,
  ): Promise<boolean> {
    if (
      tenantId === '__registration__' &&
      process.env.NODE_ENV !== 'production' &&
      FOUNDATION_DEFAULTS_ON.has(featureKey)
    ) {
      return true;
    }

    const peek = this.runtimeConfig.peekCached(tenantId);
    if (
      policySnapshotMatches(peek, {
        configVersion: Number(claims?.config_version),
        featuresDigest: claims?.features_digest,
      })
    ) {
      return this.resolveFromConfig(peek, featureKey);
    }

    const config = await this.runtimeConfig.getConfig(tenantId);
    return this.resolveFromConfig(config, featureKey);
  }

  async assertEnabled(
    tenantId: string,
    featureKey: string,
    claims?: PolicyClaims,
  ): Promise<void> {
    const on = await this.resolve(tenantId, featureKey, claims);
    if (!on) {
      throw new ForbiddenException(`Feature disabled: ${featureKey}`);
    }
  }

  async getEffectiveFeatures(tenantId: string): Promise<{
    features: Record<string, boolean>;
    status: string;
    subscription: TenantRuntimeConfig['subscription'] | null;
    providers: TenantRuntimeConfig['providers'];
    enforcement: TenantRuntimeConfig['enforcement'];
    maintenance: TenantRuntimeConfig['maintenance'];
    configVersion: number;
  }> {
    const config = await this.runtimeConfig.getConfig(tenantId);
    const features: Record<string, boolean> = {
      ...Object.fromEntries([...FOUNDATION_DEFAULTS_ON].map((k) => [k, true])),
      ...config.features,
    };

    for (const key of config.killSwitches || []) {
      features[key] = false;
    }
    for (const freeze of config.enforcement?.capabilityFreezes || []) {
      if (freeze.endsWith('*')) {
        const prefix = freeze.slice(0, -1);
        for (const k of Object.keys(features)) {
          if (k.startsWith(prefix)) features[k] = false;
        }
      } else {
        features[freeze] = false;
      }
    }

    return {
      features,
      status: config.status,
      subscription: config.subscription,
      providers: config.providers,
      enforcement: config.enforcement,
      maintenance: config.maintenance ?? null,
      configVersion: config.configVersion,
    };
  }
}
