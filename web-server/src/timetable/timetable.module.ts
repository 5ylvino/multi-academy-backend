import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { TimetableController } from './timetable.controller';
import { TimetableService } from './timetable.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [TimetableController],
  providers: [TimetableService],
  exports: [TimetableService],
})
export class TimetableModule {}
