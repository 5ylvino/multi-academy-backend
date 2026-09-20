import { Body, Controller, Get, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { EmergencyBroadcastService } from './emergency-broadcast.service';

@Controller('emergency-broadcasts')
@RequireFeature('comms.emergency_broadcast')
export class EmergencyBroadcastController {
  constructor(private readonly emergency: EmergencyBroadcastService) {}

  @Get()
  @RequirePermissions('announcements:read')
  async list(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Emergency broadcasts', await this.emergency.list(tenantId, user.user_id || user.sub));
  }

  @Post()
  @RequirePermissions('emergency:send')
  async send(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      title: string;
      message: string;
      channels?: ('sms' | 'email')[];
      audience?: string;
      recipients?: { phone?: string; email?: string }[];
    },
  ) {
    return ok(
      'Emergency broadcast sent',
      await this.emergency.broadcast(tenantId, user.user_id || user.sub, body),
    );
  }
}
