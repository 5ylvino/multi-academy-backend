import { Injectable } from '@nestjs/common';

type CacheEntry = {
  value: unknown;
  touchedAt: number;
  expiresAt: number;
};

const MAX_ENTRIES = 5000;
const DEFAULT_TTL_MS = Number(process.env.LIST_CACHE_TTL_MS) || 45_000;

export function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

function clone<T>(value: T): T {
  return value === undefined ? value : structuredClone(value);
}

@Injectable()
export class ListCacheService {
  private readonly entries = new Map<string, CacheEntry>();
  private readonly inFlight = new Map<string, Promise<unknown>>();
  private readonly generations = new Map<string, number>();
  private readonly defaultTtlMs = Math.max(1_000, DEFAULT_TTL_MS);

  async getOrLoad<T>(
    key: string,
    loader: () => Promise<T>,
    force = false,
    ttlMs?: number,
  ): Promise<T> {
    const now = Date.now();
    if (!force) {
      const cached = this.entries.get(key);
      if (cached && cached.expiresAt > now) {
        cached.touchedAt = now;
        return clone(cached.value as T);
      }
      if (cached) this.entries.delete(key);
    }
    const pending = this.inFlight.get(key);
    if (pending) return pending.then((value) => clone(value as T));
    const generation = this.generations.get(key) || 0;
    const ttl = typeof ttlMs === 'number' && ttlMs > 0 ? ttlMs : this.defaultTtlMs;
    const request = loader()
      .then((value) => {
        if ((this.generations.get(key) || 0) === generation) this.set(key, value, ttl);
        return value;
      })
      .finally(() => {
        if (this.inFlight.get(key) === request) this.inFlight.delete(key);
      });
    this.inFlight.set(key, request);
    return request.then((value) => clone(value));
  }

  set<T>(key: string, value: T, ttlMs = this.defaultTtlMs) {
    if (this.entries.size >= MAX_ENTRIES && !this.entries.has(key)) {
      let oldestKey: string | undefined;
      let oldestAt = Number.POSITIVE_INFINITY;
      for (const [entryKey, entry] of this.entries) {
        if (entry.touchedAt < oldestAt) {
          oldestKey = entryKey;
          oldestAt = entry.touchedAt;
        }
      }
      if (oldestKey) this.entries.delete(oldestKey);
    }
    const now = Date.now();
    this.entries.set(key, {
      value: clone(value),
      touchedAt: now,
      expiresAt: now + Math.max(1_000, ttlMs),
    });
  }

  invalidate(prefix: string) {
    for (const key of this.entries.keys()) {
      if (key.startsWith(prefix)) this.entries.delete(key);
    }
    for (const key of this.inFlight.keys()) {
      if (key.startsWith(prefix)) {
        this.inFlight.delete(key);
        this.generations.set(key, (this.generations.get(key) || 0) + 1);
      }
    }
  }

  invalidateTenant(tenantId: string) {
    this.invalidate(`tenant:${tenantId}:`);
  }

  clear() {
    this.entries.clear();
    this.inFlight.clear();
    this.generations.clear();
  }
}
