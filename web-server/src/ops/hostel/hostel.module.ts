import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../../control-plane/control-plane.module';
import { DatabaseModule } from '../../database/database.module';
import { HostelController } from './hostel.controller';
import { HostelService } from './hostel.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [HostelController],
  providers: [HostelService],
})
export class HostelModule {}
