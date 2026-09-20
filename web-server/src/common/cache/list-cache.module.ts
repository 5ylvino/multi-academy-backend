import { Global, Module } from '@nestjs/common';
import { ListCacheService } from './list-cache.service';

@Global()
@Module({
  providers: [ListCacheService],
  exports: [ListCacheService],
})
export class ListCacheModule {}
