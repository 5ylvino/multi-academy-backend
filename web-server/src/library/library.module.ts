import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { LibraryController } from './library.controller';
import { LibraryService } from './library.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [LibraryController],
  providers: [LibraryService],
})
export class LibraryModule {}
