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

export type PaymentActor = {
  userId: string;
  roles?: string[];
};

export type PaymentCheckoutInput = {
  context: string;
  amountMinor: number;
  email: string;
  callbackUrl: string;
  currency?: string;
  reference?: string;
  metadata?: Record<string, unknown>;
  gatewayId?: string;
  createdBy?: string;
};

export type PaymentCheckoutResult = {
  sessionId: string;
  reference: string;
  contextKey: string;
  gatewayId: string;
  checkoutUrl: string;
  accessCode?: string;
  status: string;
};

export type PaymentVerifyResult = {
  reference: string;
  status: string;
  gatewayId: string;
  amountMinor: number;
  currency: string;
  contextKey: string;
  sessionId: string;
  providerReference?: string;
  metadata?: Record<string, unknown>;
};

@Injectable()
export class PaymentServiceClient {
  private readonly logger = new Logger(PaymentServiceClient.name);

  constructor(private readonly config: ConfigService) {}

  isEnabled(): boolean {
    return Boolean(this.baseUrl());
  }

  private baseUrl(): string {
    return (this.config.get<string>('PAYMENT_SERVICE_URL') || '').replace(/\/$/, '');
  }

  private jwtSecret(): string {
    return (
      this.config.get<string>('PAYMENT_SERVICE_JWT_SECRET') ||
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

  issueToken(tenantId: string, actor: PaymentActor, features: string[]): string {
    const now = Math.floor(Date.now() / 1000);
    return this.sign({
      iss: this.config.get<string>('PAYMENT_SERVICE_JWT_ISSUER') || 'mas-school-server',
      aud: this.config.get<string>('PAYMENT_SERVICE_JWT_AUDIENCE') || 'mas-payment-service',
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

  static kindToContext(kind?: string): string {
    switch (kind) {
      case 'installment':
        return 'school_installment';
      case 'advance':
        return 'school_advance';
      case 'saas_subscription':
        return 'saas_subscription';
      case 'invoice':
      default:
        return 'school_invoice';
    }
  }

  private headers(tenantId: string, actor: PaymentActor, features: string[]) {
    return {
      Authorization: `Bearer ${this.issueToken(tenantId, actor, features)}`,
      'Content-Type': 'application/json',
      'X-Tenant-Id': tenantId,
      'X-Request-Id': randomUUID(),
    };
  }

  async createCheckout(
    tenantId: string,
    actor: PaymentActor,
    body: PaymentCheckoutInput,
    features: string[],
  ): Promise<PaymentCheckoutResult> {
    if (!this.isEnabled()) throw new Error('PAYMENT_SERVICE_URL not configured');
    const res = await fetch(`${this.baseUrl()}/v1/checkout`, {
      method: 'POST',
      headers: this.headers(tenantId, actor, features),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`Payment service checkout failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`Payment service checkout failed (${res.status})`);
    }
    return (await res.json()) as PaymentCheckoutResult;
  }

  async verify(
    tenantId: string,
    actor: PaymentActor,
    reference: string,
    features: string[],
  ): Promise<PaymentVerifyResult> {
    if (!this.isEnabled()) throw new Error('PAYMENT_SERVICE_URL not configured');
    const res = await fetch(`${this.baseUrl()}/v1/verify`, {
      method: 'POST',
      headers: this.headers(tenantId, actor, features),
      body: JSON.stringify({ reference }),
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`Payment service verify failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`Payment service verify failed (${res.status})`);
    }
    return (await res.json()) as PaymentVerifyResult;
  }

  async get<T>(path: string, tenantId: string, actor: PaymentActor, features: string[]): Promise<T> {
    if (!this.isEnabled()) throw new Error('PAYMENT_SERVICE_URL not configured');
    const res = await fetch(`${this.baseUrl()}${path}`, {
      headers: this.headers(tenantId, actor, features),
    });
    if (!res.ok) throw new Error(`Payment service failed (${res.status})`);
    return (await res.json()) as T;
  }

  async patch<T>(
    path: string,
    tenantId: string,
    actor: PaymentActor,
    body: unknown,
    features: string[],
  ): Promise<T> {
    if (!this.isEnabled()) throw new Error('PAYMENT_SERVICE_URL not configured');
    const res = await fetch(`${this.baseUrl()}${path}`, {
      method: 'PATCH',
      headers: this.headers(tenantId, actor, features),
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) throw new Error(`Payment service failed (${res.status})`);
    return (await res.json()) as T;
  }
}
