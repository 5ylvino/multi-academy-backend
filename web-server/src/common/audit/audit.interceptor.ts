import { CallHandler, ExecutionContext, Injectable, Logger, NestInterceptor } from '@nestjs/common';
import { Observable, tap } from 'rxjs';
import { Request, Response } from 'express';
import { AuditLogService } from './audit-log.service';

const SKIP_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
const SKIP_PATH_PREFIXES = [
  '/api/v1/health',
  '/api/v1/config/',
  '/health',
  '/api/v1/internal/control/',
];

/**
 * Persist mutating requests without blocking the HTTP response.
 * Read/hot paths are skipped — they dominate traffic and are not audit-worthy.
 */
@Injectable()
export class AuditInterceptor implements NestInterceptor {
  private readonly logger = new Logger(AuditInterceptor.name);

  constructor(private readonly audit: AuditLogService) {}

  intercept(context: ExecutionContext, next: CallHandler): Observable<any> {
    const ctx = context.switchToHttp();
    const req = ctx.getRequest<Request>();
    const res = ctx.getResponse<Response>();

    if (this.shouldSkip(req)) {
      return next.handle();
    }

    return next.handle().pipe(
      tap({
        next: (value) => {
          this.enqueue(req, res.statusCode, value);
        },
        error: (err) => {
          const resp =
            err && typeof err.getResponse === 'function'
              ? err.getResponse()
              : { message: err?.message || 'Error' };
          this.enqueue(req, res.statusCode || 500, resp);
        },
      }),
    );
  }

  private shouldSkip(req: Request): boolean {
    const method = (req.method || 'GET').toUpperCase();
    if (SKIP_METHODS.has(method)) return true;
    const path = req.originalUrl || req.url || '';
    return SKIP_PATH_PREFIXES.some((prefix) => path.startsWith(prefix));
  }

  private enqueue(req: Request, statusCode: number, response: unknown): void {
    const user = (req as any).user || {};
    void this.audit
      .log({
        userId: user?.sub || null,
        tenantId: user?.tenant_id || null,
        method: req.method,
        path: req.url,
        statusCode,
        ip: (req.headers['x-forwarded-for'] as string) || req.ip,
        userAgent: req.headers['user-agent'] as string,
        payload: req.body,
        response,
      })
      .catch((err) => {
        this.logger.warn(
          `Audit write failed for ${req.method} ${req.url}: ${
            err instanceof Error ? err.message : err
          }`,
        );
      });
  }
}
