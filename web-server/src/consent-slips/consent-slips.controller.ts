import { Body, Controller, Get, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { ConsentSlipsService } from './consent-slips.service';

@Controller('consent-slips')
@RequireFeature('comms.consent_slips')
export class ConsentSlipsController {
  constructor(private readonly consent: ConsentSlipsService) {}

  @Get()
  @RequirePermissions('consent:read')
  async list(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Consent slips', await this.consent.list(tenantId, user.user_id || user.sub));
  }

  @Post()
  @RequirePermissions('consent:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { title: string; body: string; slipType?: string },
  ) {
    return ok(
      'Consent slip created',
      await this.consent.create(tenantId, user.user_id || user.sub, body),
    );
  }

  @Post('responses')
  @RequirePermissions('consent:respond')
  async respond(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      slipId: string;
      studentId: string;
      guardianName?: string;
      status: 'approved' | 'denied';
      notes?: string;
    },
  ) {
    return ok('Response recorded', await this.consent.respond(tenantId, user.user_id || user.sub, body));
  }
}
