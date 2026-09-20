import { Controller, Get } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { WorkspaceIntegrationsService } from './workspace-integrations.service';

@Controller('integrations/workspace')
@RequireFeature('integrations.workspace')
export class WorkspaceIntegrationsController {
  constructor(private readonly workspace: WorkspaceIntegrationsService) {}

  @Get('status')
  @RequirePermissions('organization:view', 'system_config:view')
  async status(@TenantId() tenantId: string) {
    return ok('Workspace status', await this.workspace.getStatus(tenantId));
  }

  @Get('extras')
  @RequirePermissions('organization:view', 'system_config:view')
  async extras(@TenantId() tenantId: string) {
    return ok('Workspace extras', await this.workspace.listExtras(tenantId));
  }
}
