import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../../control-plane/control-plane.module';
import { DatabaseModule } from '../../database/database.module';
import { TransportController } from './transport.controller';
import { TransportService } from './transport.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [TransportController],
  providers: [TransportService],
})
export class TransportModule {}
