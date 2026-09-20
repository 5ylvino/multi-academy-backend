import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { loadUserRoles } from '../common/auth/teacher-scope.util';

@Injectable()
export class RiskAnalyticsService {
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

  /** Early-warning list: low attendance + falling grades. */
  async listAtRisk(tenantId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'ai.risk_analytics');
    const ds = await this.getTenantDs(tenantId);
    const roles = await loadUserRoles(ds, actorId);
    const scope = roles.includes('parent')
      ? ` AND student_id IN (SELECT student_id FROM parent_student_links WHERE parent_id = ?)`
      : roles.includes('student') ? ` AND student_id = ?` : '';
    const since = new Date(Date.now() - 60 * 86400000).toISOString();
    const earlier = new Date(Date.now() - 120 * 86400000).toISOString();

    const recent: any[] = await runDbQuery(
      ds,
      `SELECT student_id as "studentId", AVG(total_score) as "avgScore"
       FROM academic_results WHERE created_at >= ? AND total_score IS NOT NULL${scope}
       GROUP BY student_id`,
      scope ? [since, actorId] : [since],
    );
    const prior: any[] = await runDbQuery(
      ds,
      `SELECT student_id as "studentId", AVG(total_score) as "avgScore"
       FROM academic_results WHERE created_at >= ? AND created_at < ? AND total_score IS NOT NULL${scope}
       GROUP BY student_id`,
      scope ? [earlier, since, actorId] : [earlier, since],
    );
    const priorMap = new Map(prior.map((p) => [p.studentId, Number(p.avgScore)]));

    const attendance: any[] = await runDbQuery(
      ds,
      `SELECT student_id as "studentId",
              SUM(CASE WHEN LOWER(status) = 'present' THEN 1 ELSE 0 END) as "present",
              COUNT(*) as "total"
       FROM attendance_students WHERE created_at >= ?${scope}
       GROUP BY student_id`,
      scope ? [since, actorId] : [since],
    );
    const attMap = new Map<string, number>();
    for (const row of attendance) {
      attMap.set(row.studentId, row.total > 0 ? Number(row.present) / Number(row.total) : 1);
    }

    const atRisk = [];
    for (const r of recent) {
      const current = Number(r.avgScore);
      const previous = priorMap.get(r.studentId);
      const falling = previous !== undefined && current < previous - 5;
      const attRate = attMap.get(r.studentId) ?? 1;
      const lowAtt = attRate < 0.8;
      if (falling && lowAtt) {
        atRisk.push({
          studentId: r.studentId,
          currentAvg: Math.round(current * 10) / 10,
          previousAvg: previous !== undefined ? Math.round(previous * 10) / 10 : null,
          attendanceRate: Math.round(attRate * 100),
          riskLevel: current < 45 ? 'high' : 'medium',
        });
      }
    }
    return { count: atRisk.length, students: atRisk };
  }

}
