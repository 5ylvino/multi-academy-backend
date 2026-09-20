import { Controller, Get } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { BursarPortalService } from './bursar-portal.service';

@Controller('portal/bursar')
@RequireFeature('portal.bursar')
export class BursarPortalController {
  constructor(private readonly bursar: BursarPortalService) {}

  @Get('dashboard')
  @RequirePermissions('fees:view', 'payments:view', 'invoices:read')
  async dashboard(@TenantId() tenantId: string) {
    return ok('Bursar dashboard', await this.bursar.getDashboard(tenantId));
  }
}
