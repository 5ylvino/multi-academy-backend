import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { PrimaryController } from './primary.controller';
import { PrimaryService } from './primary.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [PrimaryController],
  providers: [PrimaryService],
})
export class PrimaryModule {}
