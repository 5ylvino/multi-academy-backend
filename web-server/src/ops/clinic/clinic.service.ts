import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../../control-plane/control-plane.service';
import { TenantConnectionService } from '../../database/tenant-connection.service';
import { randomToken } from '../../common/utils/id.util';
import { runDbQuery } from '../../database/db-driver.util';
import { FeatureFlagService } from '../../platform-config/feature-flag.service';

@Injectable()
export class ClinicService {
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
      CREATE TABLE IF NOT EXISTS clinic_visits (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        symptoms text NULL,
        treatment text NULL,
        nurse_id varchar(64) NULL,
        visited_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    try {
      await ds.query(
        `ALTER TABLE clinic_visits ADD COLUMN IF NOT EXISTS severity varchar(16) NOT NULL DEFAULT 'minor'`,
      );
    } catch {
      // Older engines without IF NOT EXISTS — the column already exists
    }
  }

  async list(tenantId: string, actorId: string, studentId?: string) {
    await this.flags.assertEnabled(tenantId, 'ops.clinic');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.getUserRoles(ds, actorId);
    const isManager = roles.some(canManageClinicRole);
    if (!isManager && roles.includes('student')) studentId = actorId;
    if (!isManager && roles.includes('parent')) {
      if (studentId) {
        const linked = await runDbQuery(
          ds,
          `SELECT 1 FROM parent_student_links WHERE parent_id = ? AND student_id = ? LIMIT 1`,
          [actorId, studentId],
        );
        if (!linked.length) throw new NotFoundException('Student is not linked to this parent');
      }
    }
    if (!isManager && !roles.includes('student') && !roles.includes('parent')) {
      throw new NotFoundException('Clinic records are restricted to authorized roles');
    }
    if (studentId) {
      return runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", symptoms, treatment, severity, nurse_id as "nurseId",
                visited_at as "visitedAt", created_at as "createdAt"
         FROM clinic_visits WHERE student_id = ? ORDER BY visited_at DESC LIMIT 100`,
        [studentId],
      );
    }
    const parentScope = roles.includes('parent')
      ? `WHERE student_id IN (SELECT student_id FROM parent_student_links WHERE parent_id = ?)`
      : '';
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", symptoms, treatment, severity, nurse_id as "nurseId",
              visited_at as "visitedAt", created_at as "createdAt"
       FROM clinic_visits ${parentScope} ORDER BY visited_at DESC LIMIT 200`,
      roles.includes('parent') ? [actorId] : [],
    );
  }

  async logVisit(
    tenantId: string,
    actorId: string,
    body: {
      studentId: string;
      symptoms?: string;
      treatment?: string;
      severity?: string;
      nurseId?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.clinic');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.getUserRoles(ds, actorId);
    if (!roles.some(canManageClinicRole)) {
      throw new NotFoundException('Only clinic staff or school administrators may log visits');
    }
    const id = randomToken('cln');
    const severity = ['minor', 'moderate', 'urgent'].includes(String(body.severity))
      ? String(body.severity)
      : 'minor';
    await runDbQuery(
      ds,
      `INSERT INTO clinic_visits (id, student_id, symptoms, treatment, severity, nurse_id, visited_at, created_at)
       VALUES (?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        body.studentId,
        body.symptoms || null,
        body.treatment || null,
        severity,
        body.nurseId || actorId,
      ],
    );
    return { id, severity };
  }

  private async getUserRoles(ds: any, userId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const row = rows[0];
    if (!row) return [];
    if (Array.isArray(row.roles)) return row.roles.map((role: string) => String(role).toLowerCase());
    if (typeof row.roles === 'string') {
      try {
        const parsed = JSON.parse(row.roles);
        if (Array.isArray(parsed)) return parsed.map((role: string) => String(role).toLowerCase());
      } catch {
        // Legacy role fallback.
      }
    }
    return row.role ? [String(row.role).toLowerCase()] : [];
  }
}

export function canManageClinicRole(role: string): boolean {
  return ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'nurse', 'administrative_staff'].includes(role);
}
