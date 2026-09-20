import { Module } from '@nestjs/common';
import { DatabaseModule } from '../database/database.module';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { LessonNotesController } from './lesson-notes.controller';
import { LessonNotesService } from './lesson-notes.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [LessonNotesController],
  providers: [LessonNotesService],
})
export class LessonNotesModule {}
