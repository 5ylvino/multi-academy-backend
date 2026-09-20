import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import helmet from 'helmet';
import express = require('express');
import compression = require('compression');
import { EdgeModule } from './edge/edge.module';

function resolveCorsOrigins(): string[] {
  const raw =
    process.env.CORS_ORIGINS ||
    process.env.CORS_ORGINS ||
    'http://localhost:3000,http://localhost:3100';
  return raw
    .split(',')
    .map((s) => s.trim())
    .map((s) => s.replace(/\/$/, ''))
    .filter((s) => s.length > 0);
}

async function bootstrap() {
  const app = await NestFactory.create(EdgeModule, { bodyParser: false });
  app.use(
    express.json({
      limit: '10mb',
      verify: (req: express.Request & { rawBody?: Buffer }, _res, buffer) => {
        req.rawBody = buffer;
      },
    }),
  );
  app.use(express.urlencoded({ extended: true, limit: '10mb' }));
  app.setGlobalPrefix('api/v1');
  app.use(
    helmet({
      crossOriginResourcePolicy: { policy: 'cross-origin' },
    }),
  );
  app.use(compression({ threshold: 1024 }));
  app.enableShutdownHooks();
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
      transformOptions: { enableImplicitConversion: true },
    }),
  );

  const allowedOrigins = resolveCorsOrigins();
  const port = process.env.EDGE_PORT || process.env.PORT || 8001;

  app.enableCors({
    origin: (requestOrigin, callback) => {
      if (!requestOrigin) return callback(null, true);
      if (allowedOrigins.includes(requestOrigin)) return callback(null, true);
      return callback(new Error('CORS origin not allowed'), false);
    },
    credentials: true,
    methods: ['GET', 'HEAD', 'PUT', 'PATCH', 'POST', 'DELETE', 'OPTIONS'],
    allowedHeaders: [
      'Content-Type',
      'Authorization',
      'X-Requested-With',
      'Accept',
      'Origin',
      'X-Tenant-Id',
    ],
    exposedHeaders: ['Content-Length', 'Authorization'],
    preflightContinue: false,
    optionsSuccessStatus: 204,
  });

  await app.listen(port);
  // eslint-disable-next-line no-console
  console.log(`[ma-sms-edge] listening on ${port}; cors=${allowedOrigins.join('|')}`);
}

bootstrap().catch((err) => {
  // eslint-disable-next-line no-console
  console.error('[ma-sms-edge] bootstrap failed:', err);
  process.exit(1);
});
