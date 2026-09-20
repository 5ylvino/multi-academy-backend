import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { PushController } from './push.controller';
import { PushService } from './push.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [PushController],
  providers: [PushService],
  exports: [PushService],
})
export class PushModule {}
