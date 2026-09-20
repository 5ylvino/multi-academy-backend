import { Body, Controller, Get, Post } from '@nestjs/common';
import { ok } from '../../common/types/api-response';
import { TenantId } from '../../common/auth/tenant-id.decorator';
import { CurrentUser } from '../../common/auth/current-user.decorator';
import { AuthUserClaims } from '../../common/auth/auth-user.interface';
import { RequireFeature } from '../../platform-config/require-feature.decorator';
import { RequirePermissions } from '../../common/auth/require-permissions.decorator';
import { TransportService } from './transport.service';

@Controller('ops/transport')
@RequireFeature('ops.transport')
export class TransportController {
  constructor(private readonly transport: TransportService) {}

  @Get('routes')
  @RequirePermissions('transport:read')
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Transport routes',
      await this.transport.listRoutes(tenantId, user.user_id || user.sub),
    );
  }

  @Post('routes')
  @RequirePermissions('transport:manage')
  async createRoute(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { name: string; stops?: string[]; driverId?: string },
  ) {
    return ok(
      'Route created',
      await this.transport.createRoute(tenantId, user.user_id || user.sub, body),
    );
  }

  @Post('vehicles')
  @RequirePermissions('transport:manage')
  async addVehicle(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { plate: string; capacity?: number; routeId?: string },
  ) {
    return ok(
      'Vehicle added',
      await this.transport.addVehicle(tenantId, user.user_id || user.sub, body),
    );
  }

  @Post('assignments')
  @RequirePermissions('transport:manage')
  async assign(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { routeId: string; studentId: string; pickupStop?: string },
  ) {
    return ok(
      'Student assigned',
      await this.transport.assignStudent(tenantId, user.user_id || user.sub, body),
    );
  }
}
