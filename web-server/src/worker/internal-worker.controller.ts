import { Body, Controller, Post, UseGuards } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { Public } from '../common/auth/public.decorator';
import { ServiceJwtGuard } from '../common/auth/service-jwt.guard';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { NotificationsService } from '../notifications/notifications.service';
import { ReportsService } from '../reports/reports.service';

@Controller('internal/worker')
@Public()
@UseGuards(ServiceJwtGuard)
export class InternalWorkerController {
  constructor(
    private readonly notifications: NotificationsService,
    private readonly reports: ReportsService,
  ) {}

  @Post('notifications')
  async createNotification(
    @TenantId() tenantId: string,
    @Body()
    body: {
      userId: string;
      title: string;
      message: string;
      type?: string;
      href?: string;
    },
  ) {
    const result = await this.notifications.createLocal({
      tenantId,
      userId: body.userId,
      title: body.title,
      message: body.message,
      type: body.type as any,
      href: body.href,
    });
    return ok('Notification created', result);
  }

  @Post('reports/download')
  async downloadReport(
    @TenantId() tenantId: string,
    @Body() body: { reportId: string; format: string },
  ) {
    const format = String(body.format || 'pdf').toLowerCase() as 'pdf' | 'docx' | 'csv' | 'json';
    const data = await this.reports.downloadReportLocal(tenantId, body.reportId, format);
    return ok('Report download', data);
  }
}
