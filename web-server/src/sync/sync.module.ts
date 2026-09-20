import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { SyncController } from './sync.controller';
import { SyncService } from './sync.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [SyncController],
  providers: [SyncService],
})
export class SyncModule {}
