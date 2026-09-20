import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { getVisibleStudentIds } from '../common/auth/teacher-scope.util';

@Injectable()
export class PrimaryService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureBadges(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS primary_badges (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        badge_type varchar(64) NOT NULL,
        title varchar(255) NOT NULL,
        awarded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  private async ensureLiteracy(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS primary_literacy_log (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        skill varchar(128) NOT NULL,
        level varchar(32) NOT NULL,
        notes text NULL,
        logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async listBadges(tenantId: string, studentId?: string, actorUserId?: string) {
    await this.flags.assertEnabled(tenantId, 'primary.badges');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureBadges(ds);
    const visibleStudentIds = actorUserId
      ? await getVisibleStudentIds(ds, actorUserId)
      : null;
    if (studentId && visibleStudentIds && !visibleStudentIds.includes(studentId)) return [];
    if (!studentId && visibleStudentIds) {
      if (!visibleStudentIds.length) return [];
      return runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", badge_type as "badgeType", title,
                awarded_at as "awardedAt" FROM primary_badges
         WHERE student_id IN (${visibleStudentIds.map(() => '?').join(',')})
         ORDER BY awarded_at DESC LIMIT 200`,
        visibleStudentIds,
      );
    }
    if (studentId) {
      return runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", badge_type as "badgeType", title,
                awarded_at as "awardedAt" FROM primary_badges WHERE student_id = ? ORDER BY awarded_at DESC LIMIT 200`,
        [studentId],
      );
    }
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", badge_type as "badgeType", title,
              awarded_at as "awardedAt" FROM primary_badges ORDER BY awarded_at DESC LIMIT 200`,
      [],
    );
  }

  async awardBadge(
    tenantId: string,
    body: { studentId: string; badgeType: string; title: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'primary.badges');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureBadges(ds);
    const id = randomToken('bdg');
    await runDbQuery(
      ds,
      `INSERT INTO primary_badges (id, student_id, badge_type, title, awarded_at, created_at)
       VALUES (?, ?, ?, ?, NOW(), NOW())`,
      [id, body.studentId, body.badgeType, body.title],
    );
    return { id };
  }

  async listLiteracy(tenantId: string, studentId?: string, actorUserId?: string) {
    await this.flags.assertEnabled(tenantId, 'primary.literacy_log');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureLiteracy(ds);
    const visibleStudentIds = actorUserId
      ? await getVisibleStudentIds(ds, actorUserId)
      : null;
    if (studentId && visibleStudentIds && !visibleStudentIds.includes(studentId)) return [];
    if (!studentId && visibleStudentIds) {
      if (!visibleStudentIds.length) return [];
      return runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", skill, level, notes, logged_at as "loggedAt"
         FROM primary_literacy_log
         WHERE student_id IN (${visibleStudentIds.map(() => '?').join(',')})
         ORDER BY logged_at DESC LIMIT 200`,
        visibleStudentIds,
      );
    }
    if (studentId) {
      return runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", skill, level, notes, logged_at as "loggedAt"
         FROM primary_literacy_log WHERE student_id = ? ORDER BY logged_at DESC LIMIT 200`,
        [studentId],
      );
    }
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", skill, level, notes, logged_at as "loggedAt"
       FROM primary_literacy_log ORDER BY logged_at DESC LIMIT 200`,
      [],
    );
  }

  async logLiteracy(
    tenantId: string,
    body: { studentId: string; skill: string; level: string; notes?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'primary.literacy_log');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureLiteracy(ds);
    const id = randomToken('lit');
    await runDbQuery(
      ds,
      `INSERT INTO primary_literacy_log (id, student_id, skill, level, notes, logged_at)
       VALUES (?, ?, ?, ?, ?, NOW())`,
      [id, body.studentId, body.skill, body.level, body.notes || null],
    );
    return { id };
  }
}
