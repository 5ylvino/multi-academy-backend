import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../../control-plane/control-plane.module';
import { DatabaseModule } from '../../database/database.module';
import { ClinicController } from './clinic.controller';
import { ClinicService } from './clinic.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [ClinicController],
  providers: [ClinicService],
})
export class ClinicModule {}
