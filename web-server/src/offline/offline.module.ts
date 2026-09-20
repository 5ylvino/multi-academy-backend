import { Module } from '@nestjs/common';
import { PlatformConfigModule } from '../platform-config/platform-config.module';
import { OfflineSyncController } from './offline-sync.controller';

@Module({
  imports: [PlatformConfigModule],
  controllers: [OfflineSyncController],
})
export class OfflineModule {}
