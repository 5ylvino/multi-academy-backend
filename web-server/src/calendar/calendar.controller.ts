import { Body, Controller, Delete, Get, Param, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { CalendarService } from './calendar.service';

@Controller('calendar')
@RequireFeature('comms.calendar')
export class CalendarController {
  constructor(private readonly calendar: CalendarService) {}

  @Get('events')
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('from') from?: string,
    @Query('to') to?: string,
  ) {
    return ok(
      'Calendar events',
      await this.calendar.list(tenantId, from, to, user.user_id || user.sub),
    );
  }

  @Post('events')
  @RequirePermissions('calendar:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      title: string;
      description?: string;
      eventType?: string;
      startsAt: string;
      endsAt?: string;
      allDay?: boolean;
      audience?: string;
      audienceRef?: string;
      location?: string;
    },
  ) {
    return ok(
      'Event created',
      await this.calendar.create(tenantId, user.user_id || user.sub, body),
    );
  }

  @Delete('events/:id')
  @RequirePermissions('calendar:manage')
  async remove(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Event deleted', await this.calendar.remove(tenantId, id));
  }
}
