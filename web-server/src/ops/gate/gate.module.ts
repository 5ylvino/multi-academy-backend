import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../../control-plane/control-plane.module';
import { DatabaseModule } from '../../database/database.module';
import { NotificationsModule } from '../../notifications/notifications.module';
import { SafeguardingModule } from '../../safeguarding/safeguarding.module';
import { GateController } from './gate.controller';
import { GateService } from './gate.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule, NotificationsModule, SafeguardingModule],
  controllers: [GateController],
  providers: [GateService],
})
export class GateModule {}
