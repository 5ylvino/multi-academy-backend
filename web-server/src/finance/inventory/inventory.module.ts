import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../../control-plane/control-plane.module';
import { DatabaseModule } from '../../database/database.module';
import { InventoryController } from './inventory.controller';
import { InventoryService } from './inventory.service';

@Module({
  imports: [DatabaseModule, ControlPlaneModule],
  controllers: [InventoryController],
  providers: [InventoryService],
})
export class InventoryModule {}
