import { Body, Controller, Get, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { SecondaryService } from './secondary.service';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';

@Controller('secondary')
export class SecondaryController {
  constructor(private readonly secondary: SecondaryService) {}

  @Get('career')
  @RequireFeature('secondary.career')
  @RequirePermissions('results:read', 'classes:read')
  async list(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims, @Query('studentId') studentId?: string) {
    return ok('Career portfolio', await this.secondary.listCareer(tenantId, studentId, user.user_id || user.sub));
  }

  @Post('career')
  @RequireFeature('secondary.career')
  @RequirePermissions('results:create', 'classes:manage')
  async add(
    @TenantId() tenantId: string,
    @Body() body: { studentId: string; title: string; description?: string; category?: string; url?: string },
  ) {
    return ok('Entry added', await this.secondary.addCareerEntry(tenantId, body));
  }
}
