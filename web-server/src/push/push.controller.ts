import { Body, Controller, Get, Post } from '@nestjs/common';
import { ForbiddenException } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { ProviderRegistryService } from '../platform-config/providers/provider-registry.service';
import { PushService } from './push.service';

@Controller('push')
@RequireFeature('comms.push')
export class PushController {
  constructor(
    private readonly flags: FeatureFlagService,
    private readonly providers: ProviderRegistryService,
    private readonly push: PushService,
  ) {}

  @Post('register')
  @RequirePermissions('push:register')
  async register(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { token: string; platform?: string },
  ) {
    return ok('Token registered', await this.push.registerToken(tenantId, user.sub, body));
  }

  @Get('tokens')
  @RequirePermissions('notifications:manage', 'organization:update')
  async listTokens(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Device tokens', await this.push.listTokensForUser(tenantId, user.sub));
  }

  @Get('preferences')
  @RequirePermissions('push:register')
  async preferences(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Push preferences', await this.push.getPreferences(tenantId, user.user_id || user.sub));
  }

  @Post('preferences')
  @RequirePermissions('push:register')
  async updatePreferences(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { enabled?: boolean; categories?: Record<string, boolean> },
  ) {
    return ok('Push preferences updated', await this.push.updatePreferences(tenantId, user.user_id || user.sub, body));
  }

  @Post('send')
  @RequirePermissions('push:send')
  async send(
    @TenantId() tenantId: string,
    @Body()
    body: { tokens: string[]; title: string; body: string; data?: Record<string, string> },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.push');
    const gw = await this.providers.resolvePush(tenantId);
    if (gw.id === 'disabled') {
      throw new ForbiddenException('Push provider unavailable');
    }
    const tokens = await this.push.resolveSendTokens(tenantId, body.tokens);
    if (!tokens.length) throw new ForbiddenException('No registered push tokens available');
    const result = await gw.send({ ...body, tokens });
    return ok('Push sent', { ...result, providerId: gw.id });
  }
}
