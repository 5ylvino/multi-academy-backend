import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { RealtimeModule } from '../realtime/realtime.module';
import { MessagingController } from './messaging.controller';
import { MessagingService } from './messaging.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule, NotificationsModule, RealtimeModule],
  controllers: [MessagingController],
  providers: [MessagingService],
})
export class MessagingModule {}
