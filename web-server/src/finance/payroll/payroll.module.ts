import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../../control-plane/control-plane.module';
import { DatabaseModule } from '../../database/database.module';
import { PayrollController } from './payroll.controller';
import { PayrollService } from './payroll.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [PayrollController],
  providers: [PayrollService],
})
export class PayrollModule {}
