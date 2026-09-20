import { Global, Module } from '@nestjs/common';
import { WorkerServiceClient } from './worker-service.client';

@Global()
@Module({
  providers: [WorkerServiceClient],
  exports: [WorkerServiceClient],
})
export class WorkerModule {}
