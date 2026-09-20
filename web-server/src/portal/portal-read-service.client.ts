import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'crypto';
import { signServiceJwt, type ServiceJwtPayload } from '../common/auth/service-jwt.util';

export type PortalReadActor = {
  userId: string;
  roles?: string[];
};

@Injectable()
export class PortalReadServiceClient {
  private readonly logger = new Logger(PortalReadServiceClient.name);

  constructor(private readonly config: ConfigService) {}

  isEnabled(): boolean {
    return Boolean(this.baseUrl());
  }

  private baseUrl(): string {
    return (this.config.get<string>('PORTAL_READ_SERVICE_URL') || '').replace(/\/$/, '');
  }

  private jwtSecret(): string {
    return (
      this.config.get<string>('PORTAL_READ_SERVICE_JWT_SECRET') ||
      this.config.get<string>('SERVICE_JWT_SECRET') ||
      'change-me'
    );
  }

  private issueToken(tenantId: string, actor: PortalReadActor, features: string[]): string {
    const now = Math.floor(Date.now() / 1000);
    const payload: ServiceJwtPayload = {
      iss: this.config.get<string>('PORTAL_READ_SERVICE_JWT_ISSUER') || 'mas-school-server',
      aud: this.config.get<string>('PORTAL_READ_SERVICE_JWT_AUDIENCE') || 'mas-portal-read-service',
      sub: 'service',
      tenant_id: tenantId,
      actor_id: actor.userId,
      roles: (actor.roles || []).map((r) => String(r).toLowerCase()),
      features,
      iat: now,
      exp: now + 300,
      jti: randomUUID(),
    };
    return signServiceJwt(payload, this.jwtSecret());
  }

  async get<T>(
    path: string,
    tenantId: string,
    actor: PortalReadActor,
    features: string[],
  ): Promise<T> {
    if (!this.isEnabled()) throw new Error('PORTAL_READ_SERVICE_URL not configured');
    const url = `${this.baseUrl()}/v1/${path.replace(/^\//, '')}`;
    const res = await fetch(url, {
      headers: {
        Authorization: `Bearer ${this.issueToken(tenantId, actor, features)}`,
        'X-Tenant-Id': tenantId,
        'X-Request-Id': randomUUID(),
      },
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`Portal read failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`Portal read service failed (${res.status})`);
    }
    return (await res.json()) as T;
  }
}
