import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { WorkerServiceClient } from '../worker/worker-service.client';

export type NotificationType = 'info' | 'success' | 'warning' | 'error';

@Injectable()
export class NotificationsService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly worker: WorkerServiceClient,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureTables(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS notifications (
        id varchar(64) PRIMARY KEY,
        user_id varchar(64) NOT NULL,
        title varchar(255) NOT NULL,
        message text NOT NULL,
        type varchar(16) NOT NULL DEFAULT 'info',
        href varchar(512) NULL,
        is_read boolean NOT NULL DEFAULT false,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE INDEX IF NOT EXISTS idx_notifications_user_read
      ON notifications (user_id, is_read, created_at DESC);
    `);
  }

  async create(params: {
    tenantId: string;
    userId: string;
    title: string;
    message: string;
    type?: NotificationType;
    href?: string;
  }) {
    if (this.worker.isEnabled()) {
      void this.worker.enqueueNotification(params);
      return { id: `queued-${Date.now()}` };
    }
    return this.createLocal(params);
  }

  async createLocal(params: {
    tenantId: string;
    userId: string;
    title: string;
    message: string;
    type?: NotificationType;
    href?: string;
  }) {
    const ds = await this.getTenantDs(params.tenantId);
    await this.ensureTables(ds);
    const id = randomToken('ntf');
    await runDbQuery(
      ds,
      `INSERT INTO notifications (id, user_id, title, message, type, href, is_read, created_at)
       VALUES (?, ?, ?, ?, ?, ?, false, NOW())`,
      [
        id,
        params.userId,
        params.title,
        params.message,
        params.type || 'info',
        params.href || null,
      ],
    );
    return { id };
  }

  async listForUser(tenantId: string, userId: string, limit = 30) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const safeLimit = Math.min(Math.max(Number(limit) || 30, 1), 100);
    const rows = await runDbQuery(
      ds,
      `SELECT id, title, message, type, href, is_read as "isRead", created_at as "createdAt"
       FROM notifications WHERE user_id = ?
       ORDER BY created_at DESC LIMIT ${safeLimit}`,
      [userId],
    );
    const unreadRows: any[] = await runDbQuery(
      ds,
      `SELECT COUNT(*)::int as count FROM notifications WHERE user_id = ? AND is_read = false`,
      [userId],
    );
    return {
      items: rows,
      unreadCount: Number(unreadRows[0]?.count || unreadRows[0]?.COUNT || 0),
    };
  }

  async markRead(tenantId: string, userId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(
      ds,
      `UPDATE notifications SET is_read = true WHERE id = ? AND user_id = ?`,
      [id, userId],
    );
    return { id };
  }

  async markAllRead(tenantId: string, userId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(
      ds,
      `UPDATE notifications SET is_read = true WHERE user_id = ? AND is_read = false`,
      [userId],
    );
    return { ok: true };
  }
}
