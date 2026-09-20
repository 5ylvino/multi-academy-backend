import { Module } from '@nestjs/common';
import { PaymentsModule } from '../payments/payments.module';
import { TutoringController } from './tutoring.controller';
import { TutoringServiceClient } from './tutoring-service.client';

@Module({
  imports: [PaymentsModule],
  controllers: [TutoringController],
  providers: [TutoringServiceClient],
  exports: [TutoringServiceClient],
})
export class TutoringModule {}
