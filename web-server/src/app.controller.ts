import { Controller, Get, Res } from '@nestjs/common';
import { Response } from 'express';
import { AppService } from './app.service';
import { Public } from './common/auth/public.decorator';
import { ControlDbService } from './database/control-db.service';
import { RuntimeConfigRedisStore } from './platform-config/runtime-config-redis.store';

@Controller()
export class AppController {
  constructor(
    private readonly appService: AppService,
    private readonly controlDb: ControlDbService,
    private readonly runtimeRedis: RuntimeConfigRedisStore,
  ) {}

  @Public()
  @Get()
  getHello(): string {
    return this.appService.getHello();
  }

  @Public()
  @Get('health')
  health(@Res() res: Response) {
    const encryptionKey = Boolean(process.env.ENCRYPTION_KEY);
    const jwtSecret = Boolean(process.env.JWT_SECRET || process.env.ACCESS_TOKEN_SECRET);
    const controlDb = Boolean(process.env.CONTROL_DB_URL);
    const ready = encryptionKey && jwtSecret && controlDb;

    return res.status(ready ? 200 : 503).json({
      status: ready ? 'ok' : 'misconfigured',
      timestamp: new Date().toISOString(),
      service: 'web-server',
      config: {
        encryptionKey,
        jwtSecret,
        controlDb,
        corsOrigins: Boolean(process.env.CORS_ORIGINS || process.env.CORS_ORGINS),
        pgSsl: process.env.PG_SSL === 'true',
        redis: Boolean((process.env.REDIS_URL || '').trim()),
      },
      hint: ready
        ? undefined
        : 'Set ENCRYPTION_KEY, JWT_SECRET, and CONTROL_DB_URL on the school API container, then restart.',
    });
  }

  @Public()
  @Get('health/ready')
  async ready(@Res() res: Response) {
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
    return res.status(ready ? 200 : 503).json({
      status: ready ? 'ready' : 'unavailable',
      timestamp: new Date().toISOString(),
      service: 'web-server',
      checks,
    });
  }
}
