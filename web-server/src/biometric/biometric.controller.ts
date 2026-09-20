import { Body, Controller, Delete, Get, Param, Post, Req } from '@nestjs/common';
import type { Request } from 'express';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { BiometricService } from './biometric.service';
import { BiometricAuthVerifyDto, BiometricRegisterVerifyDto } from './dto/biometric.dto';

function resolveActorId(user: AuthUserClaims): string {
  return String(user.user_id || user.sub || '').trim();
}

import { RequireFeature } from '../platform-config/require-feature.decorator';

@Controller('biometric')
@RequireFeature('auth.biometric_webauthn')
export class BiometricController {
  constructor(private readonly biometricService: BiometricService) {}

  @Get('status')
  @RequirePermissions(
    'biometric:verify',
    'biometric:configure',
    'payments:process',
    'staff_attendance:create',
  )
  async status(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    const data = await this.biometricService.getStatus(tenantId, resolveActorId(user));
    return ok('Biometric enrollment status', data);
  }

  @Post('register/options')
  @RequirePermissions(
    'biometric:configure',
    'biometric:verify',
    'payments:process',
    'staff_attendance:create',
  )
  async registerOptions(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Req() req: Request,
  ) {
    const userId = resolveActorId(user);
    const data = await this.biometricService.beginRegistration(
      tenantId,
      userId,
      user.email,
      user.email,
      req,
    );
    return ok('WebAuthn registration options', data);
  }

  @Post('register/verify')
  @RequirePermissions(
    'biometric:configure',
    'biometric:verify',
    'payments:process',
    'staff_attendance:create',
  )
  async registerVerify(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: BiometricRegisterVerifyDto,
    @Req() req: Request,
  ) {
    const data = await this.biometricService.finishRegistration(
      tenantId,
      resolveActorId(user),
      body.response as any,
      req,
    );
    return ok('Biometric credential enrolled', data);
  }

  @Post('authenticate/options')
  @RequirePermissions('biometric:verify', 'payments:process', 'staff_attendance:create')
  async authOptions(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Req() req: Request,
  ) {
    const data = await this.biometricService.beginAuthentication(
      tenantId,
      resolveActorId(user),
      req,
    );
    return ok('WebAuthn authentication options', data);
  }

  @Post('authenticate/verify')
  @RequirePermissions('biometric:verify', 'payments:process', 'staff_attendance:create')
  async authVerify(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: BiometricAuthVerifyDto,
    @Req() req: Request,
  ) {
    const data = await this.biometricService.finishAuthentication(
      tenantId,
      resolveActorId(user),
      body.response as any,
      body.purpose || 'general',
      req,
    );
    return ok('Biometric verified', data);
  }

  @Delete('credentials/:id')
  @RequirePermissions('biometric:configure', 'biometric:verify', 'staff_attendance:create')
  async remove(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    const data = await this.biometricService.removeCredential(
      tenantId,
      resolveActorId(user),
      id,
    );
    return ok('Biometric credential removed', data);
  }
}
