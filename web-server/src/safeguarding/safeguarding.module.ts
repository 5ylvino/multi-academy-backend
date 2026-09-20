import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { SafeguardingController } from './safeguarding.controller';
import { SafeguardingService } from './safeguarding.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule, NotificationsModule],
  controllers: [SafeguardingController],
  providers: [SafeguardingService],
  exports: [SafeguardingService],
})
export class SafeguardingModule {}
