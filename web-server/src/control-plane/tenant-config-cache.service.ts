import { Injectable } from '@nestjs/common';
import { TenantConfig } from './control-plane.types';

type CacheEntry = {
  value: TenantConfig;
  expiresAt: number;
};

@Injectable()
export class TenantConfigCacheService {
  private readonly byTenantId = new Map<string, CacheEntry>();
  private readonly bySboId = new Map<string, CacheEntry>();
  private readonly ttlMs = 60 * 1000;

  getByTenantId(tenantId: string): TenantConfig | null {
    return this.getValid(this.byTenantId.get(tenantId));
  }

  getBySboId(sboId: string): TenantConfig | null {
    return this.getValid(this.bySboId.get(sboId));
  }

  set(config: TenantConfig): void {
    const entry: CacheEntry = {
      value: config,
      expiresAt: Date.now() + this.ttlMs,
    };
    this.byTenantId.set(config.id, entry);
    this.bySboId.set(config.schoolBusinessOrganisationId, entry);
  }

  invalidate(tenantId: string, sboId: string): void {
    this.byTenantId.delete(tenantId);
    this.bySboId.delete(sboId);
  }

  private getValid(entry?: CacheEntry): TenantConfig | null {
    if (!entry) return null;
    if (entry.expiresAt <= Date.now()) return null;
    return entry.value;
  }
}

