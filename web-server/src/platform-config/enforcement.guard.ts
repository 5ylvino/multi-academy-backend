import {
  CanActivate,
  ExecutionContext,
  Injectable,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { Request } from 'express';
import { IS_PUBLIC_KEY } from '../common/auth/public.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { EnforcementService } from './enforcement.service';

/**
 * Server-enforced tenant/user/IP blocks from control runtime config.
 * Runs on authenticated routes after JwtAuthGuard.
 */
@Injectable()
export class EnforcementGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly enforcement: EnforcementService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const request = context.switchToHttp().getRequest<Request>();
    const user = (request as any).user as AuthUserClaims | undefined;
    if (!user?.tenant_id) return true;

    const forwarded = request.headers['x-forwarded-for'];
    const ip =
      (typeof forwarded === 'string' ? forwarded.split(',')[0].trim() : undefined) ||
      request.ip ||
      request.socket?.remoteAddress;

    await this.enforcement.assertTenantAllowed(
      user.tenant_id,
      {
        userId: user.sub || user.user_id,
        email: user.email,
        ip,
      },
      user,
    );
    return true;
  }
}
