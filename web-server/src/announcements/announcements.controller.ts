import { Body, Controller, Delete, Get, Param, Patch, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { AnnouncementsService } from './announcements.service';
import { CreateAnnouncementDto, UpdateAnnouncementDto } from './dto/announcements.dto';
import { RequireFeature } from '../platform-config/require-feature.decorator';

@Controller('announcements')
@RequireFeature('comms.announcements')
export class AnnouncementsController {
  constructor(private readonly announcementsService: AnnouncementsService) {}

  @Get()
  @RequirePermissions('announcements:read', 'announcements:manage')
  async list(@TenantId() tenantId: string) {
    return ok('Announcements', await this.announcementsService.list(tenantId));
  }

  @Post()
  @RequirePermissions('announcements:create')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateAnnouncementDto,
  ) {
    return ok(
      'Announcement created',
      await this.announcementsService.create(tenantId, user.user_id || user.sub, body),
    );
  }

  @Get('critical/pending')
  async pendingCritical(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Pending critical announcements',
      await this.announcementsService.pendingCritical(tenantId, user.user_id || user.sub),
    );
  }

  @Post(':id/acknowledge')
  async acknowledge(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Announcement acknowledged',
      await this.announcementsService.acknowledge(tenantId, user.user_id || user.sub, id),
    );
  }

  @Patch(':id')
  @RequirePermissions('announcements:update')
  async update(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpdateAnnouncementDto,
  ) {
    return ok('Announcement updated', await this.announcementsService.update(tenantId, id, body));
  }

  @Delete(':id')
  @RequirePermissions('announcements:delete')
  async remove(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Announcement deleted', await this.announcementsService.remove(tenantId, id));
  }
}
