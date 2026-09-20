import { Body, Controller, Delete, Get, Param, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { TimetableService } from './timetable.service';

@Controller('timetable')
@RequireFeature('academic.timetable')
export class TimetableController {
  constructor(private readonly timetable: TimetableService) {}

  @Get()
  @RequirePermissions('timetable:read')
  async list(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims, @Query('classId') classId?: string) {
    return ok('Timetable', await this.timetable.list(tenantId, user.user_id || user.sub, classId));
  }

  @Post()
  @RequirePermissions('timetable:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      classId: string;
      subjectId?: string;
      teacherId?: string;
      dayOfWeek: number;
      startTime: string;
      endTime: string;
      room?: string;
    },
  ) {
    return ok('Slot created', await this.timetable.create(tenantId, user.user_id || user.sub, body));
  }

  @Delete(':id')
  @RequirePermissions('timetable:manage')
  async remove(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims, @Param('id') id: string) {
    return ok('Slot deleted', await this.timetable.remove(tenantId, user.user_id || user.sub, id));
  }
}
