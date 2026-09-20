import { Body, Controller, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { SyncService } from './sync.service';

@Controller('sync')
@RequireFeature('comms.offline_sync')
export class SyncController {
  constructor(private readonly sync: SyncService) {}

  @Post('operations')
  @RequirePermissions('sync:write')
  async apply(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: {
      operationId: string;
      entity: string;
      action: 'create' | 'update';
      payload: Record<string, unknown>;
      clientUpdatedAt?: string;
    },
  ) {
    return ok('Offline operation accepted', await this.sync.apply(tenantId, user.user_id || user.sub, body));
  }
}
