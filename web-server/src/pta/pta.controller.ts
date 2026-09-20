import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { PtaService } from './pta.service';
import { IsISO8601, IsOptional, IsString, MaxLength, MinLength } from 'class-validator';

class CreateEventDto {
  @IsString()
  @MinLength(3)
  @MaxLength(200)
  title!: string;

  @IsOptional()
  @IsString()
  @MaxLength(4000)
  agenda?: string;

  @IsISO8601()
  startsAt!: string;

  @IsOptional()
  @IsString()
  location?: string;
}

class RsvpDto {
  @IsString()
  @MinLength(1)
  status!: string;
}

@Controller('pta')
@RequireFeature('comms.pta')
export class PtaController {
  constructor(private readonly pta: PtaService) {}

  @Get('events')
  async list(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('PTA events', await this.pta.list(tenantId, user.user_id || user.sub));
  }

  @Post('events')
  @RequirePermissions('correspondence:manage', 'announcements:create')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateEventDto,
  ) {
    return ok('PTA event created', await this.pta.create(tenantId, user.user_id || user.sub, body));
  }

  @Post('events/:id/rsvp')
  async rsvp(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: RsvpDto,
  ) {
    return ok('RSVP saved', await this.pta.rsvp(tenantId, user.user_id || user.sub, id, body.status));
  }

  @Get('events/:id/minutes')
  async minutes(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Minutes', await this.pta.getMinutes(tenantId, id));
  }

  @Post('events/:id/minutes')
  @RequirePermissions('correspondence:manage', 'announcements:create')
  async saveMinutes(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: { minutes?: string; attendanceCount?: number },
  ) {
    return ok(
      'Minutes saved',
      await this.pta.saveMinutes(tenantId, user.user_id || user.sub, id, body),
    );
  }
}
