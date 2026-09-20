import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { CbtController } from './cbt.controller';
import { CbtService } from './cbt.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [CbtController],
  providers: [CbtService],
})
export class CbtModule {}
