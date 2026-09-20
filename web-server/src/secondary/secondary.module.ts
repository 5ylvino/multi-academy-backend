import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { SecondaryController } from './secondary.controller';
import { SecondaryService } from './secondary.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [SecondaryController],
  providers: [SecondaryService],
})
export class SecondaryModule {}
