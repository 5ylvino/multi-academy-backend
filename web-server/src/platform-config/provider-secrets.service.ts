import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { ControlApiClient } from './control-api.client';

type SecretsPayload = {
  capability?: string;
  providerId: string;
  mode: string;
  settings: Record<string, unknown>;
  secrets: Record<string, string>;
  secretVersions: Record<string, number>;
};

interface TenantSecretsBucket {
  /** capability → cached payload */
  entries: Map<string, { fetchedAt: number; payload: SecretsPayload }>;
  /** In-flight fetches so concurrent callers share one control round-trip. */
  inFlight: Map<string, Promise<SecretsPayload | null>>;
  /**
   * Bumped on invalidate so a refresh that started before the webhook cannot
   * write a stale payload back into the cache.
   */
  generation: number;
}

/** Cap on tracked tenants, mirroring RuntimeConfigService. */
const MAX_TRACKED_TENANTS = 500;

/**
 * Fetches sealed provider secrets from the control vault (separate from
 * cached runtime-config). Hot payment/SMS paths call this, so the read path
 * must not pay for a control round-trip on every request: in-memory TTL cache
 * with stale-while-revalidate and single-flight coalescing.
 */
@Injectable()
export class ProviderSecretsService {
  private readonly logger = new Logger(ProviderSecretsService.name);
  private readonly cache = new Map<string, TenantSecretsBucket>();

  constructor(
    private readonly client: ControlApiClient,
    private readonly config: ConfigService,
  ) {}

  private ttlMs(): number {
    const seconds = Number(this.config.get('CONTROL_SECRETS_TTL_SECONDS') || 60);
    return Math.max(5, Math.min(seconds, 60)) * 1000;
  }

  private staleCeilingMs(): number {
    const seconds = Number(
      this.config.get('CONTROL_SECRETS_STALE_CEILING_SECONDS') || 300,
    );
    return Math.max(30, Math.min(seconds, 900)) * 1000;
  }

  private bucket(tenantId: string): TenantSecretsBucket {
    let b = this.cache.get(tenantId);
    if (!b) {
      this.evictIfFull();
      b = { entries: new Map(), inFlight: new Map(), generation: 0 };
      this.cache.set(tenantId, b);
    }
    return b;
  }

  private evictIfFull() {
    if (this.cache.size < MAX_TRACKED_TENANTS) return;
    // Drop an arbitrary (insertion-order) oldest tenant bucket.
    const oldest = this.cache.keys().next().value;
    if (oldest) this.cache.delete(oldest);
  }

  async getSecrets(
    tenantId: string,
    capability: string,
  ): Promise<SecretsPayload | null> {
    if (!this.client.isConfigured()) {
      return null;
    }

    const bucket = this.bucket(tenantId);
    const hit = bucket.entries.get(capability);
    const age = hit ? Date.now() - hit.fetchedAt : Number.POSITIVE_INFINITY;
    const ttl = this.ttlMs();

    if (hit && age < ttl) {
      return hit.payload;
    }

    // Serve stale and refresh behind the request — never add control latency
    // to a payment/SMS call just because the TTL expired.
    if (hit && age < this.staleCeilingMs()) {
      void this.refresh(tenantId, capability).catch(() => undefined);
      return hit.payload;
    }

    return this.refresh(tenantId, capability);
  }

  private refresh(
    tenantId: string,
    capability: string,
  ): Promise<SecretsPayload | null> {
    const bucket = this.bucket(tenantId);
    const existing = bucket.inFlight.get(capability);
    if (existing) return existing;

    const generation = bucket.generation;
    const task = (async () => {
      try {
        const payload = await this.client.getProviderSecrets(tenantId, capability);
        // Discard if invalidate() ran while we were in flight.
        const current = this.cache.get(tenantId);
        if (!current || current.generation !== generation) {
          return payload;
        }
        current.entries.set(capability, {
          fetchedAt: Date.now(),
          payload,
        });
        return payload;
      } catch (err) {
        this.logger.warn(
          `provider-secrets fetch failed: ${err instanceof Error ? err.message : err}`,
        );
        const stale = this.cache.get(tenantId)?.entries.get(capability);
        return stale?.payload ?? null;
      }
    })().finally(() => {
      const current = this.cache.get(tenantId);
      if (current && current.generation === generation) {
        current.inFlight.delete(capability);
      }
    });

    bucket.inFlight.set(capability, task);
    return task;
  }

  /**
   * O(1) for a whole-tenant flush (nested Map delete) and O(1) for a single
   * capability. Called from the control webhook on every config change.
   */
  invalidate(tenantId?: string, capability?: string) {
    if (!tenantId) {
      this.cache.clear();
      return;
    }
    if (capability) {
      const bucket = this.cache.get(tenantId);
      if (!bucket) return;
      bucket.entries.delete(capability);
      bucket.inFlight.delete(capability);
      bucket.generation += 1;
      return;
    }
    // Whole-tenant flush: bump generation first so any in-flight write is
    // discarded, then drop the bucket.
    const bucket = this.cache.get(tenantId);
    if (bucket) bucket.generation += 1;
    this.cache.delete(tenantId);
  }
}
