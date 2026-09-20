import { Body, Controller, Get, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { PrimaryService } from './primary.service';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';

@Controller('primary')
export class PrimaryController {
  constructor(private readonly primary: PrimaryService) {}

  @Get('badges')
  @RequireFeature('primary.badges')
  @RequirePermissions('results:read', 'classes:read')
  async listBadges(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims, @Query('studentId') studentId?: string) {
    return ok('Badges', await this.primary.listBadges(tenantId, studentId, user.user_id || user.sub));
  }

  @Post('badges')
  @RequireFeature('primary.badges')
  @RequirePermissions('results:create', 'classes:manage')
  async awardBadge(
    @TenantId() tenantId: string,
    @Body() body: { studentId: string; badgeType: string; title: string },
  ) {
    return ok('Badge awarded', await this.primary.awardBadge(tenantId, body));
  }

  @Get('literacy')
  @RequireFeature('primary.literacy_log')
  @RequirePermissions('results:read', 'classes:read')
  async listLiteracy(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims, @Query('studentId') studentId?: string) {
    return ok('Literacy log', await this.primary.listLiteracy(tenantId, studentId, user.user_id || user.sub));
  }

  @Post('literacy')
  @RequireFeature('primary.literacy_log')
  @RequirePermissions('results:create', 'classes:manage')
  async logLiteracy(
    @TenantId() tenantId: string,
    @Body() body: { studentId: string; skill: string; level: string; notes?: string },
  ) {
    return ok('Literacy logged', await this.primary.logLiteracy(tenantId, body));
  }
}
