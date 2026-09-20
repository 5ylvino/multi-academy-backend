import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHmac, randomUUID } from 'crypto';

type ServiceJwtPayload = {
  iss: string;
  aud: string;
  sub: string;
  tenant_id: string;
  actor_id: string;
  roles: string[];
  features: string[];
  iat: number;
  exp: number;
  jti: string;
};

export type TutoringActor = {
  userId: string;
  roles?: string[];
};

@Injectable()
export class TutoringServiceClient {
  private readonly logger = new Logger(TutoringServiceClient.name);

  constructor(private readonly config: ConfigService) {}

  isEnabled(): boolean {
    return Boolean(this.baseUrl());
  }

  private baseUrl(): string {
    return (this.config.get<string>('TUTORING_SERVICE_URL') || '').replace(/\/$/, '');
  }

  private jwtSecret(): string {
    return (
      this.config.get<string>('TUTORING_SERVICE_JWT_SECRET') ||
      this.config.get<string>('SERVICE_JWT_SECRET') ||
      'change-me'
    );
  }

  private sign(payload: ServiceJwtPayload): string {
    const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url');
    const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
    const input = `${header}.${body}`;
    const sig = createHmac('sha256', this.jwtSecret()).update(input).digest('base64url');
    return `${input}.${sig}`;
  }

  issueToken(tenantId: string, actor: TutoringActor, features: string[]): string {
    const now = Math.floor(Date.now() / 1000);
    return this.sign({
      iss: this.config.get<string>('TUTORING_SERVICE_JWT_ISSUER') || 'mas-school-server',
      aud: this.config.get<string>('TUTORING_SERVICE_JWT_AUDIENCE') || 'mas-tutoring-service',
      sub: 'service',
      tenant_id: tenantId,
      actor_id: actor.userId,
      roles: (actor.roles || []).map((r) => String(r).toLowerCase()),
      features,
      iat: now,
      exp: now + 300,
      jti: randomUUID(),
    });
  }

  async post<T>(path: string, tenantId: string, actor: TutoringActor, body: unknown, features: string[]): Promise<T> {
    if (!this.isEnabled()) throw new Error('TUTORING_SERVICE_URL not configured');
    const url = `${this.baseUrl()}${path}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${this.issueToken(tenantId, actor, features)}`,
        'Content-Type': 'application/json',
        'X-Tenant-Id': tenantId,
        'X-Request-Id': randomUUID(),
      },
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`Tutoring service failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`Tutoring service failed (${res.status})`);
    }
    return (await res.json()) as T;
  }

  async patch<T>(
    path: string,
    tenantId: string,
    actor: TutoringActor,
    body: unknown,
    features: string[],
  ): Promise<T> {
    if (!this.isEnabled()) throw new Error('TUTORING_SERVICE_URL not configured');
    const url = `${this.baseUrl()}${path}`;
    const res = await fetch(url, {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${this.issueToken(tenantId, actor, features)}`,
        'Content-Type': 'application/json',
        'X-Tenant-Id': tenantId,
        'X-Request-Id': randomUUID(),
      },
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`Tutoring service failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`Tutoring service failed (${res.status})`);
    }
    return (await res.json()) as T;
  }

  async get<T>(path: string, tenantId: string, actor: TutoringActor, features: string[]): Promise<T> {
    if (!this.isEnabled()) throw new Error('TUTORING_SERVICE_URL not configured');
    const url = `${this.baseUrl()}${path}`;
    const res = await fetch(url, {
      headers: {
        Authorization: `Bearer ${this.issueToken(tenantId, actor, features)}`,
        'X-Tenant-Id': tenantId,
        'X-Request-Id': randomUUID(),
      },
    });
    if (!res.ok) throw new Error(`Tutoring service failed (${res.status})`);
    return (await res.json()) as T;
  }
}
