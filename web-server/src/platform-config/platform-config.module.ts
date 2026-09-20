import { Global, Module } from '@nestjs/common';
import { DatabaseModule } from '../database/database.module';
import { AuditLogService } from '../common/audit/audit-log.service';
import { ControlApiClient } from './control-api.client';
import { RuntimeConfigService } from './runtime-config.service';
import { RuntimeConfigRedisStore } from './runtime-config-redis.store';
import { BootstrapService } from './bootstrap.service';
import { FeatureFlagService } from './feature-flag.service';
import { EnforcementService } from './enforcement.service';
import { FeatureFlagGuard } from './feature-flag.guard';
import { EnforcementGuard } from './enforcement.guard';
import { ProviderRegistryService } from './providers/provider-registry.service';
import { ProviderSecretsService } from './provider-secrets.service';
import { ConfigController } from './config.controller';
import { ControlWebhookController } from './control-webhook.controller';
import { PaystackGateway } from './providers/paystack.gateway';
import { AfricasTalkingGateway } from './providers/africas-talking.gateway';
import { ResendEmailGateway } from './providers/resend.email.gateway';
import { CpanelEmailGateway } from './providers/cpanel.email.gateway';
import { FlutterwaveGateway } from './providers/flutterwave.gateway';
import { GoogleMeetGateway } from './providers/google-meet.gateway';
import { ZoomGateway } from './providers/zoom.gateway';
import { StubAiGateway } from './providers/stub-ai.gateway';
import { OpenAiGateway } from './providers/openai.gateway';
import { GeminiGateway } from './providers/gemini.gateway';
import { KiloGateway } from './providers/kilo.gateway';
import { OpayGateway } from './providers/opay.gateway';
import { PalmPayGateway } from './providers/palmpay.gateway';
import { MomoGateway } from './providers/momo.gateway';
import { StubPushGateway } from './providers/push.gateway';
import { CommsService } from './comms.service';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { OrganizationsModule } from '../organizations/organizations.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { PushModule } from '../push/push.module';

@Global()
@Module({
  imports: [
    DatabaseModule,
    ControlPlaneModule,
    OrganizationsModule,
    NotificationsModule,
    PushModule,
  ],
  controllers: [ConfigController, ControlWebhookController],
  providers: [
    AuditLogService,
    ControlApiClient,
    RuntimeConfigRedisStore,
    RuntimeConfigService,
    BootstrapService,
    FeatureFlagService,
    EnforcementService,
    FeatureFlagGuard,
    EnforcementGuard,
    ProviderSecretsService,
    PaystackGateway,
    FlutterwaveGateway,
    AfricasTalkingGateway,
    ResendEmailGateway,
    CpanelEmailGateway,
    GoogleMeetGateway,
    ZoomGateway,
    StubAiGateway,
    OpenAiGateway,
    GeminiGateway,
    KiloGateway,
    OpayGateway,
    PalmPayGateway,
    MomoGateway,
    StubPushGateway,
    ProviderRegistryService,
    CommsService,
  ],
  exports: [
    ControlApiClient,
    RuntimeConfigService,
    BootstrapService,
    FeatureFlagService,
    EnforcementService,
    FeatureFlagGuard,
    EnforcementGuard,
    ProviderRegistryService,
    ProviderSecretsService,
    CommsService,
    RuntimeConfigRedisStore,
  ],
})
export class PlatformConfigModule {}
