import { Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import { DataSource } from 'typeorm';
import { buildTenantDataSourceOptions, TENANT_POOL_MAX } from './typeorm.config';

interface CacheEntry {
  ds: DataSource;
  dbUrl: string;
  lastUsedAt: number;
}

function intFromEnv(name: string, fallback: number): number {
  const parsed = Number(process.env[name]);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

/**
 * Caches one TypeORM DataSource per tenant database.
 *
 * Each cached DataSource owns a connection pool, so the cache size is bounded
 * and least-recently-used tenants are evicted: `MAX_TENANTS × TENANT_POOL_MAX`
 * must stay under the database's `max_connections`.
 */
@Injectable()
export class TenantConnectionService implements OnModuleDestroy {
  private readonly logger = new Logger(TenantConnectionService.name);
  private readonly cache = new Map<string, CacheEntry>();
  /** In-flight initializations, so concurrent callers share one DataSource. */
  private readonly pending = new Map<string, Promise<DataSource>>();
  private readonly maxTenants = intFromEnv('TENANT_DB_MAX_CACHED', 20);
  private readonly idleEvictMs = intFromEnv('TENANT_DB_EVICT_IDLE_MS', 10 * 60_000);
  private sweepTimer?: NodeJS.Timeout;

  constructor() {
    this.sweepTimer = setInterval(() => {
      void this.evictIdle();
    }, 60_000);
    // Never hold the event loop open just for cache maintenance.
    this.sweepTimer.unref?.();
  }

  async getOrCreate(tenantId: string, dbUrl: string): Promise<DataSource> {
    const existing = this.cache.get(tenantId);
    if (existing) {
      // A tenant that was moved to another database must not keep using the
      // pool pointed at its old host.
      if (existing.dbUrl !== dbUrl) {
        this.logger.log(`Tenant ${tenantId} database URL changed — reconnecting`);
        await this.release(tenantId);
      } else {
        existing.lastUsedAt = Date.now();
        if (!existing.ds.isInitialized) {
          await existing.ds.initialize();
        }
        return existing.ds;
      }
    }

    // Without single-flight, concurrent first requests for the same tenant each
    // build a DataSource and all but one leak their pool.
    const inFlight = this.pending.get(tenantId);
    if (inFlight) return inFlight;

    const task = this.create(tenantId, dbUrl).finally(() => {
      this.pending.delete(tenantId);
    });
    this.pending.set(tenantId, task);
    return task;
  }

  private async create(tenantId: string, dbUrl: string): Promise<DataSource> {
    await this.evictToCapacity();
    const ds = new DataSource(buildTenantDataSourceOptions(dbUrl));
    try {
      await ds.initialize();
    } catch (err) {
      // Do not cache a broken DataSource; the next request should retry.
      await ds.destroy().catch(() => undefined);
      throw err;
    }
    this.cache.set(tenantId, { ds, dbUrl, lastUsedAt: Date.now() });
    return ds;
  }

  private async evictToCapacity(): Promise<void> {
    while (this.cache.size >= this.maxTenants) {
      let oldestId: string | undefined;
      let oldestAt = Number.POSITIVE_INFINITY;
      for (const [id, entry] of this.cache) {
        if (entry.lastUsedAt < oldestAt) {
          oldestAt = entry.lastUsedAt;
          oldestId = id;
        }
      }
      if (!oldestId) return;
      this.logger.log(`Evicting idle tenant pool ${oldestId} (cache at capacity)`);
      await this.release(oldestId);
    }
  }

  private async evictIdle(): Promise<void> {
    const cutoff = Date.now() - this.idleEvictMs;
    const stale = [...this.cache.entries()]
      .filter(([, entry]) => entry.lastUsedAt < cutoff)
      .map(([id]) => id);
    for (const id of stale) {
      await this.release(id).catch((err) =>
        this.logger.warn(`Failed to release idle tenant pool ${id}: ${String(err)}`),
      );
    }
  }

  /** Active pool count and configured ceiling, for health/metrics endpoints. */
  stats(): { cachedTenants: number; maxTenants: number; maxConnections: number } {
    return {
      cachedTenants: this.cache.size,
      maxTenants: this.maxTenants,
      maxConnections: this.maxTenants * TENANT_POOL_MAX,
    };
  }

  async release(tenantId: string): Promise<void> {
    const entry = this.cache.get(tenantId);
    if (!entry) return;
    this.cache.delete(tenantId);
    if (entry.ds.isInitialized) {
      await entry.ds.destroy();
    }
  }

  async onModuleDestroy() {
    if (this.sweepTimer) clearInterval(this.sweepTimer);
    const releases = Array.from(this.cache.keys()).map((tenantId) => this.release(tenantId));
    await Promise.all(releases);
  }
}
