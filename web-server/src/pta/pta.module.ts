import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { DatabaseModule } from '../database/database.module';
import { PtaController } from './pta.controller';
import { PtaService } from './pta.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [PtaController],
  providers: [PtaService],
})
export class PtaModule {}
