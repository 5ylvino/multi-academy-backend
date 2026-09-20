import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Agent, fetch as undiciFetch } from 'undici';
import { TenantRuntimeConfig } from './runtime-config.types';

/**
 * Outbound calls to control sit on the school request path, so they are always
 * bounded: an unresponsive control plane must fail fast and let the caller fall
 * back to cache, never hang the request.
 *
 * Defaults are deliberately tight — stale-while-revalidate on the services that
 * call us means a miss rarely blocks a user request for the full timeout.
 */
const DEFAULT_TIMEOUT_MS = 2_500;
const TOKEN_TIMEOUT_MS = 4_000;
const CONNECT_TIMEOUT_MS = 1_500;

@Injectable()
export class ControlApiClient {
  private readonly logger = new Logger(ControlApiClient.name);
  private m2mToken: string | null = null;
  private m2mTokenExpiresAt = 0;
  /** In-flight token request, so an expiring token triggers one POST, not N. */
  private tokenRequest: Promise<string> | null = null;

  /**
   * Shared keep-alive agent: control is a single origin and every authenticated
   * request may touch it, so TCP/TLS handshake reuse matters more than the
   * body transfer.
   */
  private readonly agent = new Agent({
    connections: 16,
    pipelining: 1,
    keepAliveTimeout: 30_000,
    keepAliveMaxTimeout: 60_000,
    connect: { timeout: CONNECT_TIMEOUT_MS },
  });

  constructor(private readonly config: ConfigService) {}

  private timeoutMs(): number {
    const raw = Number(this.config.get('CONTROL_API_TIMEOUT_MS'));
    return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_TIMEOUT_MS;
  }

  /** fetch with an enforced deadline, keep-alive pool, and clearer timeout errors. */
  private async timedFetch(
    url: string,
    init: RequestInit,
    timeoutMs: number,
  ): Promise<Response> {
    try {
      // Cast through any: undici's dispatcher option conflicts with DOM lib RequestInit.
      const res = await undiciFetch(url, {
        method: init.method,
        headers: init.headers,
        body: init.body,
        dispatcher: this.agent,
        signal: AbortSignal.timeout(timeoutMs),
      } as any);
      return res as unknown as Response;
    } catch (err) {
      const name = err instanceof Error ? err.name : '';
      const message = err instanceof Error ? err.message : String(err);
      if (
        name === 'TimeoutError' ||
        name === 'AbortError' ||
        /timeout/i.test(message)
      ) {
        throw new Error(`control request timed out after ${timeoutMs}ms: ${url}`);
      }
      throw err;
    }
  }

  isConfigured(): boolean {
    return Boolean(
      this.baseUrl() &&
        this.config.get<string>('CONTROL_M2M_CLIENT_ID') &&
        this.config.get<string>('CONTROL_M2M_CLIENT_SECRET'),
    );
  }

  private baseUrl(): string {
    return (this.config.get<string>('CONTROL_API_URL') || '').replace(/\/$/, '');
  }

  private async getAccessToken(): Promise<string> {
    const now = Date.now();
    if (this.m2mToken && now < this.m2mTokenExpiresAt - 30_000) {
      return this.m2mToken;
    }

    // Control rate-limits the token endpoint tightly to resist brute force, so
    // concurrent callers must share one request rather than each POSTing.
    if (this.tokenRequest) return this.tokenRequest;

    this.tokenRequest = this.requestToken().finally(() => {
      this.tokenRequest = null;
    });
    return this.tokenRequest;
  }

  private async requestToken(): Promise<string> {
    const res = await this.timedFetch(
      `${this.baseUrl()}/internal/v1/token`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: this.config.get<string>('CONTROL_M2M_CLIENT_ID'),
          client_secret: this.config.get<string>('CONTROL_M2M_CLIENT_SECRET'),
        }),
      },
      TOKEN_TIMEOUT_MS,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Control m2m token failed (${res.status}): ${text}`);
    }
    const data = (await res.json()) as { access_token: string; expires_in?: number };
    // Honour the issuer's lifetime, refreshing a little early.
    const lifetimeMs =
      typeof data.expires_in === 'number' && data.expires_in > 60
        ? data.expires_in * 1000
        : 15 * 60_000;
    this.m2mToken = data.access_token;
    this.m2mTokenExpiresAt = Date.now() + lifetimeMs * 0.8;
    return this.m2mToken;
  }

  private async authedFetch(path: string, init?: RequestInit): Promise<Response> {
    const token = await this.getAccessToken();
    const res = await this.timedFetch(
      `${this.baseUrl()}${path}`,
      {
        ...init,
        headers: {
          ...(init?.headers || {}),
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
      },
      this.timeoutMs(),
    );

    // A rotated/revoked client secret invalidates the cached token; retry once
    // with a fresh one so callers do not see a spurious failure.
    if (res.status === 401 && this.m2mToken) {
      this.m2mToken = null;
      this.m2mTokenExpiresAt = 0;
      const retryToken = await this.getAccessToken();
      return this.timedFetch(
        `${this.baseUrl()}${path}`,
        {
          ...init,
          headers: {
            ...(init?.headers || {}),
            Authorization: `Bearer ${retryToken}`,
            'Content-Type': 'application/json',
          },
        },
        this.timeoutMs(),
      );
    }
    return res;
  }

  async getRuntimeConfig(tenantRef: string): Promise<TenantRuntimeConfig> {
    const res = await this.authedFetch(
      `/internal/v1/tenants/${encodeURIComponent(tenantRef)}/runtime-config`,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`runtime-config ${res.status}: ${text}`);
    }
    return (await res.json()) as TenantRuntimeConfig;
  }

  async getCatalogPlans(): Promise<Array<{
    id: number;
    key: string;
    name: string;
    description: string;
    monthly_price_minor: number;
    currency: string;
    rank: number;
    is_active: boolean;
    entitlements: Array<{
      feature_key: string;
      name?: string | null;
      enabled: boolean;
      quota: number | null;
    }>;
  }>> {
    const res = await this.authedFetch('/internal/v1/plans');
    if (!res.ok) {
      throw new Error(`plans ${res.status}: ${await res.text()}`);
    }
    return (await res.json()) as any;
  }

  async getTenantSubscription(tenantRef: string): Promise<any> {
    const res = await this.authedFetch(
      `/internal/v1/tenants/${encodeURIComponent(tenantRef)}/subscription`,
    );
    if (!res.ok) {
      const text = await res.text();
      try {
        const detail = (JSON.parse(text) as { detail?: string; message?: string }).detail ||
          (JSON.parse(text) as { message?: string }).message;
        throw new Error(
          detail === 'No current subscription'
            ? 'No current subscription found'
            : `Subscription request failed: ${detail || text}`,
        );
      } catch (error) {
        if (error instanceof Error && !error.message.includes('Unexpected token')) throw error;
        throw new Error(`Subscription request failed: ${text}`);
      }
    }
    return (await res.json()) as any;
  }

  async changeTenantSubscription(
    tenantRef: string,
    input: { planKey: string; billingCycle: 'monthly' | 'yearly' },
  ): Promise<any> {
    const res = await this.authedFetch(
      `/internal/v1/tenants/${encodeURIComponent(tenantRef)}/subscription`,
      {
        method: 'POST',
        body: JSON.stringify({
          plan_key: input.planKey,
          billing_cycle: input.billingCycle,
        }),
      },
    );
    if (!res.ok) {
      throw new Error(`subscription change ${res.status}: ${await res.text()}`);
    }
    return (await res.json()) as any;
  }

  async getTenantInvoices(tenantRef: string): Promise<any[]> {
    const res = await this.authedFetch(
      `/internal/v1/tenants/${encodeURIComponent(tenantRef)}/invoices`,
    );
    if (!res.ok) {
      throw new Error(`invoices ${res.status}: ${await res.text()}`);
    }
    return (await res.json()) as any[];
  }

  async getTenantInvoicePdf(tenantRef: string, invoiceId: number): Promise<{
    body: Buffer;
    contentType: string;
    contentDisposition: string | null;
  }> {
    const res = await this.authedFetch(
      `/internal/v1/tenants/${encodeURIComponent(tenantRef)}/invoices/${invoiceId}/pdf`,
    );
    if (!res.ok) {
      throw new Error(`invoice PDF ${res.status}: ${await res.text()}`);
    }
    return {
      body: Buffer.from(await res.arrayBuffer()),
      contentType: res.headers.get('content-type') || 'application/pdf',
      contentDisposition: res.headers.get('content-disposition'),
    };
  }

  async settleTenantInvoice(
    tenantRef: string,
    invoiceId: number,
    input: { amountMinor: number; providerReference?: string; providerId?: string },
  ): Promise<any> {
    const res = await this.authedFetch(
      `/internal/v1/tenants/${encodeURIComponent(tenantRef)}/invoices/${invoiceId}/settle`,
      {
        method: 'POST',
        body: JSON.stringify(input),
      },
    );
    if (!res.ok) {
      throw new Error(`invoice settlement ${res.status}: ${await res.text()}`);
    }
    return (await res.json()) as any;
  }

  async getProviderSecrets(
    tenantRef: string,
    capability: string,
  ): Promise<{
    capability: string;
    providerId: string;
    mode: string;
    settings: Record<string, unknown>;
    secrets: Record<string, string>;
    secretVersions: Record<string, number>;
  }> {
    const path =
      tenantRef === '__platform__'
        ? `/internal/v1/provider-secrets/${encodeURIComponent(capability)}`
        : `/internal/v1/tenants/${encodeURIComponent(tenantRef)}/provider-secrets/${encodeURIComponent(capability)}`;
    const res = await this.authedFetch(path);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`provider-secrets ${res.status}: ${text}`);
    }
    return (await res.json()) as any;
  }

  async checkRegistration(input: {
    schoolName?: string;
    slug?: string;
    adminEmail?: string;
    phone?: string;
    ipAddress?: string;
    installToken?: string;
    deviceHash?: string;
  }): Promise<{ allowed: boolean; matches: string[]; message?: string | null }> {
    try {
      const res = await this.authedFetch('/internal/v1/registration-check', {
        method: 'POST',
        body: JSON.stringify({
          school_name: input.schoolName || '',
          slug: input.slug || '',
          admin_email: input.adminEmail || '',
          phone: input.phone || '',
          ip_address: input.ipAddress || '',
          install_token: input.installToken || '',
          device_hash: input.deviceHash || '',
        }),
      });
      if (!res.ok) {
        this.logger.warn(`registration-check failed: ${res.status}`);
        if (process.env.NODE_ENV !== 'production') {
          return { allowed: true, matches: [], message: 'Development control fallback' };
        }
        return {
          allowed: false,
          matches: [],
          message: 'Registration temporarily unavailable. Please try again later.',
        };
      }
      return (await res.json()) as any;
    } catch (err) {
      this.logger.warn(`registration-check error: ${err instanceof Error ? err.message : err}`);
      if (process.env.NODE_ENV !== 'production') {
        return { allowed: true, matches: [], message: 'Development control fallback' };
      }
      return {
        allowed: false,
        matches: [],
        message: 'Registration temporarily unavailable. Please try again later.',
      };
    }
  }

  async registerTenant(
    tenantId: string,
    body: {
      slug: string;
      name: string;
      adminEmail?: string;
      adminPhone?: string;
      region?: string;
      installToken?: string;
      deviceHash?: string;
    },
  ): Promise<void> {
    try {
      const res = await this.authedFetch(
        `/internal/v1/tenants/${encodeURIComponent(tenantId)}/register`,
        {
          method: 'POST',
          body: JSON.stringify(body),
        },
      );
      if (!res.ok) {
        this.logger.warn(`registerTenant failed: ${res.status} ${await res.text()}`);
      }
    } catch (err) {
      this.logger.warn(`registerTenant error: ${err instanceof Error ? err.message : err}`);
    }
  }

  async activateTenant(tenantId: string): Promise<void> {
    const res = await this.authedFetch(
      `/internal/v1/tenants/${encodeURIComponent(tenantId)}/activate`,
      { method: 'POST' },
    );
    if (!res.ok) {
      throw new Error(`activateTenant failed: ${res.status}`);
    }
  }

  async postUsage(input: {
    tenantId: string;
    meterKey: string;
    quantity: number;
    eventId?: string;
    properties?: Record<string, unknown>;
  }): Promise<void> {
    const res = await this.authedFetch('/internal/v1/usage', {
      method: 'POST',
      body: JSON.stringify({
        tenant_ref: input.tenantId,
        tenantId: input.tenantId,
        meter_key: input.meterKey,
        meterKey: input.meterKey,
        quantity: input.quantity,
        event_id: input.eventId || '',
        properties: input.properties || {},
      }),
    });
    if (!res.ok) {
      this.logger.warn(`usage ingest failed: ${res.status}`);
    }
  }
}
