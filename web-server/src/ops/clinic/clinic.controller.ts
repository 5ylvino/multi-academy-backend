import { Body, Controller, Get, Post, Query } from '@nestjs/common';
import { ok } from '../../common/types/api-response';
import { TenantId } from '../../common/auth/tenant-id.decorator';
import { CurrentUser } from '../../common/auth/current-user.decorator';
import { AuthUserClaims } from '../../common/auth/auth-user.interface';
import { RequireFeature } from '../../platform-config/require-feature.decorator';
import { RequirePermissions } from '../../common/auth/require-permissions.decorator';
import { ClinicService } from './clinic.service';

@Controller('ops/clinic')
@RequireFeature('ops.clinic')
export class ClinicController {
  constructor(private readonly clinic: ClinicService) {}

  @Get('visits')
  @RequirePermissions('clinic:read')
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('studentId') studentId?: string,
  ) {
    return ok(
      'Clinic visits',
      await this.clinic.list(tenantId, user.user_id || user.sub, studentId),
    );
  }

  @Post('visits')
  @RequirePermissions('clinic:manage')
  async log(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      studentId: string;
      symptoms?: string;
      treatment?: string;
      severity?: string;
      nurseId?: string;
    },
  ) {
    return ok(
      'Visit logged',
      await this.clinic.logVisit(tenantId, user.user_id || user.sub, body),
    );
  }
}
