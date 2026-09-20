import { Module } from '@nestjs/common';
import { ControlPlaneService } from './control-plane.service';
import { TenantProvisioningService } from './tenant-provisioning.service';
import { DatabaseModule } from '../database/database.module';
import { TenantConfigCacheService } from './tenant-config-cache.service';
import { UserAuthorityService } from './user-authority.service';
import { PasswordService } from '../common/security/password.service';
import { CryptoService } from '../common/security/crypto.service';

@Module({
  imports: [DatabaseModule],
  providers: [ControlPlaneService, TenantProvisioningService, TenantConfigCacheService, UserAuthorityService, PasswordService, CryptoService],
  exports: [ControlPlaneService, TenantProvisioningService, TenantConfigCacheService, UserAuthorityService, PasswordService, CryptoService],
})
export class ControlPlaneModule {}

