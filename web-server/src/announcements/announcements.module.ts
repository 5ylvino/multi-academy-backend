import { Module } from '@nestjs/common';
import { AnnouncementsController } from './announcements.controller';
import { AnnouncementsService } from './announcements.service';
import { DatabaseModule } from '../database/database.module';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { ListCacheModule } from '../common/cache/list-cache.module';

@Module({
  imports: [DatabaseModule, ControlPlaneModule, NotificationsModule, ListCacheModule],
  controllers: [AnnouncementsController],
  providers: [AnnouncementsService],
})
export class AnnouncementsModule {}
