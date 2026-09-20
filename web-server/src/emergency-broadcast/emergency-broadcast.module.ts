import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { EmergencyBroadcastController } from './emergency-broadcast.controller';
import { EmergencyBroadcastService } from './emergency-broadcast.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [EmergencyBroadcastController],
  providers: [EmergencyBroadcastService],
})
export class EmergencyBroadcastModule {}
