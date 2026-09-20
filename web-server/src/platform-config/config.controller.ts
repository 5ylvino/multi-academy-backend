import { Controller, Get } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { FeatureFlagService } from './feature-flag.service';
import { ProviderRegistryService } from './providers/provider-registry.service';
import { BootstrapService } from './bootstrap.service';

@Controller('config')
export class ConfigController {
  constructor(
    private readonly featureFlags: FeatureFlagService,
    private readonly providers: ProviderRegistryService,
    private readonly bootstrap: BootstrapService,
  ) {}

  /** Single startup payload: user claims summary + effective config + provider status. */
  @Get('bootstrap')
  async getBootstrap(@CurrentUser() user: AuthUserClaims) {
    const payload = await this.bootstrap.getBootstrap(user);
    return ok('Bootstrap config', payload);
  }

  /** School client: effective flags + status for nav/route gating. */
  @Get('features')
  async getFeatures(@CurrentUser() user: AuthUserClaims) {
    const effective = await this.featureFlags.getEffectiveFeatures(user.tenant_id);
    const providerStatus = await this.providers.getProviderStatus(user.tenant_id);
    return ok('Effective features', {
      ...effective,
      providerStatus,
    });
  }
}
