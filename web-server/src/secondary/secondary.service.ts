import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { getVisibleStudentIds } from '../common/auth/teacher-scope.util';

@Injectable()
export class SecondaryService {
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
      CREATE TABLE IF NOT EXISTS secondary_career_entries (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        title varchar(255) NOT NULL,
        description text NULL,
        category varchar(64) NULL,
        url text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async listCareer(tenantId: string, studentId?: string, actorUserId?: string) {
    await this.flags.assertEnabled(tenantId, 'secondary.career');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const visibleStudentIds = actorUserId
      ? await getVisibleStudentIds(ds, actorUserId)
      : null;
    if (studentId && visibleStudentIds && !visibleStudentIds.includes(studentId)) return [];
    if (!studentId && visibleStudentIds) {
      if (!visibleStudentIds.length) return [];
      return runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", title, description, category, url, created_at as "createdAt"
         FROM secondary_career_entries
         WHERE student_id IN (${visibleStudentIds.map(() => '?').join(',')})
         ORDER BY created_at DESC LIMIT 200`,
        visibleStudentIds,
      );
    }
    if (studentId) {
      return runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", title, description, category, url, created_at as "createdAt"
         FROM secondary_career_entries WHERE student_id = ? ORDER BY created_at DESC LIMIT 200`,
        [studentId],
      );
    }
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", title, description, category, url, created_at as "createdAt"
       FROM secondary_career_entries ORDER BY created_at DESC LIMIT 200`,
      [],
    );
  }

  async addCareerEntry(
    tenantId: string,
    body: { studentId: string; title: string; description?: string; category?: string; url?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'secondary.career');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const id = randomToken('car');
    await runDbQuery(
      ds,
      `INSERT INTO secondary_career_entries (id, student_id, title, description, category, url, created_at)
       VALUES (?, ?, ?, ?, ?, ?, NOW())`,
      [id, body.studentId, body.title, body.description || null, body.category || null, body.url || null],
    );
    return { id };
  }
}
