import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { IS_PUBLIC_KEY } from './public.decorator';
import {
  PERMISSIONS_KEY,
  ROLES_KEY,
} from './require-permissions.decorator';
import { AuthUserClaims } from './auth-user.interface';
import { UserAuthorityService } from '../../control-plane/user-authority.service';

@Injectable()
export class PermissionsGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly userAuthority: UserAuthorityService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const request = context.switchToHttp().getRequest();
    const user = request.user as AuthUserClaims | undefined;
    if (!user) {
      throw new ForbiddenException('Authentication required');
    }

    const requiredPermissions = this.reflector.getAllAndOverride<string[]>(
      PERMISSIONS_KEY,
      [context.getHandler(), context.getClass()],
    );
    const requiredRoles = this.reflector.getAllAndOverride<string[]>(
      ROLES_KEY,
      [context.getHandler(), context.getClass()],
    );

    // Authenticated-only routes trust verified JWT claims — avoids a tenant DB
    // round trip on every read (bootstrap, portal overview, etc.).
    if (!requiredPermissions?.length && !requiredRoles?.length) {
      return true;
    }

    // Zero-trust for protected routes: live roles/permissions from tenant DB.
    const authority = await this.userAuthority.getAuthority(
      user.tenant_id,
      user.sub,
    );
    if (!authority || !authority.isActive) {
      throw new ForbiddenException('Account is inactive or no longer exists');
    }

    request.user = {
      ...user,
      roles: authority.roles,
      permissions: authority.permissions,
      capabilities: authority.capabilities,
    };

    if (requiredRoles?.length) {
      const hasRole = requiredRoles.some((role) => authority.roles.includes(role));
      if (!hasRole) {
        throw new ForbiddenException('Insufficient role privileges');
      }
    }

    if (requiredPermissions?.length) {
      const granted = [...authority.permissions, ...authority.capabilities];
      if (this.hasWildcard(granted) || this.hasAnyPermission(granted, requiredPermissions)) {
        return true;
      }
      throw new ForbiddenException('Insufficient permissions');
    }

    return true;
  }

  private hasWildcard(granted: string[]): boolean {
    return granted.includes('*');
  }

  private hasAnyPermission(granted: string[], required: string[]): boolean {
    return required.some((permission) => {
      if (granted.includes(permission)) return true;
      const [resource] = permission.split(':');
      return granted.includes(`${resource}:manage`) || granted.includes(`${resource}:*`);
    });
  }
}
