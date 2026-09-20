import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { NotificationsService } from '../notifications/notifications.service';
import { mapDataToUpdateKeys } from '../util/mapDataToUpdateKeys';
import { ListCacheService } from '../common/cache/list-cache.service';

const STAFF_ROLES = [
  'director',
  'school_admin',
  'it_admin',
  'head_teacher',
  'principal',
  'assistant_head_teacher',
  'bursar',
  'class_teacher',
  'subject_teacher',
  'administrative_staff',
];

const PENDING_CRITICAL_CACHE_TTL_MS = 30_000;

@Injectable()
export class AnnouncementsService {
  private readonly ensuredTenants = new Set<string>();

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly notificationsService: NotificationsService,
    private readonly listCache: ListCacheService,
  ) {}

  private invalidateCriticalCache(tenantId: string) {
    this.listCache.invalidate(`tenant:${tenantId}:announcements:critical:`);
  }

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureTables(tenantId: string, ds: any) {
    if (this.ensuredTenants.has(tenantId)) return;
    await ds.query(`
      CREATE TABLE IF NOT EXISTS announcements (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        body text NOT NULL,
        audience varchar(32) NOT NULL DEFAULT 'all',
        school_level varchar(32) NULL,
        is_published boolean NOT NULL DEFAULT true,
        is_critical boolean NOT NULL DEFAULT false,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    // Phase-1 tenant DBs created announcements without these columns.
    // CREATE TABLE IF NOT EXISTS is a no-op on the old table, so ALTER is required.
    await ds.query(
      `ALTER TABLE announcements ADD COLUMN IF NOT EXISTS school_level varchar(32) NULL`,
    );
    await ds.query(
      `ALTER TABLE announcements ADD COLUMN IF NOT EXISTS is_published boolean NOT NULL DEFAULT true`,
    );
    await ds.query(
      `ALTER TABLE announcements ADD COLUMN IF NOT EXISTS is_critical boolean NOT NULL DEFAULT false`,
    );
    await ds.query(`
      CREATE TABLE IF NOT EXISTS announcement_acks (
        announcement_id varchar(64) NOT NULL,
        user_id varchar(64) NOT NULL,
        acknowledged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (announcement_id, user_id)
      );
    `);
    await ds.query(`
      CREATE INDEX IF NOT EXISTS idx_announcements_critical_published
      ON announcements (is_critical, is_published, created_at DESC);
    `);
    await ds.query(`
      CREATE INDEX IF NOT EXISTS idx_announcement_acks_user
      ON announcement_acks (user_id, announcement_id);
    `);
    this.ensuredTenants.add(tenantId);
  }

  async list(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    const rows = await ds.query(`
      SELECT id, title, body, audience, school_level as schoolLevel,
             is_published as isPublished, is_critical as isCritical, created_by as createdBy,
             created_at as createdAt, updated_at as updatedAt
      FROM announcements
      ORDER BY created_at DESC
      LIMIT 200
    `);
    return mapDataToUpdateKeys(rows, { ispublished: 'isPublished' });
  }

  async create(tenantId: string, userId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    const id = randomToken('ann');
    const isPublished = body.isPublished !== false;
    await runDbQuery(
      ds,
      `INSERT INTO announcements
        (id, title, body, audience, school_level, is_published, is_critical, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        body.title,
        body.body,
        body.audience || 'all',
        body.schoolLevel || null,
        isPublished,
        !!body.isCritical,
        userId,
      ],
    );

    if (isPublished) {
      await this.notifyAudience(tenantId, ds, body.audience || 'all', body.title, id).catch(() => {
        // best-effort notification
      });
    }
    if (body.isCritical) {
      this.invalidateCriticalCache(tenantId);
    }

    return { id };
  }

  private async notifyAudience(
    tenantId: string,
    ds: any,
    audience: string,
    title: string,
    announcementId: string,
  ) {
    const users: Array<{ id: string; roles: string }> = await runDbQuery(
      ds,
      `SELECT id, roles FROM users WHERE is_active = true`,
    );
    const targets = (users || []).filter((u) => {
      let roles: string[] = [];
      try {
        roles = typeof u.roles === 'string' ? JSON.parse(u.roles) : Array.isArray(u.roles) ? u.roles : [];
      } catch {
        roles = [];
      }
      if (audience === 'all') return true;
      if (audience === 'students') return roles.includes('student');
      if (audience === 'parents') return roles.includes('parent');
      if (audience === 'staff') return roles.some((r) => STAFF_ROLES.includes(r));
      return false;
    });

    // Cap broadcast to avoid flooding
    for (const user of targets.slice(0, 200)) {
      await this.notificationsService.create({
        tenantId,
        userId: user.id,
        title: 'New announcement',
        message: title,
        type: 'info',
        href: '/dashboard/announcements',
      });
    }
    return { notified: Math.min(targets.length, 200), announcementId };
  }

  async update(tenantId: string, id: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    const existing = await runDbQuery(ds, `SELECT id FROM announcements WHERE id = ? LIMIT 1`, [id]);
    if (!existing.length) throw new NotFoundException('Announcement not found');

    const updates: string[] = [];
    const values: any[] = [];
    const set = (col: string, val: any) => {
      updates.push(`${col} = ?`);
      values.push(val);
    };
    if (body.title !== undefined) set('title', body.title);
    if (body.body !== undefined) set('body', body.body);
    if (body.audience !== undefined) set('audience', body.audience);
    if (body.schoolLevel !== undefined) set('school_level', body.schoolLevel || null);
    if (body.isPublished !== undefined) set('is_published', !!body.isPublished);
    if (body.isCritical !== undefined) set('is_critical', !!body.isCritical);

    if (updates.length === 0) {
      throw new BadRequestException('No fields to update');
    }
    values.push(id);
    await runDbQuery(
      ds,
      `UPDATE announcements SET ${updates.join(', ')}, updated_at = NOW() WHERE id = ?`,
      values,
    );
    if (
      body.isCritical !== undefined ||
      body.isPublished !== undefined
    ) {
      this.invalidateCriticalCache(tenantId);
    }
    return { id };
  }

  async remove(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    await runDbQuery(ds, `DELETE FROM announcements WHERE id = ?`, [id]);
    return { id };
  }

  async acknowledge(tenantId: string, userId: string, announcementId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    await runDbQuery(
      ds,
      `INSERT INTO announcement_acks (announcement_id, user_id, acknowledged_at)
       VALUES (?, ?, NOW())
       ON CONFLICT (announcement_id, user_id) DO NOTHING`,
      [announcementId, userId],
    );
    this.invalidateCriticalCache(tenantId);
    return { announcementId, acknowledged: true };
  }

  async pendingCritical(tenantId: string, userId: string) {
    return this.listCache.getOrLoad(
      `tenant:${tenantId}:announcements:critical:${userId}`,
      async () => {
        const ds = await this.getTenantDs(tenantId);
        await this.ensureTables(tenantId, ds);
        return runDbQuery(
          ds,
          `SELECT a.id, a.title, a.body, a.created_at as "createdAt"
           FROM announcements a
           WHERE a.is_critical = true AND a.is_published = true
             AND NOT EXISTS (
               SELECT 1 FROM announcement_acks k
               WHERE k.announcement_id = a.id AND k.user_id = ?
             )
           ORDER BY a.created_at DESC
           LIMIT 10`,
          [userId],
        );
      },
      false,
      PENDING_CRITICAL_CACHE_TTL_MS,
    );
  }
}
