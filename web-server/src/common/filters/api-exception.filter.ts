import { ArgumentsHost, Catch, ExceptionFilter, HttpException, HttpStatus, Logger } from '@nestjs/common';
import { Response, Request } from 'express';
import { fail } from '../types/api-response';

@Catch()
export class ApiExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger(ApiExceptionFilter.name);

  catch(exception: unknown, host: ArgumentsHost) {
    const ctx = host.switchToHttp();
    const response = ctx.getResponse<Response>();
    const request = ctx.getRequest<Request>();
    const hideInternals = process.env.NODE_ENV === 'production';

    if (exception instanceof HttpException) {
      const status = exception.getStatus();
      const res: any = exception.getResponse();
      const message = typeof res === 'string' ? res : res?.message || exception.message || 'An error occurred';
      return response.status(status).json(
        fail(Array.isArray(message) ? message.join('; ') : message, {
          path: request.url,
          error: res?.error || exception.name,
          statusCode: status,
        }),
      );
    }

    this.logger.error(
      `Unhandled ${request.method} ${request.url}: ${
        exception instanceof Error ? exception.stack || exception.message : exception
      }`,
    );

    const status = HttpStatus.INTERNAL_SERVER_ERROR;
    const rawMessage = (exception as any)?.message || 'Internal server error';
    return response.status(status).json(
      fail(hideInternals ? 'Internal server error' : rawMessage, {
        path: request.url,
        error: hideInternals ? 'InternalServerError' : (exception as any)?.name || 'Error',
        statusCode: status,
      }),
    );
  }
}
