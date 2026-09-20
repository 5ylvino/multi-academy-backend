import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { ConsentSlipsController } from './consent-slips.controller';
import { ConsentSlipsService } from './consent-slips.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [ConsentSlipsController],
  providers: [ConsentSlipsService],
})
export class ConsentSlipsModule {}
