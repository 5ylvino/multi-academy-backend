import { Body, Controller, Delete, Get, Param, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { ReportsService } from './reports.service';
import { CreateReportDto } from './dto/reports.dto';
import { RequireFeature } from '../platform-config/require-feature.decorator';

@Controller('reports')
@RequireFeature('reports.view')
export class ReportsController {
  constructor(private readonly reportsService: ReportsService) {}

  @Get()
  @RequirePermissions('reports:view', 'reports:generate')
  async list(@TenantId() tenantId: string, @Query('category') category?: string) {
    return ok('Reports', await this.reportsService.listReports(tenantId, category));
  }

  @Get('summary/overview')
  @RequirePermissions('reports:view', 'analytics:view')
  async summary(@TenantId() tenantId: string) {
    return ok('Report summary', await this.reportsService.getSummary(tenantId));
  }

  @Get('advanced/dashboard')
  @RequireFeature('reports.advanced')
  @RequirePermissions('reports:view', 'analytics:view')
  async advanced(@TenantId() tenantId: string) {
    return ok('Advanced analytics', await this.reportsService.getAdvancedDashboard(tenantId));
  }

  @Get(':id/download')
  @RequirePermissions('reports:export', 'reports:view', 'reports:generate')
  async download(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Query('format') format?: string,
  ) {
    const allowed = new Set(['csv', 'json', 'pdf', 'docx', 'doc']);
    const raw = String(format || 'csv').toLowerCase();
    const normalized = (allowed.has(raw) ? raw : 'csv') as
      | 'csv'
      | 'json'
      | 'pdf'
      | 'docx'
      | 'doc';
    const data = await this.reportsService.downloadReport(
      tenantId,
      id,
      normalized,
      user.user_id,
    );
    return ok('Report download', data);
  }

  @Post()
  @RequirePermissions('reports:generate', 'reports:export')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateReportDto,
  ) {
    return ok('Report created', await this.reportsService.createReport(tenantId, user.user_id, body));
  }

  @Delete(':id')
  @RequirePermissions('reports:generate', 'reports:export')
  async remove(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Report deleted', await this.reportsService.deleteReport(tenantId, id));
  }
}
