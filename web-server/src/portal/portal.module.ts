import { Module } from '@nestjs/common';
import { DatabaseModule } from '../database/database.module';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { FinancialModule } from '../financial/financial.module';
import { AcademicModule } from '../academic/academic.module';
import { PlatformConfigModule } from '../platform-config/platform-config.module';
import { ParentPortalController } from './parent-portal.controller';
import { ParentPortalService } from './parent-portal.service';
import { StudentPortalController } from './student-portal.controller';
import { StudentPortalService } from './student-portal.service';
import { BursarPortalController } from './bursar-portal.controller';
import { BursarPortalService } from './bursar-portal.service';
import { PortalReadServiceClient } from './portal-read-service.client';
import { InternalPortalController } from './internal-portal.controller';

@Module({
  imports: [
    DatabaseModule,
    ControlPlaneModule,
    FinancialModule,
    AcademicModule,
    PlatformConfigModule,
  ],
  controllers: [
    ParentPortalController,
    StudentPortalController,
    BursarPortalController,
    InternalPortalController,
  ],
  providers: [
    ParentPortalService,
    StudentPortalService,
    BursarPortalService,
    PortalReadServiceClient,
  ],
})
export class PortalModule {}
