import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

@Injectable()
export class TermRankingService {
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

  async rankClass(tenantId: string, classId: string, termId: string) {
    await this.flags.assertEnabled(tenantId, 'academic.term_ranking');
    const ds = await this.getTenantDs(tenantId);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT r.student_id as "studentId",
              COALESCE(u.full_name, r.student_id) as "studentName",
              COUNT(r.id) as "subjectCount",
              AVG(r.total_score) as "averageScore",
              SUM(r.total_score) as "totalScore"
       FROM academic_results r
       LEFT JOIN users u ON u.id = r.student_id
       WHERE r.class_id = ? AND r.term_id = ? AND r.status = 'approved' AND r.total_score IS NOT NULL
       GROUP BY r.student_id, u.full_name
       ORDER BY AVG(r.total_score) DESC`,
      [classId, termId],
    );
    return rows.map((r, i) => ({
      position: competitionPosition(rows, i),
      studentId: r.studentId,
      studentName: r.studentName,
      subjectCount: Number(r.subjectCount),
      averageScore: Math.round(Number(r.averageScore || 0) * 100) / 100,
      totalScore: Math.round(Number(r.totalScore || 0) * 100) / 100,
    }));
  }
}

export function competitionPosition(rows: any[], index: number): number {
  if (index <= 0) return 1;
  const score = Number(rows[index]?.averageScore ?? rows[index]?.avg ?? 0);
  const preceding = rows
    .slice(0, index)
    .filter((row) => Number(row.averageScore ?? row.avg ?? 0) > score).length;
  return preceding + 1;
}
