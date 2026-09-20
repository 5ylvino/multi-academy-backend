import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { FeesOpsController } from './fees-ops.controller';
import { FeesOpsService } from './fees-ops.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [FeesOpsController],
  providers: [FeesOpsService],
  exports: [FeesOpsService],
})
export class FeesOpsModule {}
