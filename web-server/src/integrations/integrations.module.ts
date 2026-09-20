import { Module } from '@nestjs/common';
import { PlatformConfigModule } from '../platform-config/platform-config.module';
import { WorkspaceIntegrationsController } from './workspace-integrations.controller';
import { WorkspaceIntegrationsService } from './workspace-integrations.service';

@Module({
  imports: [PlatformConfigModule],
  controllers: [WorkspaceIntegrationsController],
  providers: [WorkspaceIntegrationsService],
})
export class IntegrationsModule {}
