import { Injectable } from '@nestjs/common';
import { ControlPlaneService } from './control-plane.service';
import { derivePermissionsForRoles } from '../common/auth/role-permissions';

function normalizeRoleId(role: unknown): string {
  if (role == null) return '';
  if (typeof role === 'object') {
    const obj = role as { id?: unknown; name?: unknown; role?: unknown };
    return normalizeRoleId(obj.id ?? obj.role ?? obj.name);
  }
  return String(role).trim().toLowerCase().replace(/[\s-]+/g, '_');
}

function normalizeRoles(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return [...new Set(raw.map(normalizeRoleId).filter(Boolean))];
}

export type UserAuthority = {
  roles: string[];
  permissions: string[];
  capabilities: string[];
  isActive: boolean;
};

type CacheEntry = {
  value: UserAuthority;
  expiresAt: number;
};

/**
 * Resolves a user's live roles/permissions from the tenant database
 * so authorization decisions reflect current state instead of stale
 * JWT claims. A short TTL cache keeps request overhead low while
 * ensuring role changes propagate within seconds.
 */
@Injectable()
export class UserAuthorityService {
  private readonly cache = new Map<string, CacheEntry>();
  private readonly ttlMs = 30 * 1000;

  constructor(private readonly controlPlane: ControlPlaneService) {}

  async getAuthority(tenantId: string, userId: string): Promise<UserAuthority | null> {
    const key = `${tenantId}:${userId}`;
    const cached = this.cache.get(key);
    if (cached && cached.expiresAt > Date.now()) {
      return cached.value;
    }

    const user = await this.controlPlane.getUserById(tenantId, userId);
    if (!user) {
      this.cache.delete(key);
      return null;
    }

    const storedPermissions = user.permissions ?? [];
    const roles = normalizeRoles(user.roles ?? []);
    const value: UserAuthority = {
      roles,
      // Wildcard (primary owner) is honoured; otherwise roles are the
      // source of truth so revoked roles immediately lose their grants.
      permissions: storedPermissions.includes('*')
        ? ['*']
        : derivePermissionsForRoles(roles),
      capabilities: user.capabilities ?? [],
      isActive: user.isActive,
    };
    this.cache.set(key, { value, expiresAt: Date.now() + this.ttlMs });
    return value;
  }

  invalidate(tenantId: string, userId: string): void {
    this.cache.delete(`${tenantId}:${userId}`);
  }
}
