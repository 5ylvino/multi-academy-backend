import { Controller, Get, Res } from '@nestjs/common';
import { Response } from 'express';
import { Public } from '../common/auth/public.decorator';
import { ControlDbService } from '../database/control-db.service';
import { RuntimeConfigRedisStore } from '../platform-config/runtime-config-redis.store';

@Controller()
export class EdgeHealthController {
  constructor(
    private readonly controlDb: ControlDbService,
    private readonly runtimeRedis: RuntimeConfigRedisStore,
  ) {}

  @Get('health')
  @Public()
  health() {
    return {
      ok: true,
      mode: 'edge',
      ts: new Date().toISOString(),
    };
  }

  @Get('health/ready')
  @Public()
  async ready(@Res() response: Response) {
    const checks: Record<string, 'ok' | 'down' | 'skipped'> = {
      controlDb: 'down',
      redis: 'skipped',
    };
    try {
      checks.controlDb = (await this.controlDb.ping()) ? 'ok' : 'down';
    } catch {
      checks.controlDb = 'down';
    }
    if (this.runtimeRedis.isEnabled()) {
      try {
        checks.redis = (await this.runtimeRedis.ping()) ? 'ok' : 'down';
      } catch {
        checks.redis = 'down';
      }
    }
    const ready = checks.controlDb === 'ok' && checks.redis !== 'down';
    return response.status(ready ? 200 : 503).json({
      ok: ready,
      mode: 'edge',
      ts: new Date().toISOString(),
      checks,
    });
  }

  @Get('edge/routes')
  @Public()
  routes() {
    return {
      mode: 'edge',
      hotPaths: [
        'POST /api/v1/auth/login',
        'POST /api/v1/auth/refresh',
        'GET /api/v1/config/bootstrap',
        'GET /api/v1/config/features',
        'GET /api/v1/auth/me',
        'GET /api/v1/users/me',
        'GET /api/v1/health',
        'GET /api/v1/health/ready',
      ],
      proxies: ['payments', 'tutoring', 'ai'],
    };
  }
}
