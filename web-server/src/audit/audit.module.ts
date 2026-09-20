import { Module } from '@nestjs/common';
import { DatabaseModule } from '../database/database.module';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { AuditController } from './audit.controller';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [AuditController],
})
export class AuditModule {}

