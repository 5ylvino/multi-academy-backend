import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { ParentActivationController } from './parent-activation.controller';
import { ParentActivationService } from './parent-activation.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule, NotificationsModule],
  controllers: [ParentActivationController],
  providers: [ParentActivationService],
  exports: [ParentActivationService],
})
export class ParentActivationModule {}
