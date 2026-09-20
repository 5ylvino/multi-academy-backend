import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { ProviderRegistryService } from '../platform-config/providers/provider-registry.service';

@Injectable()
export class PushService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly providers: ProviderRegistryService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS push_device_tokens (
        id varchar(64) PRIMARY KEY,
        user_id varchar(64) NOT NULL,
        token text NOT NULL,
        platform varchar(32) NOT NULL DEFAULT 'web',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_used_at TIMESTAMP NULL,
        UNIQUE (user_id, token)
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS push_preferences (
        user_id varchar(64) PRIMARY KEY,
        enabled boolean NOT NULL DEFAULT true,
        categories text NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async registerToken(
    tenantId: string,
    userId: string,
    body: { token: string; platform?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.push');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    if (!body.token?.trim() || body.token.length > 2048) {
      throw new NotFoundException('Push token is invalid');
    }
    const existing: any[] = await runDbQuery(
      ds,
      `SELECT id FROM push_device_tokens WHERE user_id = ? AND token = ? LIMIT 1`,
      [userId, body.token],
    );
    if (existing.length) {
      await runDbQuery(
        ds,
        `UPDATE push_device_tokens SET last_used_at = NOW(), platform = ? WHERE id = ?`,
        [body.platform || 'web', existing[0].id],
      );
      return { id: existing[0].id, updated: true };
    }
    const id = randomToken('pdt');
    await runDbQuery(
      ds,
      `INSERT INTO push_device_tokens (id, user_id, token, platform, created_at, last_used_at)
       VALUES (?, ?, ?, ?, NOW(), NOW())`,
      [id, userId, body.token, body.platform || 'web'],
    );
    return { id, updated: false };
  }

  async listTokensForUser(tenantId: string, userId: string) {
    await this.flags.assertEnabled(tenantId, 'comms.push');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    return runDbQuery(
      ds,
      `SELECT id, token, platform, created_at as "createdAt", last_used_at as "lastUsedAt"
       FROM push_device_tokens WHERE user_id = ? ORDER BY last_used_at DESC NULLS LAST`,
      [userId],
    );
  }

  async listAllTokens(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'comms.push');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    return runDbQuery(
      ds,
      `SELECT id, user_id as "userId", token, platform, created_at as "createdAt"
       FROM push_device_tokens ORDER BY created_at DESC LIMIT 500`,
      [],
    );
  }

  async resolveSendTokens(tenantId: string, tokens: string[]) {
    await this.flags.assertEnabled(tenantId, 'comms.push');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const requested = [...new Set((tokens || []).map((token) => String(token).trim()).filter(Boolean))].slice(0, 500);
    if (!requested.length) return [];
    const placeholders = requested.map(() => '?').join(',');
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT d.token FROM push_device_tokens d
       LEFT JOIN push_preferences p ON p.user_id = d.user_id
       WHERE d.token IN (${placeholders}) AND COALESCE(p.enabled, true) = true`,
      requested,
    );
    return rows.map((row) => row.token);
  }

  async sendToUser(
    tenantId: string,
    userId: string,
    input: { title: string; body: string; data?: Record<string, string> },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.push');
    const gw = await this.providers.resolvePush(tenantId);
    if (gw.id === 'disabled') return { sent: false, reason: 'provider_unavailable' };
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT d.token FROM push_device_tokens d
       LEFT JOIN push_preferences p ON p.user_id = d.user_id
       WHERE d.user_id = ? AND COALESCE(p.enabled, true) = true`,
      [userId],
    );
    const tokens = rows.map((row) => row.token).filter(Boolean);
    if (!tokens.length) return { sent: false, reason: 'no_tokens' };
    const result = await gw.send({ ...input, tokens });
    return { sent: true, providerId: gw.id, result };
  }

  async getPreferences(tenantId: string, userId: string) {
    await this.flags.assertEnabled(tenantId, 'comms.push');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(ds, `SELECT enabled, categories, updated_at as "updatedAt" FROM push_preferences WHERE user_id = ?`, [userId]);
    const row = rows[0];
    let categories: Record<string, boolean> = {};
    if (row?.categories) {
      try { categories = typeof row.categories === 'string' ? JSON.parse(row.categories) : row.categories; } catch { /* default */ }
    }
    return { enabled: row?.enabled !== false, categories, updatedAt: row?.updatedAt ?? null };
  }

  async updatePreferences(tenantId: string, userId: string, body: { enabled?: boolean; categories?: Record<string, boolean> }) {
    await this.flags.assertEnabled(tenantId, 'comms.push');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const enabled = body.enabled !== false;
    const categories = body.categories && typeof body.categories === 'object' ? body.categories : {};
    await runDbQuery(
      ds,
      `INSERT INTO push_preferences (user_id, enabled, categories, updated_at) VALUES (?, ?, ?, NOW())
       ON CONFLICT (user_id) DO UPDATE SET enabled = EXCLUDED.enabled, categories = EXCLUDED.categories, updated_at = NOW()`,
      [userId, enabled, JSON.stringify(categories)],
    );
    return { enabled, categories };
  }
}
