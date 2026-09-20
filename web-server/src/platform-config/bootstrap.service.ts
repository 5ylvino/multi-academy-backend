import { Injectable, UnauthorizedException } from '@nestjs/common';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import {
  effectivePermissions,
  effectiveSchoolLevel,
} from '../common/auth/session-profile.util';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { OrganizationsService } from '../organizations/organizations.service';
import { FeatureFlagService } from './feature-flag.service';
import { ProviderRegistryService } from './providers/provider-registry.service';

/** Single round-trip payload for school app startup (user + org + effective config). */
@Injectable()
export class BootstrapService {
  constructor(
    private readonly featureFlags: FeatureFlagService,
    private readonly providers: ProviderRegistryService,
    private readonly controlPlane: ControlPlaneService,
    private readonly organizations: OrganizationsService,
  ) {}

  async getBootstrap(user: AuthUserClaims) {
    const tenantId = user.tenant_id;
    const userId = user.user_id || user.sub;

    const [effective, providerStatus, tenant, dbUser, organization] =
      await Promise.all([
        this.featureFlags.getEffectiveFeatures(tenantId),
        this.providers.getProviderStatus(tenantId),
        this.controlPlane.getTenantById(tenantId),
        this.controlPlane.getUserById(tenantId, userId),
        this.organizations.getCurrentOrganization(tenantId),
      ]);

    if (!tenant) {
      throw new UnauthorizedException('Invalid tenant context');
    }
    if (!dbUser) {
      throw new UnauthorizedException('User not found in tenant');
    }

    const permissions = effectivePermissions(dbUser.roles, dbUser.permissions);
    const tenantSlug = tenant.slug;

    const orgBranding = organization
      ? {
          id: organization.id,
          slug: tenantSlug,
          name: organization.name,
          logo: organization.logo ?? null,
          motto: organization.motto ?? null,
          brandColor: organization.brandColor ?? null,
          loginPageConfig: organization.loginPageConfig ?? null,
        }
      : null;

    return {
      user: {
        id: dbUser.id,
        email: dbUser.email,
        name: dbUser.name,
        phone: dbUser.phone ?? null,
        role: dbUser.roles[0],
        roles: dbUser.roles,
        tenantSlug,
        schoolLevel: effectiveSchoolLevel(
          dbUser.schoolLevel,
          dbUser.roles,
          tenant.schoolLevels,
        ),
        permissions,
        capabilities: dbUser.capabilities,
        configVersion: effective.configVersion,
      },
      organization: orgBranding,
      features: effective.features,
      status: effective.status,
      subscription: effective.subscription,
      providers: effective.providers,
      enforcement: effective.enforcement,
      maintenance: effective.maintenance,
      configVersion: effective.configVersion,
      providerStatus,
    };
  }
}
