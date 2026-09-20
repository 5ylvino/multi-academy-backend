import { Body, Controller, Get, Post, Query } from '@nestjs/common';
import { Throttle } from '@nestjs/throttler';
import { ok } from '../common/types/api-response';
import { Public } from '../common/auth/public.decorator';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { ParentActivationService } from './parent-activation.service';
import {
  ArrayMaxSize,
  IsArray,
  IsOptional,
  IsString,
  Max,
  Min,
  MinLength,
} from 'class-validator';
import { Type } from 'class-transformer';

class IssueActivationDto {
  @IsString()
  @MinLength(1)
  parentId!: string;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(20)
  @IsString({ each: true })
  studentIds?: string[];

  @IsOptional()
  @Type(() => Number)
  @Min(1)
  @Max(30)
  daysValid?: number;
}

class ActivateParentDto {
  @IsString()
  @MinLength(1)
  schoolSlug!: string;

  @IsString()
  @MinLength(6)
  code!: string;

  @IsString()
  @MinLength(8)
  password!: string;
}

@Controller('parent-activation')
export class ParentActivationController {
  constructor(private readonly activation: ParentActivationService) {}

  @Get('codes')
  @RequireFeature('users.parent_activation')
  @RequirePermissions('users:read', 'users:create')
  async list(@TenantId() tenantId: string) {
    return ok('Activation codes', await this.activation.list(tenantId));
  }

  @Get('stats')
  @RequireFeature('users.parent_activation')
  @RequirePermissions('users:read', 'reports:view')
  async stats(@TenantId() tenantId: string) {
    return ok('Activation stats', await this.activation.stats(tenantId));
  }

  @Post('codes')
  @RequireFeature('users.parent_activation')
  @RequirePermissions('users:create', 'users:manage')
  async issue(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: IssueActivationDto,
  ) {
    return ok(
      'Activation code issued',
      await this.activation.issue(tenantId, user.user_id || user.sub, body),
    );
  }

  @Public()
  @Get('schools')
  @Throttle({ default: { limit: 30, ttl: 60_000 } })
  async schools(@Query('query') query: string) {
    return ok('Schools', await this.activation.searchSchools(query));
  }

  @Public()
  @Get('preview')
  @Throttle({ default: { limit: 10, ttl: 60_000 } })
  async preview(
    @Query('schoolSlug') schoolSlug: string,
    @Query('code') code: string,
  ) {
    return ok('Activation preview', await this.activation.preview(schoolSlug, code));
  }

  @Public()
  @Post('activate')
  @Throttle({ default: { limit: 8, ttl: 60_000 } })
  async activate(@Body() body: ActivateParentDto) {
    return ok('Parent activated', await this.activation.activate(body));
  }
}
