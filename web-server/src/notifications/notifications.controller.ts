import { Controller, Get, Param, Patch, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { NotificationsService } from './notifications.service';
import { RequireFeature } from '../platform-config/require-feature.decorator';

@Controller('notifications')
@RequireFeature('comms.in_app_notifications')
export class NotificationsController {
  constructor(private readonly notificationsService: NotificationsService) {}

  @Get()
  @RequirePermissions('notifications:view', 'notifications:manage')
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('limit') limit?: string,
  ) {
    const data = await this.notificationsService.listForUser(
      tenantId,
      user.user_id,
      limit ? Number(limit) : 30,
    );
    return ok('Notifications', data);
  }

  @Patch(':id/read')
  @RequirePermissions('notifications:view', 'notifications:manage')
  async markRead(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    const data = await this.notificationsService.markRead(tenantId, user.user_id, id);
    return ok('Notification marked read', data);
  }

  @Post('read-all')
  @RequirePermissions('notifications:view', 'notifications:manage')
  async markAll(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    const data = await this.notificationsService.markAllRead(tenantId, user.user_id);
    return ok('All notifications marked read', data);
  }
}
