import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { StudentPortalService } from './student-portal.service';

@Controller('portal/student')
@RequireFeature('portal.student')
export class StudentPortalController {
  constructor(private readonly portal: StudentPortalService) {}

  @Get('overview')
  async overview(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    const userId = user.user_id || user.sub;
    return ok(
      'Student portal overview',
      await this.portal.getOverview(tenantId, userId),
    );
  }

  @Get('results')
  async results(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    const userId = user.user_id || user.sub;
    return ok('Student results', await this.portal.getResults(tenantId, userId));
  }

  @Get('assignments')
  async assignments(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    const userId = user.user_id || user.sub;
    return ok('Student assignments', await this.portal.getAssignments(tenantId, userId));
  }

  @Post('assignments/:id/submit')
  async submitAssignment(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: { note?: string },
  ) {
    const userId = user.user_id || user.sub;
    return ok(
      'Assignment submitted',
      await this.portal.submitAssignment(tenantId, userId, id, body?.note),
    );
  }

  @Get('schedule')
  async schedule(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    const userId = user.user_id || user.sub;
    return ok('Student schedule', await this.portal.getSchedule(tenantId, userId));
  }
}
