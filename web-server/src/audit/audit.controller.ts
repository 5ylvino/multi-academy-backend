import { Controller, Get, Query, Req } from '@nestjs/common';
import { ControlDbService } from '../database/control-db.service';
import { AuditLogEntity } from '../control-plane/entities/audit-log.entity';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { RequireFeature } from '../platform-config/require-feature.decorator';

@Controller('/audit')
@RequireFeature('audit.logs')
export class AuditController {
  constructor(
    private readonly controlDb: ControlDbService,
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
  ) {}

  private async resolveUserNames(
    tenantId: string | null,
    userIds: string[],
  ): Promise<Map<string, string>> {
    const nameById = new Map<string, string>();
    if (!tenantId || userIds.length === 0) return nameById;

    try {
      const tenant = await this.controlPlane.getTenantById(tenantId);
      if (!tenant) return nameById;
      const ds = await this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
      const placeholders = userIds.map(() => '?').join(', ');
      const rows: Array<{ id: string; name: string }> = await runDbQuery(
        ds,
        `SELECT id, name FROM users WHERE id IN (${placeholders})`,
        userIds,
      );
      for (const row of rows) {
        if (row?.id && row?.name) nameById.set(row.id, row.name);
      }
    } catch {
      // Tenant user lookup is best-effort for audit display.
    }
    return nameById;
  }

  @Get('logs')
  @RequirePermissions('audit_logs:view')
  async listLogs(
    @Req() req: any,
    @Query('limit') limitStr?: string,
    @Query('offset') offsetStr?: string,
  ): Promise<{ items: Array<Partial<AuditLogEntity>>; total: number }> {
    const ds = await this.controlDb.getDataSource();
    const repo = ds.getRepository(AuditLogEntity);

    const effectiveTenantId = req?.user?.tenant_id || null;
    const limit = Math.min(Math.max(parseInt(limitStr || '20', 10) || 20, 1), 100);
    const offset = Math.max(parseInt(offsetStr || '0', 10) || 0, 0);

    const qb = repo
      .createQueryBuilder('a')
      .orderBy('a.createdAt', 'DESC')
      .take(limit)
      .skip(offset);

    if (effectiveTenantId) {
      qb.andWhere('a.tenantId = :tenantId', { tenantId: effectiveTenantId });
    }

    const [rows, total] = await qb.getManyAndCount();

    const userIds = [
      ...new Set(rows.map((r) => r.userId).filter((id): id is string => !!id)),
    ];
    const nameById = await this.resolveUserNames(effectiveTenantId, userIds);

    const items = rows.map((r) => ({
      id: r.id,
      userId: r.userId,
      userName: r.userId ? nameById.get(r.userId) || null : null,
      tenantId: r.tenantId,
      method: r.method,
      path: r.path,
      statusCode: r.statusCode,
      ip: r.ip,
      userAgent: r.userAgent,
      createdAt: r.createdAt,
    }));

    return { items, total };
  }
}
