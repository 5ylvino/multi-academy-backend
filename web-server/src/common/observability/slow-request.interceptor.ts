import {
  CallHandler,
  ExecutionContext,
  Injectable,
  Logger,
  NestInterceptor,
} from '@nestjs/common';
import { Observable, tap } from 'rxjs';
import { Request } from 'express';
import { AuthUserClaims } from '../auth/auth-user.interface';

function intFromEnv(name: string, fallback: number): number {
  const parsed = Number(process.env[name]);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

function resolveTenantId(req: Request): string {
  const headerTenant = String(req.headers['x-tenant-id'] || '').trim();
  if (headerTenant) return headerTenant;
  const user = (req as Request & { user?: AuthUserClaims }).user;
  return String(user?.tenant_id || '').trim();
}

@Injectable()
export class SlowRequestInterceptor implements NestInterceptor {
  private readonly logger = new Logger('SlowRequest');
  private readonly thresholdMs = intFromEnv('SLOW_REQUEST_MS', 500);

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    const started = Date.now();
    const req = context.switchToHttp().getRequest<Request>();
    const path = req.originalUrl || req.url;
    const method = req.method;
    return next.handle().pipe(
      tap({
        next: () => {
          const elapsed = Date.now() - started;
          if (elapsed >= this.thresholdMs) {
            this.logger.warn(
              `${method} ${path} ${elapsed}ms tenant=${resolveTenantId(req)}`,
            );
          }
        },
        error: () => {
          const elapsed = Date.now() - started;
          if (elapsed >= this.thresholdMs) {
            this.logger.warn(
              `${method} ${path} ${elapsed}ms (error) tenant=${resolveTenantId(req)}`,
            );
          }
        },
      }),
    );
  }
}
