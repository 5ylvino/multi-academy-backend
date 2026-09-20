import { Module } from '@nestjs/common';
import { FinancialController } from './financial.controller';
import { FinancialService } from './financial.service';
import { GatewayPaymentsController } from './gateway-payments.controller';
import { GatewayPaymentsService } from './gateway-payments.service';
import { BalanceFreezeService } from './balance-freeze.service';
import { WebhooksController } from './webhooks.controller';
import { DatabaseModule } from '../database/database.module';
import { ControlPlaneModule } from '../control-plane/control-plane.module';
import { BiometricModule } from '../biometric/biometric.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { RealtimeModule } from '../realtime/realtime.module';
import { PaymentsModule } from '../payments/payments.module';
import { FeesOpsModule } from '../fees-ops/fees-ops.module';
import { ListCacheModule } from '../common/cache/list-cache.module';

@Module({
  imports: [
    DatabaseModule,
    ControlPlaneModule,
    BiometricModule,
    NotificationsModule,
    RealtimeModule,
    PaymentsModule,
    FeesOpsModule,
    ListCacheModule,
  ],
  controllers: [FinancialController, GatewayPaymentsController, WebhooksController],
  providers: [FinancialService, GatewayPaymentsService, BalanceFreezeService],
  exports: [FinancialService, GatewayPaymentsService, BalanceFreezeService],
})
export class FinancialModule {}
