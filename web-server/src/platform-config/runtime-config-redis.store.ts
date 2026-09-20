import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { TenantRuntimeConfig } from './runtime-config.types';

type RedisClient = {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, mode: string, ttlSeconds: number): Promise<unknown>;
  del(...keys: string[]): Promise<unknown>;
  ping(): Promise<string>;
  quit(): Promise<string>;
};

interface CacheEnvelope {
  config: TenantRuntimeConfig;
  fetchedAt: number;
}

/**
 * Optional shared Redis layer for tenant runtime config.
 * When REDIS_URL is unset, all methods no-op and in-memory cache remains the source.
 */
@Injectable()
export class RuntimeConfigRedisStore implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(RuntimeConfigRedisStore.name);
  private client: RedisClient | null = null;
  private readonly keyPrefix: string;

  constructor(private readonly config: ConfigService) {
    this.keyPrefix =
      (this.config.get<string>('RUNTIME_CONFIG_REDIS_PREFIX') || 'mas:runtime-config').replace(
        /:$/,
        '',
      ) + ':';
  }

  async onModuleInit(): Promise<void> {
    const url = (this.config.get<string>('REDIS_URL') || '').trim();
    if (!url) return;
    try {
      const mod = await import('ioredis');
      const Redis = mod.default as new (
        url: string,
        opts: { maxRetriesPerRequest: number; connectTimeout: number; lazyConnect: boolean },
      ) => RedisClient & { connect(): Promise<void> };
      const client = new Redis(url, {
        maxRetriesPerRequest: 1,
        connectTimeout: 2_000,
        lazyConnect: true,
      });
      await client.connect();
      this.client = client;
      this.logger.log('Runtime config Redis cache enabled');
    } catch (err) {
      this.logger.warn(
        `Runtime config Redis unavailable — using in-memory cache only: ${
          err instanceof Error ? err.message : err
        }`,
      );
      this.client = null;
    }
  }

  async onModuleDestroy(): Promise<void> {
    if (this.client) {
      try {
        await this.client.quit();
      } catch {
        /* ignore */
      }
    }
  }

  isEnabled(): boolean {
    return this.client !== null;
  }

  async ping(): Promise<boolean> {
    if (!this.client) return false;
    return (await this.client.ping()) === 'PONG';
  }

  private key(tenantId: string): string {
    return `${this.keyPrefix}${tenantId}`;
  }

  async read(tenantId: string): Promise<CacheEnvelope | null> {
    if (!this.client || !tenantId) return null;
    try {
      const raw = await this.client.get(this.key(tenantId));
      if (!raw) return null;
      const parsed = JSON.parse(raw) as CacheEnvelope;
      if (!parsed?.config?.tenantId) return null;
      return parsed;
    } catch (err) {
      this.logger.warn(
        `Redis runtime-config read failed for ${tenantId}: ${
          err instanceof Error ? err.message : err
        }`,
      );
      return null;
    }
  }

  async write(tenantId: string, envelope: CacheEnvelope, ttlSeconds: number): Promise<void> {
    if (!this.client || !tenantId) return;
    const ttl = Math.max(30, Math.min(ttlSeconds, 900));
    try {
      await this.client.set(this.key(tenantId), JSON.stringify(envelope), 'EX', ttl);
    } catch (err) {
      this.logger.warn(
        `Redis runtime-config write failed for ${tenantId}: ${
          err instanceof Error ? err.message : err
        }`,
      );
    }
  }

  async invalidate(tenantId?: string): Promise<void> {
    if (!this.client) return;
    try {
      if (tenantId) {
        await this.client.del(this.key(tenantId));
      }
    } catch (err) {
      this.logger.warn(
        `Redis runtime-config invalidate failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }
}
