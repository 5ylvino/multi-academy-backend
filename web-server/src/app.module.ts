import { Module } from '@nestjs/common';
import { APP_FILTER, APP_GUARD, APP_INTERCEPTOR } from '@nestjs/core';
import { ConfigModule } from '@nestjs/config';
import { ThrottlerGuard, ThrottlerModule } from '@nestjs/throttler';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { ControlPlaneModule } from './control-plane/control-plane.module';
import { PlatformConfigModule } from './platform-config/platform-config.module';
import { IdentificationModule } from './identification/identification.module';
import { AuthModule } from './auth/auth.module';
import { DatabaseModule } from './database/database.module';
import { JwtAuthGuard } from './common/auth/jwt-auth.guard';
import { PermissionsGuard } from './common/auth/permissions.guard';
import { EnforcementGuard } from './platform-config/enforcement.guard';
import { FeatureFlagGuard } from './platform-config/feature-flag.guard';
import { ApiExceptionFilter } from './common/filters/api-exception.filter';
import { AuditLogService } from './common/audit/audit-log.service';
import { AuditInterceptor } from './common/audit/audit.interceptor';
import { OrganizationsModule } from './organizations/organizations.module';
import { UsersModule } from './users/users.module';
import { AttendanceModule } from './attendance/attendance.module';
import { AcademicModule } from './academic/academic.module';
import { FinancialModule } from './financial/financial.module';
import { ReportsModule } from './reports/reports.module';
import { AuditModule } from './audit/audit.module';
import { BiometricModule } from './biometric/biometric.module';
import { NotificationsModule } from './notifications/notifications.module';
import { AnnouncementsModule } from './announcements/announcements.module';
import { PortalModule } from './portal/portal.module';
import { RealtimeModule } from './realtime/realtime.module';
import { MessagingModule } from './messaging/messaging.module';
import { CalendarModule } from './calendar/calendar.module';
import { MeetingsModule } from './meetings/meetings.module';
import { TimetableModule } from './timetable/timetable.module';
import { CbtModule } from './cbt/cbt.module';
import { AiModule } from './ai/ai.module';
import { TutoringModule } from './tutoring/tutoring.module';
import { PaymentsModule } from './payments/payments.module';
import { LibraryModule } from './library/library.module';
import { ConsentSlipsModule } from './consent-slips/consent-slips.module';
import { EmergencyBroadcastModule } from './emergency-broadcast/emergency-broadcast.module';
import { FeesOpsModule } from './fees-ops/fees-ops.module';
import { CurriculumModule } from './curriculum/curriculum.module';
import { NurseryModule } from './nursery/nursery.module';
import { PushModule } from './push/push.module';
import { PayrollModule } from './finance/payroll/payroll.module';
import { InventoryModule } from './finance/inventory/inventory.module';
import { BudgetsModule } from './finance/budgets/budgets.module';
import { SyncModule } from './sync/sync.module';
import { TransportModule } from './ops/transport/transport.module';
import { HostelModule } from './ops/hostel/hostel.module';
import { ClinicModule } from './ops/clinic/clinic.module';
import { GateModule } from './ops/gate/gate.module';
import { PrimaryModule } from './primary/primary.module';
import { SecondaryModule } from './secondary/secondary.module';
import { LessonNotesModule } from './lesson-notes/lesson-notes.module';
import { IntegrationsModule } from './integrations/integrations.module';
import { OfflineModule } from './offline/offline.module';
import { ParentActivationModule } from './parent-activation/parent-activation.module';
import { SafeguardingModule } from './safeguarding/safeguarding.module';
import { PtaModule } from './pta/pta.module';
import { ListCacheModule } from './common/cache/list-cache.module';
import { WorkerModule } from './worker/worker.module';
import { InternalWorkerController } from './worker/internal-worker.controller';
import { SlowRequestInterceptor } from './common/observability/slow-request.interceptor';

@Module({
  imports: [
    ConfigModule.forRoot({
      envFilePath: ['.env','.env.production', '.env.development'],
      isGlobal: true,
    }),
    ThrottlerModule.forRoot([
      {
        ttl: 60_000,
        limit: 600,
      },
    ]),
    DatabaseModule,
    PlatformConfigModule,
    ControlPlaneModule,
    IdentificationModule,
    AuthModule,
    OrganizationsModule,
    UsersModule,
    AttendanceModule,
    AcademicModule,
    FinancialModule,
    ReportsModule,
    AuditModule,
    BiometricModule,
    NotificationsModule,
    AnnouncementsModule,
    PortalModule,
    RealtimeModule,
    MessagingModule,
    CalendarModule,
    MeetingsModule,
    TimetableModule,
    CbtModule,
    AiModule,
    TutoringModule,
    PaymentsModule,
    LibraryModule,
    ConsentSlipsModule,
    EmergencyBroadcastModule,
    FeesOpsModule,
    CurriculumModule,
    NurseryModule,
    PushModule,
    LessonNotesModule,
    PayrollModule,
    InventoryModule,
    BudgetsModule,
    SyncModule,
    TransportModule,
    HostelModule,
    ClinicModule,
    GateModule,
    ParentActivationModule,
    SafeguardingModule,
    PtaModule,
    ListCacheModule,
    WorkerModule,
    PrimaryModule,
    SecondaryModule,
    IntegrationsModule,
    OfflineModule,
  ],
  controllers: [AppController, InternalWorkerController],
  providers: [
    AppService,
    { provide: APP_GUARD, useClass: ThrottlerGuard },
    { provide: APP_GUARD, useClass: JwtAuthGuard },
    { provide: APP_GUARD, useClass: EnforcementGuard },
    { provide: APP_GUARD, useClass: PermissionsGuard },
    { provide: APP_GUARD, useClass: FeatureFlagGuard },
    { provide: APP_FILTER, useClass: ApiExceptionFilter },
    AuditLogService,
    { provide: APP_INTERCEPTOR, useClass: AuditInterceptor },
    { provide: APP_INTERCEPTOR, useClass: SlowRequestInterceptor },
  ],
})
export class AppModule {}
