import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { NurseryController } from './nursery.controller';
import { NurseryService } from './nursery.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [NurseryController],
  providers: [NurseryService],
})
export class NurseryModule {}
