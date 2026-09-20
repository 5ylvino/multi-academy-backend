import { Module } from '@nestjs/common';
import { AcademicController } from './academic.controller';
import { AcademicService } from './academic.service';
import { DatabaseModule } from '../database/database.module';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { GradingMatrixService } from './grading-matrix.service';
import { ReportCardsService } from './report-cards.service';
import { TermRankingService } from './term-ranking.service';
import { NotificationsModule } from '../notifications/notifications.module';

@Module({
  imports: [DatabaseModule, ControlPlaneModule, NotificationsModule],
  controllers: [AcademicController],
  providers: [AcademicService, GradingMatrixService, ReportCardsService, TermRankingService],
  exports: [AcademicService, GradingMatrixService, ReportCardsService],
})
export class AcademicModule {}

