import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { Transform } from 'class-transformer';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { MeetingsService } from './meetings.service';
import { IsArray, IsBoolean, IsISO8601, IsOptional, IsString, MaxLength } from 'class-validator';

function toIso8601({ value }: { value: unknown }) {
  if (value === '' || value == null) return undefined;
  if (typeof value !== 'string') return value;
  const normalized = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) ? `${value}:00` : value;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
}

class CreateMeetingDto {
  @IsString()
  @MaxLength(255)
  title!: string;

  @Transform(toIso8601)
  @IsISO8601()
  startsAt!: string;

  @Transform(toIso8601)
  @IsOptional()
  @IsISO8601()
  endsAt?: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  attendees?: string[];

  @IsOptional()
  @IsBoolean()
  syncCalendar?: boolean;

  @IsOptional()
  @IsString()
  classId?: string;

  @IsOptional()
  @IsString()
  subjectId?: string;

  @IsOptional()
  @IsBoolean()
  audioOnly?: boolean;

  @IsOptional()
  @IsString()
  kind?: string;
}

class JoinMeetingDto {
  @IsOptional()
  @IsBoolean()
  audioOnly?: boolean;
}

class SaveRecordingDto {
  @IsOptional()
  @IsString()
  url?: string;

  @IsOptional()
  @IsBoolean()
  audioOnly?: boolean;
}

@Controller('meetings')
@RequireFeature('comms.meetings')
export class MeetingsController {
  constructor(private readonly meetings: MeetingsService) {}

  @Get()
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Meetings',
      await this.meetings.list(tenantId, user.user_id || user.sub),
    );
  }

  @Get('recipients')
  async recipients(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Meeting recipients',
      await this.meetings.listRecipients(tenantId, user.user_id || user.sub),
    );
  }

  @Get(':id')
  async getOne(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Meeting',
      await this.meetings.get(tenantId, user.user_id || user.sub, id),
    );
  }

  @Post()
  @RequirePermissions('correspondence:manage', 'classes:update', 'classes:create')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateMeetingDto,
  ) {
    return ok(
      'Meeting created',
      await this.meetings.create(tenantId, user.user_id || user.sub, body),
    );
  }

  @Post(':id/join')
  async join(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: JoinMeetingDto,
  ) {
    return ok(
      'Joined live class',
      await this.meetings.join(tenantId, user.user_id || user.sub, id, {
        audioOnly: body?.audioOnly,
      }),
    );
  }

  @Post(':id/leave')
  async leave(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Left live class',
      await this.meetings.leave(tenantId, user.user_id || user.sub, id),
    );
  }

  @Post(':id/recording')
  @RequirePermissions('correspondence:manage', 'classes:update')
  async recording(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: SaveRecordingDto,
  ) {
    return ok(
      'Recording saved',
      await this.meetings.saveRecording(
        tenantId,
        user.user_id || user.sub,
        id,
        body,
      ),
    );
  }
}
