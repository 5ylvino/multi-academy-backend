import { Body, Controller, Get, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions, RequireRoles } from '../common/auth/require-permissions.decorator';
import { NurseryService } from './nursery.service';

@Controller('nursery')
@RequireRoles('class_teacher', 'head_teacher', 'principal', 'school_admin')
export class NurseryController {
  constructor(private readonly nursery: NurseryService) {}

  @Get('developmental')
  @RequireFeature('nursery.developmental')
  @RequirePermissions('nursery:read')
  async listDev(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Developmental logs', await this.nursery.listDevelopmental(tenantId, user));
  }

  @Post('developmental')
  @RequireFeature('nursery.developmental')
  @RequirePermissions('nursery:manage')
  async addDev(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { studentId: string; domain: string; level: string; notes?: string },
  ) {
    return ok('Logged', await this.nursery.addDevelopmental(tenantId, body, user));
  }

  @Get('wellness')
  @RequireFeature('nursery.wellness')
  @RequirePermissions('nursery:read')
  async listWellness(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Wellness logs', await this.nursery.listWellness(tenantId, user));
  }

  @Post('wellness')
  @RequireFeature('nursery.wellness')
  @RequirePermissions('nursery:manage')
  async addWellness(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      studentId: string;
      mood?: string;
      appetite?: string;
      napMinutes?: number;
      notes?: string;
    },
  ) {
    return ok('Wellness logged', await this.nursery.addWellness(tenantId, body, user));
  }

  @Get('media-moments')
  @RequireFeature('nursery.media_moments')
  @RequirePermissions('nursery:read')
  async listMedia(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Media moments', await this.nursery.listMediaMoments(tenantId, user));
  }

  @Post('media-moments')
  @RequireFeature('nursery.media_moments')
  @RequirePermissions('nursery:manage')
  async addMedia(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { studentId: string; url: string; caption?: string },
  ) {
    return ok('Media moment added', await this.nursery.addMediaMoment(tenantId, body, user));
  }
}
