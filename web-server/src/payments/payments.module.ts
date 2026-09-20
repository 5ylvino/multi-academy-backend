import { Module } from '@nestjs/common';
import { PaymentsController } from './payments.controller';
import { PaymentServiceClient } from './payment-service.client';

@Module({
  controllers: [PaymentsController],
  providers: [PaymentServiceClient],
  exports: [PaymentServiceClient],
})
export class PaymentsModule {}
