import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'crypto';
import { signServiceJwt, type ServiceJwtPayload } from '../common/auth/service-jwt.util';

@Injectable()
export class WorkerServiceClient {
  private readonly logger = new Logger(WorkerServiceClient.name);

  constructor(private readonly config: ConfigService) {}

  isEnabled(): boolean {
    return Boolean(this.baseUrl());
  }

  private baseUrl(): string {
    return (this.config.get<string>('WORKER_SERVICE_URL') || '').replace(/\/$/, '');
  }

  private jwtSecret(): string {
    return (
      this.config.get<string>('WORKER_SERVICE_JWT_SECRET') ||
      this.config.get<string>('SERVICE_JWT_SECRET') ||
      'change-me'
    );
  }

  private issueToken(tenantId: string, actorId: string): string {
    const now = Math.floor(Date.now() / 1000);
    const payload: ServiceJwtPayload = {
      iss: this.config.get<string>('WORKER_SERVICE_JWT_ISSUER') || 'mas-school-server',
      aud: this.config.get<string>('WORKER_SERVICE_JWT_AUDIENCE') || 'mas-worker-service',
      sub: 'service',
      tenant_id: tenantId,
      actor_id: actorId,
      roles: ['service'],
      features: [],
      iat: now,
      exp: now + 300,
      jti: randomUUID(),
    };
    return signServiceJwt(payload, this.jwtSecret());
  }

  async enqueueNotification(params: {
    tenantId: string;
    userId: string;
    title: string;
    message: string;
    type?: string;
    href?: string;
  }): Promise<void> {
    if (!this.isEnabled()) return;
    const url = `${this.baseUrl()}/v1/jobs/notifications`;
    void fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${this.issueToken(params.tenantId, params.userId)}`,
        'Content-Type': 'application/json',
        'X-Tenant-Id': params.tenantId,
        'X-Request-Id': randomUUID(),
      },
      body: JSON.stringify(params),
    }).catch((err) => {
      this.logger.warn(`Worker notification enqueue failed: ${String(err)}`);
    });
  }

  async enqueueReportDownload(params: {
    tenantId: string;
    actorId: string;
    reportId: string;
    format: string;
  }): Promise<{ jobId: string } | null> {
    if (!this.isEnabled()) return null;
    const url = `${this.baseUrl()}/v1/jobs/reports/download`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${this.issueToken(params.tenantId, params.actorId)}`,
        'Content-Type': 'application/json',
        'X-Tenant-Id': params.tenantId,
        'X-Request-Id': randomUUID(),
      },
      body: JSON.stringify(params),
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`Worker report enqueue failed (${res.status}): ${text.slice(0, 200)}`);
      return null;
    }
    return (await res.json()) as { jobId: string };
  }

  async pollJob<T>(tenantId: string, actorId: string, jobId: string, timeoutMs = 90_000): Promise<T | null> {
    if (!this.isEnabled()) return null;
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const url = `${this.baseUrl()}/v1/jobs/${encodeURIComponent(jobId)}`;
      const res = await fetch(url, {
        headers: {
          Authorization: `Bearer ${this.issueToken(tenantId, actorId)}`,
          'X-Tenant-Id': tenantId,
          'X-Request-Id': randomUUID(),
        },
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { status: string; result?: T };
      if (body.status === 'completed' && body.result !== undefined) return body.result;
      if (body.status === 'failed') return null;
      await new Promise((r) => setTimeout(r, 500));
    }
    return null;
  }
}
