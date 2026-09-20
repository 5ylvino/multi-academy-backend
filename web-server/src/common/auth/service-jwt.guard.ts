import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Request } from 'express';
import { verifyServiceJwt } from './service-jwt.util';
import type { AuthUserClaims } from './auth-user.interface';

@Injectable()
export class ServiceJwtGuard implements CanActivate {
  constructor(private readonly config: ConfigService) {}

  canActivate(context: ExecutionContext): boolean {
    const req = context.switchToHttp().getRequest<Request>();
    const auth = req.headers['authorization'];
    if (!auth) throw new UnauthorizedException('Missing Authorization header');
    const [scheme, token] = auth.split(' ');
    if (scheme !== 'Bearer' || !token) {
      throw new UnauthorizedException('Invalid Authorization header');
    }
    const configuredSecret =
      this.config.get<string>('INTERNAL_SERVICE_JWT_SECRET') ||
      this.config.get<string>('SERVICE_JWT_SECRET');
    const isProduction = this.config.get<string>('NODE_ENV') === 'production';
    if (
      isProduction &&
      (!configuredSecret || ['change-me', 'change-me-in-production'].includes(configuredSecret))
    ) {
      throw new UnauthorizedException('Internal service JWT secret is not configured');
    }
    const secret = configuredSecret || 'change-me';
    const audience =
      this.config.get<string>('INTERNAL_SERVICE_JWT_AUDIENCE') || 'mas-school-internal';
    const issuer = this.config.get<string>('INTERNAL_SERVICE_JWT_ISSUER') || 'mas-school-server';
    let payload;
    try {
      payload = verifyServiceJwt(token, secret, audience, issuer);
    } catch (err) {
      throw new UnauthorizedException(
        err instanceof Error ? err.message : 'Invalid service token',
      );
    }
    const headerTenant = String(req.headers['x-tenant-id'] || '').trim();
    if (headerTenant && headerTenant !== payload.tenant_id) {
      throw new UnauthorizedException('Tenant header mismatch');
    }
    const claims: AuthUserClaims = {
      sub: payload.actor_id,
      user_id: payload.actor_id,
      tenant_id: payload.tenant_id,
      email: '',
      roles: payload.roles || [],
      permissions: [],
      capabilities: [],
    };
    (req as any).user = claims;
    (req as any).serviceFeatures = payload.features || [];
    return true;
  }
}
