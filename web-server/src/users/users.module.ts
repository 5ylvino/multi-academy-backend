import { Module } from '@nestjs/common';
import { UsersService } from './users.service';
import { UsersController } from './users.controller';
import { DatabaseModule } from '../database/database.module';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { PlatformConfigModule } from '../platform-config/platform-config.module';
import { PasswordService } from '../common/security/password.service';
import { AuditLogService } from '../common/audit/audit-log.service';
import { AuthModule } from '../auth/auth.module';

@Module({
  imports: [DatabaseModule, ControlPlaneModule, PlatformConfigModule, AuthModule],
  controllers: [UsersController],
  providers: [UsersService, PasswordService, AuditLogService],
})
export class UsersModule {}

