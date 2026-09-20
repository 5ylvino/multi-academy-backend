import { Module } from '@nestjs/common';
import { AttendanceController } from './attendance.controller';
import { AttendanceService } from './attendance.service';
import { AbsenceAlertsService } from './absence-alerts.service';
import { DatabaseModule } from '../database/database.module';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { BiometricModule } from '../biometric/biometric.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { OrganizationsModule } from '../organizations/organizations.module';

@Module({
  imports: [
    DatabaseModule,
    ControlPlaneModule,
    BiometricModule,
    NotificationsModule,
    OrganizationsModule,
  ],
  controllers: [AttendanceController],
  providers: [AttendanceService, AbsenceAlertsService],
})
export class AttendanceModule {}
