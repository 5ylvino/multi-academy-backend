import { ForbiddenException, Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { AuthUserClaims } from '../common/auth/auth-user.interface';

export const NURSERY_ROLES = [
  'class_teacher',
  'head_teacher',
  'principal',
  'school_admin',
] as const;

export function canAccessNursery(roles: string[] | undefined): boolean {
  return (roles || []).some((role) => NURSERY_ROLES.includes(role as (typeof NURSERY_ROLES)[number]));
}

/** Phase 4 nursery developmental + wellness stubs. */
@Injectable()
export class NurseryService {
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

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS nursery_developmental (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        domain varchar(64) NOT NULL,
        level varchar(32) NOT NULL,
        notes text NULL,
        observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS nursery_wellness (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        mood varchar(32) NULL,
        appetite varchar(32) NULL,
        nap_minutes int NULL,
        notes text NULL,
        logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS nursery_media_moments (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        url text NOT NULL,
        caption text NULL,
        logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  private scope(actor: AuthUserClaims, alias: string, studentExpression = `${alias}.student_id`) {
    if (!canAccessNursery(actor.roles)) {
      throw new ForbiddenException('Nursery access is limited to nursery teaching and school leadership roles');
    }
    const actorId = actor.user_id || actor.sub;
    if (actor.roles.some((role) => ['head_teacher', 'principal', 'school_admin'].includes(role))) {
      return {
        sql: `EXISTS (
          SELECT 1 FROM student_class_enrollments sce
          JOIN academic_classes ac ON ac.id = sce.class_id
          WHERE sce.student_id = ${studentExpression} AND ac.school_level = 'nursery'
        )`,
        params: [],
      };
    }
    return {
      sql: `EXISTS (
        SELECT 1
        FROM student_class_enrollments sce
        JOIN academic_class_teacher_links ctl ON ctl.class_id = sce.class_id
        JOIN academic_classes ac ON ac.id = sce.class_id
        WHERE sce.student_id = ${studentExpression}
          AND ctl.teacher_id = ?
          AND ac.school_level = 'nursery'
      )`,
      params: [actorId],
    };
  }

  private async assertStudentInScope(ds: any, actor: AuthUserClaims, studentId: string) {
    const scope = this.scope(actor, 'n', 'n.id');
    const rows = await runDbQuery(
      ds,
      `SELECT 1 FROM users n WHERE n.id = ? AND ${scope.sql} LIMIT 1`,
      [studentId, ...scope.params],
    );
    if (!rows?.length) {
      throw new ForbiddenException('Student is outside your assigned nursery scope');
    }
  }

  async listMediaMoments(tenantId: string, actor: AuthUserClaims) {
    await this.flags.assertEnabled(tenantId, 'nursery.media_moments');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const scope = this.scope(actor, 'n');
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", url, caption, logged_at as "loggedAt"
       FROM nursery_media_moments n
       WHERE ${scope.sql}
       ORDER BY logged_at DESC LIMIT 200`,
      scope.params,
    );
  }

  async addMediaMoment(
    tenantId: string,
    body: { studentId: string; url: string; caption?: string },
    actor: AuthUserClaims,
  ) {
    await this.flags.assertEnabled(tenantId, 'nursery.media_moments');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertStudentInScope(ds, actor, body.studentId);
    const id = randomToken('nmed');
    await runDbQuery(
      ds,
      `INSERT INTO nursery_media_moments (id, student_id, url, caption, logged_at)
       VALUES (?, ?, ?, ?, NOW())`,
      [id, body.studentId, body.url, body.caption || null],
    );
    return { id };
  }

  async listDevelopmental(tenantId: string, actor: AuthUserClaims) {
    await this.flags.assertEnabled(tenantId, 'nursery.developmental');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const scope = this.scope(actor, 'n');
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", domain, level, notes,
              observed_at as "observedAt" FROM nursery_developmental n
       WHERE ${scope.sql}
       ORDER BY observed_at DESC LIMIT 200`,
      scope.params,
    );
  }

  async addDevelopmental(
    tenantId: string,
    body: { studentId: string; domain: string; level: string; notes?: string },
    actor: AuthUserClaims,
  ) {
    await this.flags.assertEnabled(tenantId, 'nursery.developmental');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertStudentInScope(ds, actor, body.studentId);
    const id = randomToken('ndev');
    await runDbQuery(
      ds,
      `INSERT INTO nursery_developmental (id, student_id, domain, level, notes)
       VALUES (?, ?, ?, ?, ?)`,
      [id, body.studentId, body.domain, body.level, body.notes || null],
    );
    return { id };
  }

  async listWellness(tenantId: string, actor: AuthUserClaims) {
    await this.flags.assertEnabled(tenantId, 'nursery.wellness');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const scope = this.scope(actor, 'n');
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", mood, appetite, nap_minutes as "napMinutes",
              notes, logged_at as "loggedAt" FROM nursery_wellness n
       WHERE ${scope.sql}
       ORDER BY logged_at DESC LIMIT 200`,
      scope.params,
    );
  }

  async addWellness(
    tenantId: string,
    body: {
      studentId: string;
      mood?: string;
      appetite?: string;
      napMinutes?: number;
      notes?: string;
    },
    actor: AuthUserClaims,
  ) {
    await this.flags.assertEnabled(tenantId, 'nursery.wellness');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertStudentInScope(ds, actor, body.studentId);
    const id = randomToken('nwell');
    await runDbQuery(
      ds,
      `INSERT INTO nursery_wellness (id, student_id, mood, appetite, nap_minutes, notes)
       VALUES (?, ?, ?, ?, ?, ?)`,
      [
        id,
        body.studentId,
        body.mood || null,
        body.appetite || null,
        body.napMinutes ?? null,
        body.notes || null,
      ],
    );
    return { id };
  }
}
