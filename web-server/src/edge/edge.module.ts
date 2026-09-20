import { Module } from '@nestjs/common';
import { APP_FILTER, APP_GUARD, APP_INTERCEPTOR } from '@nestjs/core';
import { ConfigModule } from '@nestjs/config';
import { ThrottlerGuard, ThrottlerModule } from '@nestjs/throttler';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { PlatformConfigModule } from '../platform-config/platform-config.module';
import { IdentificationModule } from '../identification/identification.module';
import { AuthModule } from '../auth/auth.module';
import { DatabaseModule } from '../database/database.module';
import { OrganizationsModule } from '../organizations/organizations.module';
import { UsersModule } from '../users/users.module';
import { ListCacheModule } from '../common/cache/list-cache.module';
import { PaymentsModule } from '../payments/payments.module';
import { TutoringModule } from '../tutoring/tutoring.module';
import { AiModule } from '../ai/ai.module';
import { WorkerModule } from '../worker/worker.module';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { PermissionsGuard } from '../common/auth/permissions.guard';
import { EnforcementGuard } from '../platform-config/enforcement.guard';
import { FeatureFlagGuard } from '../platform-config/feature-flag.guard';
import { ApiExceptionFilter } from '../common/filters/api-exception.filter';
import { SlowRequestInterceptor } from '../common/observability/slow-request.interceptor';
import { EdgeHealthController } from './edge-health.controller';

/**
 * Thin School Edge API — auth, bootstrap/config, health, and microservice handoffs only.
 * Run via `npm run start:edge` (see main-edge.ts). Domain modules stay on the full server.
 */
@Module({
  imports: [
    ConfigModule.forRoot({
      envFilePath: ['.env', '.env.production', '.env.development'],
      isGlobal: true,
    }),
    ThrottlerModule.forRoot([{ ttl: 60_000, limit: 120 }]),
    DatabaseModule,
    PlatformConfigModule,
    ControlPlaneModule,
    IdentificationModule,
    AuthModule,
    OrganizationsModule,
    UsersModule,
    ListCacheModule,
    PaymentsModule,
    TutoringModule,
    AiModule,
    WorkerModule,
  ],
  controllers: [EdgeHealthController],
  providers: [
    { provide: APP_GUARD, useClass: ThrottlerGuard },
    { provide: APP_GUARD, useClass: JwtAuthGuard },
    { provide: APP_GUARD, useClass: EnforcementGuard },
    { provide: APP_GUARD, useClass: PermissionsGuard },
    { provide: APP_GUARD, useClass: FeatureFlagGuard },
    { provide: APP_FILTER, useClass: ApiExceptionFilter },
    { provide: APP_INTERCEPTOR, useClass: SlowRequestInterceptor },
  ],
})
export class EdgeModule {}
