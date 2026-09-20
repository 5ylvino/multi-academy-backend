import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { TimetableModule } from '../timetable/timetable.module';
import { AiController } from './ai.controller';
import { AiContextService } from './ai-context.service';
import { AiClassInsightsService } from './ai-class-insights.service';
import { AiSignalsService } from './ai-signals.service';
import { AiServiceClient } from './ai-service.client';
import { PerformanceService } from './performance.service';
import { RiskAnalyticsService } from './risk-analytics.service';
import { TimetableSolverService } from './timetable-solver.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule, TimetableModule],
  controllers: [AiController],
  providers: [
    AiContextService,
    AiSignalsService,
    AiClassInsightsService,
    AiServiceClient,
    PerformanceService,
    RiskAnalyticsService,
    TimetableSolverService,
  ],
  exports: [AiServiceClient],
})
export class AiModule {}
