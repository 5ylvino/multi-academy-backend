import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { ControlApiClient } from './control-api.client';
import {
  FAIL_CLOSED_FEATURES,
  TenantRuntimeConfig,
} from './runtime-config.types';
import { AuditLogService } from '../common/audit/audit-log.service';
import { RuntimeConfigRedisStore } from './runtime-config-redis.store';

interface CacheEntry {
  config: TenantRuntimeConfig;
  fetchedAt: number;
}

/** Cap on tracked tenants, so per-tenant bookkeeping cannot grow unbounded. */
const MAX_TRACKED_TENANTS = 500;

/**
 * Pulls TenantRuntimeConfig from the FastAPI control plane and caches briefly
 * (≤ 60s, or control's ttlSeconds). Fail-closed for gated external features
 * when control is unreachable past cache TTL.
 *
 * Every authenticated request resolves config, so the read path must not pay
 * for a control-plane round-trip. Within the TTL the cache is served directly;
 * past it the entry is served stale while a single background refresh runs
 * (`invalidate()` is called by the control webhook, so real changes still
 * propagate immediately). Past the stale ceiling the request blocks on a fetch
 * and fails closed if control is unreachable.
 */
@Injectable()
export class RuntimeConfigService {
  private readonly logger = new Logger(RuntimeConfigService.name);
  private readonly cache = new Map<string, CacheEntry>();
  /** Last applied configVersion per tenant — used for structured change audit. */
  private readonly lastVersion = new Map<string, number>();
  private readonly lastProviders = new Map<string, string>();
  /** In-flight refreshes, so an expiring entry triggers one fetch, not N. */
  private readonly inFlight = new Map<string, Promise<TenantRuntimeConfig>>();
  /**
   * Bumped on invalidate so a refresh that started before the webhook cannot
   * write a stale config back into the cache after a deliberate flush.
   */
  private readonly generation = new Map<string, number>();
  private globalGeneration = 0;

  constructor(
    private readonly client: ControlApiClient,
    private readonly config: ConfigService,
    private readonly audit: AuditLogService,
    private readonly redisStore: RuntimeConfigRedisStore,
  ) {}

  /** Synchronous read of the in-memory cache — no control-plane I/O. */
  peekCached(tenantId: string): TenantRuntimeConfig | null {
    if (!tenantId) return null;
    return this.cache.get(tenantId)?.config ?? null;
  }

  private defaultTtlMs(): number {
    const seconds = Number(this.config.get('CONTROL_CONFIG_TTL_SECONDS') || 60);
    return Math.max(5, Math.min(seconds, 60)) * 1000;
  }

  /**
   * How long past the TTL an entry may still be served while refreshing in the
   * background. Beyond this the caller must wait for a fresh pull.
   */
  private staleCeilingMs(): number {
    const seconds = Number(this.config.get('CONTROL_CONFIG_STALE_CEILING_SECONDS') || 300);
    return Math.max(30, Math.min(seconds, 900)) * 1000;
  }

  /** Local fallback when control API is not configured (dev without FastAPI). */
  private localFallback(tenantId: string): TenantRuntimeConfig {
    return {
      tenantId,
      subscription: { type: 'catalog', planId: 'basic', billingCycle: 'monthly' },
      status: 'active',
      // Empty map: FeatureFlagService fills foundation defaults for missing keys.
      features: {},
      quotas: {},
      providers: {},
      enforcement: {
        schoolBlacklisted: false,
        blockedUserIds: [],
        blockedEmails: [],
        blockedIpCidrs: [],
        capabilityFreezes: [],
        denyLogin: false,
      },
      killSwitches: [],
      updatedAt: new Date().toISOString(),
      configVersion: 0,
    };
  }

  private failClosedConfig(tenantId: string, stale?: TenantRuntimeConfig): TenantRuntimeConfig {
    const base = stale || this.localFallback(tenantId);
    const features = { ...base.features };
    for (const key of FAIL_CLOSED_FEATURES) {
      features[key] = false;
    }
    return {
      ...base,
      features,
      providers: {},
      updatedAt: new Date().toISOString(),
    };
  }

  private providerFingerprint(config: TenantRuntimeConfig): string {
    const p = config.providers || {};
    return [
      p.payment?.providerId,
      p.sms?.providerId,
      p.email?.providerId,
      p.ai?.providerId,
      p.meeting?.providerId,
    ].join('|');
  }

  private async auditConfigApplied(
    tenantId: string,
    config: TenantRuntimeConfig,
    previousVersion: number | undefined,
  ) {
    const providers = this.providerFingerprint(config);
    const prevProviders = this.lastProviders.get(tenantId);
    await this.audit.log({
      tenantId,
      method: 'SYSTEM',
      path: '/internal/runtime-config',
      statusCode: 200,
      payload: {
        event: 'tenant_config.applied',
        configVersion: config.configVersion,
        previousVersion: previousVersion ?? null,
        providers: config.providers,
        status: config.status,
        killSwitches: config.killSwitches,
        providerFingerprintChanged:
          prevProviders !== undefined && prevProviders !== providers,
      },
    });
    this.lastProviders.set(tenantId, providers);
  }

  async getConfig(tenantId: string, opts?: { force?: boolean }): Promise<TenantRuntimeConfig> {
    if (!tenantId) {
      return this.localFallback('unknown');
    }

    let cached = this.cache.get(tenantId);
    if (!cached && !opts?.force && this.redisStore.isEnabled()) {
      const fromRedis = await this.redisStore.read(tenantId);
      if (fromRedis) {
        this.cache.set(tenantId, fromRedis);
        cached = fromRedis;
      }
    }

    const ttl = cached?.config.ttlSeconds
      ? Math.min(cached.config.ttlSeconds, 60) * 1000
      : this.defaultTtlMs();
    const age = cached ? Date.now() - cached.fetchedAt : Number.POSITIVE_INFINITY;

    if (!opts?.force && cached && age < ttl) {
      return cached.config;
    }

    if (!this.client.isConfigured()) {
      return this.localFallback(tenantId);
    }

    // Serve stale and refresh behind the request, so an expiring TTL never adds
    // control-plane latency to a user request.
    if (!opts?.force && cached && age < this.staleCeilingMs()) {
      void this.refresh(tenantId).catch(() => undefined);
      return cached.config;
    }

    try {
      return await this.refresh(tenantId);
    } catch (err) {
      this.logger.warn(
        `Control runtime-config pull failed for ${tenantId}: ${
          err instanceof Error ? err.message : err
        }`,
      );
      if (cached) {
        return this.failClosedConfig(tenantId, cached.config);
      }
      return this.failClosedConfig(tenantId);
    }
  }

  /** Pull from control, collapsing concurrent callers onto one request. */
  private refresh(tenantId: string): Promise<TenantRuntimeConfig> {
    const existing = this.inFlight.get(tenantId);
    if (existing) return existing;

    const gen = this.generation.get(tenantId) || 0;
    const globalGen = this.globalGeneration;

    const task = (async () => {
      const config = await this.pullOrRegister(tenantId);
      // Discard if invalidate() ran while we were in flight.
      if (
        (this.generation.get(tenantId) || 0) !== gen ||
        this.globalGeneration !== globalGen
      ) {
        return config;
      }
      this.evictIfFull();
      const entry = { config, fetchedAt: Date.now() };
      this.cache.set(tenantId, entry);
      const redisTtlSeconds = Math.ceil(this.staleCeilingMs() / 1000);
      void this.redisStore.write(tenantId, entry, redisTtlSeconds).catch(() => undefined);

      const prev = this.lastVersion.get(tenantId);
      if (prev === undefined || prev !== config.configVersion) {
        this.lastVersion.set(tenantId, config.configVersion);
        // Fire-and-forget audit; never block config resolution.
        void this.auditConfigApplied(tenantId, config, prev).catch((err) => {
          this.logger.warn(
            `Config-change audit failed for ${tenantId}: ${
              err instanceof Error ? err.message : err
            }`,
          );
        });
      }
      return config;
    })().finally(() => {
      // Only clear our own in-flight slot — a later refresh may already be booked.
      if (this.inFlight.get(tenantId) === task) {
        this.inFlight.delete(tenantId);
      }
    });

    this.inFlight.set(tenantId, task);
    return task;
  }

  /** Drop the oldest entry so a long-lived process cannot grow without bound. */
  private evictIfFull() {
    if (this.cache.size < MAX_TRACKED_TENANTS) return;
    let oldestId: string | undefined;
    let oldestAt = Number.POSITIVE_INFINITY;
    for (const [id, entry] of this.cache) {
      if (entry.fetchedAt < oldestAt) {
        oldestAt = entry.fetchedAt;
        oldestId = id;
      }
    }
    if (oldestId) {
      this.cache.delete(oldestId);
      this.lastVersion.delete(oldestId);
      this.lastProviders.delete(oldestId);
    }
  }

  /**
   * Fetch runtime-config; if the tenant was never registered in control
   * (common for schools created before control was wired), auto-register
   * then retry once.
   */
  private async pullOrRegister(tenantId: string): Promise<TenantRuntimeConfig> {
    try {
      const config = await this.client.getRuntimeConfig(tenantId);
      if (config.status === 'suspended' && process.env.NODE_ENV !== 'production') {
        this.logger.warn(`Development tenant ${tenantId} is suspended — attempting activation`);
        try {
          await this.client.activateTenant(tenantId);
          return await this.client.getRuntimeConfig(tenantId);
        } catch (activationError) {
          this.logger.warn(
            `Development tenant ${tenantId} activation failed: ${
              activationError instanceof Error ? activationError.message : activationError
            }`,
          );
        }
      }
      return config;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const missing =
        msg.includes('404') ||
        /not registered/i.test(msg) ||
        /Tenant not found/i.test(msg);
      if (!missing) throw err;

      this.logger.warn(
        `Tenant ${tenantId} missing in control — auto-registering with basic plan`,
      );
      await this.client.registerTenant(tenantId, {
        slug: tenantId,
        name: tenantId,
        region: 'NG',
      });
      try {
        await this.client.activateTenant(tenantId);
      } catch (activationError) {
        this.logger.warn(
          `Tenant ${tenantId} could not be activated after registration: ${
            activationError instanceof Error ? activationError.message : activationError
          }`,
        );
      }
      return await this.client.getRuntimeConfig(tenantId);
    }
  }

  /** O(1) Map delete / clear — called from the control webhook. */
  invalidate(tenantId?: string) {
    if (tenantId) {
      this.cache.delete(tenantId);
      this.lastProviders.delete(tenantId);
      this.inFlight.delete(tenantId);
      this.generation.set(tenantId, (this.generation.get(tenantId) || 0) + 1);
      void this.redisStore.invalidate(tenantId).catch(() => undefined);
    } else {
      this.cache.clear();
      this.lastProviders.clear();
      this.inFlight.clear();
      this.globalGeneration += 1;
    }
  }
}
