import { Body, Controller, Delete, Get, Param, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { SafeguardingService } from './safeguarding.service';
import {
  IsIn,
  IsISO8601,
  IsOptional,
  IsString,
  MaxLength,
  MinLength,
} from 'class-validator';

class CreateExeatDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsIn(['parent_initiated', 'school_initiated'])
  kind?: 'parent_initiated' | 'school_initiated';

  @IsString()
  @MinLength(3)
  @MaxLength(2000)
  reason!: string;

  @IsISO8601()
  pickupAt!: string;

  @IsString()
  @MinLength(2)
  pickupAdultName!: string;

  @IsOptional()
  @IsString()
  pickupAdultPhone?: string;

  @IsOptional()
  @IsString()
  pickupAdultId?: string;
}

class DecisionDto {
  @IsIn(['approve', 'decline'])
  decision!: 'approve' | 'decline';

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  note?: string;
}

class ConfirmOtpDto {
  @IsString()
  @MinLength(4)
  otp!: string;
}

class AdultDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsString()
  @MinLength(2)
  name!: string;

  @IsOptional()
  @IsString()
  phone?: string;

  @IsOptional()
  @IsString()
  photoUrl?: string;

  @IsOptional()
  @IsString()
  relationship?: string;

  @IsOptional()
  @IsString()
  idNumber?: string;
}

class DelegationDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsString()
  @MinLength(2)
  adultName!: string;

  @IsOptional()
  @IsString()
  phone?: string;

  @IsOptional()
  @IsString()
  photoUrl?: string;

  @IsOptional()
  @IsString()
  idCapture?: string;

  @IsString()
  @MinLength(8)
  validOn!: string;
}

@Controller('safeguarding')
@RequireFeature('ops.safeguarding')
export class SafeguardingController {
  constructor(private readonly safeguarding: SafeguardingService) {}

  @Get('exeats')
  @RequirePermissions(
    'students:read',
    'student_attendance:read',
    'results:approve',
    'biometric:verify',
  )
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('studentId') studentId?: string,
  ) {
    return ok(
      'Exeats',
      await this.safeguarding.listExeats(tenantId, user.user_id || user.sub, studentId),
    );
  }

  @Post('exeats')
  @RequirePermissions(
    'students:read',
    'student_attendance:read',
    'correspondence:manage',
    'payments:process',
  )
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateExeatDto,
  ) {
    return ok(
      'Exeat created',
      await this.safeguarding.createExeat(tenantId, user.user_id || user.sub, body),
    );
  }

  @Post('exeats/:id/review')
  @RequirePermissions('student_attendance:create', 'results:approve', 'students:read')
  async review(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: DecisionDto,
  ) {
    return ok(
      'Exeat reviewed',
      await this.safeguarding.review(
        tenantId,
        user.user_id || user.sub,
        id,
        body.decision,
        body.note,
      ),
    );
  }

  @Post('exeats/:id/decide')
  @RequirePermissions('results:approve', 'organization:update')
  async decide(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: DecisionDto,
  ) {
    return ok(
      'Exeat decided',
      await this.safeguarding.decide(
        tenantId,
        user.user_id || user.sub,
        id,
        body.decision,
        body.note,
      ),
    );
  }

  @Post('exeats/:id/confirm')
  @RequirePermissions('payments:process', 'correspondence:manage', 'student_attendance:read')
  async confirm(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: ConfirmOtpDto,
  ) {
    return ok(
      'Exeat confirmed',
      await this.safeguarding.confirmParentOtp(
        tenantId,
        user.user_id || user.sub,
        id,
        body.otp,
      ),
    );
  }

  @Get('adults')
  @RequirePermissions('students:read', 'student_attendance:read', 'correspondence:manage')
  async adults(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('studentId') studentId: string,
  ) {
    return ok(
      'Authorized adults',
      await this.safeguarding.listAdults(tenantId, studentId, user.user_id || user.sub),
    );
  }

  @Post('adults')
  @RequirePermissions('students:read', 'correspondence:manage', 'payments:process')
  async addAdult(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: AdultDto,
  ) {
    return ok(
      'Adult registered',
      await this.safeguarding.addAdult(tenantId, user.user_id || user.sub, body),
    );
  }

  @Delete('adults/:id')
  @RequirePermissions('students:read', 'correspondence:manage', 'payments:process')
  async removeAdult(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Authorized adult removed',
      await this.safeguarding.removeAdult(tenantId, user.user_id || user.sub, id),
    );
  }

  @Post('delegations')
  @RequirePermissions('students:read', 'correspondence:manage', 'payments:process')
  async delegate(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: DelegationDto,
  ) {
    return ok(
      'Delegation saved',
      await this.safeguarding.addDelegation(tenantId, user.user_id || user.sub, body),
    );
  }
}
