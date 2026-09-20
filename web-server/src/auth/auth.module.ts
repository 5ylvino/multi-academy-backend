import { Module } from '@nestjs/common';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { IdentificationModule } from '../identification/identification.module';
import { AuthController } from './auth.controller';
import { AuthService } from './auth.service';
import { GoogleOauthService } from './google-oauth.service';

@Module({
  imports: [ControlPlaneModule, IdentificationModule],
  controllers: [AuthController],
  providers: [AuthService, GoogleOauthService],
  exports: [AuthService],
})
export class AuthModule {}

