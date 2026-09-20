import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { IS_PUBLIC_KEY } from '../common/auth/public.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { FEATURE_KEYS } from './require-feature.decorator';
import { FeatureFlagService } from './feature-flag.service';

@Injectable()
export class FeatureFlagGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly featureFlags: FeatureFlagService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const required = this.reflector.getAllAndOverride<string[]>(FEATURE_KEYS, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (!required?.length) return true;

    const request = context.switchToHttp().getRequest();
    const user = request.user as AuthUserClaims | undefined;
    if (!user?.tenant_id) {
      throw new ForbiddenException('Authentication required');
    }

    for (const key of required) {
      if (await this.featureFlags.resolve(user.tenant_id, key, user)) {
        return true;
      }
    }
    throw new ForbiddenException(`Feature disabled: ${required.join(' | ')}`);
  }
}
