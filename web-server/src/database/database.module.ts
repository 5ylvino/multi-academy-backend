import { Module } from '@nestjs/common';
import { TenantConnectionService } from './tenant-connection.service';
import { ControlDbService } from './control-db.service';

@Module({
  providers: [TenantConnectionService, ControlDbService],
  exports: [TenantConnectionService, ControlDbService],
})
export class DatabaseModule {}

