import { Body, Controller, Get, Post } from '@nestjs/common';
import { ok } from '../../common/types/api-response';
import { TenantId } from '../../common/auth/tenant-id.decorator';
import { RequireFeature } from '../../platform-config/require-feature.decorator';
import { RequirePermissions } from '../../common/auth/require-permissions.decorator';
import { CurrentUser } from '../../common/auth/current-user.decorator';
import { AuthUserClaims } from '../../common/auth/auth-user.interface';
import { HostelService } from './hostel.service';

@Controller('ops/hostel')
@RequireFeature('ops.hostel')
export class HostelController {
  constructor(private readonly hostel: HostelService) {}

  @Get('houses')
  @RequirePermissions('hostel:read')
  async list(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Hostel houses', await this.hostel.listHouses(tenantId, user.user_id || user.sub));
  }

  @Post('houses')
  @RequirePermissions('hostel:manage')
  async createHouse(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { name: string; gender?: string; capacity?: number },
  ) {
    return ok('House created', await this.hostel.createHouse(tenantId, user.user_id || user.sub, body));
  }

  @Post('beds')
  @RequirePermissions('hostel:manage')
  async addBed(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { houseId: string; label: string },
  ) {
    return ok('Bed added', await this.hostel.addBed(tenantId, user.user_id || user.sub, body));
  }

  @Post('allocations')
  @RequirePermissions('hostel:manage')
  async allocate(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { bedId: string; studentId: string; fromDate?: string; toDate?: string },
  ) {
    return ok('Allocated', await this.hostel.allocate(tenantId, user.user_id || user.sub, body));
  }
}
