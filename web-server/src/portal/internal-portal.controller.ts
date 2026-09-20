import { Controller, Get, Param, UseGuards } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { Public } from '../common/auth/public.decorator';
import { ServiceJwtGuard } from '../common/auth/service-jwt.guard';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { ParentPortalService } from './parent-portal.service';
import { StudentPortalService } from './student-portal.service';
import { BursarPortalService } from './bursar-portal.service';

/** Service-to-service portal reads — no portal-read round trip. */
@Controller('internal/portal')
@Public()
@UseGuards(ServiceJwtGuard)
export class InternalPortalController {
  constructor(
    private readonly parent: ParentPortalService,
    private readonly student: StudentPortalService,
    private readonly bursar: BursarPortalService,
  ) {}

  @Get('parent/home')
  async parentHome(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Parent portal home', await this.parent.getHomeLocal(tenantId, user.user_id));
  }

  @Get('parent/wards/:studentId/overview')
  async parentWardOverview(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward overview',
      await this.parent.getWardOverviewLocal(tenantId, user.user_id, studentId),
    );
  }

  @Get('student/overview')
  async studentOverview(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    const userId = user.user_id || user.sub;
    return ok('Student portal overview', await this.student.getOverviewLocal(tenantId, userId));
  }

  @Get('bursar/dashboard')
  async bursarDashboard(@TenantId() tenantId: string) {
    return ok('Bursar dashboard', await this.bursar.getDashboardLocal(tenantId));
  }
}
