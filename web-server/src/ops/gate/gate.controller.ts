import { Body, Controller, Get, Post } from '@nestjs/common';
import { ok } from '../../common/types/api-response';
import { TenantId } from '../../common/auth/tenant-id.decorator';
import { CurrentUser } from '../../common/auth/current-user.decorator';
import { AuthUserClaims } from '../../common/auth/auth-user.interface';
import { RequireFeature } from '../../platform-config/require-feature.decorator';
import { RequirePermissions } from '../../common/auth/require-permissions.decorator';
import { GateService } from './gate.service';
import { IsNumber, IsOptional, IsString, Max, Min, MinLength } from 'class-validator';

class IssueTokenDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  guardianName?: string;

  @IsOptional()
  @IsNumber()
  @Min(1)
  @Max(72)
  validHours?: number;
}

class RedeemTokenDto {
  @IsString()
  @MinLength(1)
  tokenCode!: string;
}

@Controller('ops/gate')
@RequireFeature('ops.gate_security')
export class GateController {
  constructor(private readonly gate: GateService) {}

  @Get('tokens')
  @RequirePermissions('students:read', 'organization:update', 'biometric:verify')
  async list(@TenantId() tenantId: string) {
    return ok('Pickup tokens', await this.gate.list(tenantId));
  }

  @Post('tokens')
  @RequirePermissions('students:read', 'organization:update', 'biometric:verify')
  async issue(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: IssueTokenDto,
  ) {
    return ok(
      'Token issued',
      await this.gate.issueToken(tenantId, user.user_id || user.sub, body),
    );
  }

  @Post('tokens/redeem')
  @RequirePermissions('biometric:verify', 'organization:update')
  async redeem(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: RedeemTokenDto,
  ) {
    return ok(
      'Token status',
      await this.gate.redeem(tenantId, user.user_id || user.sub, body.tokenCode),
    );
  }
}
