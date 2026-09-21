import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import helmet from 'helmet';
import express = require('express');
// compression is CJS (`module.exports = fn`). Without esModuleInterop, a default
// import compiles to `.default` and blows up at boot — use require instead.
import compression = require('compression');
import { AppModule } from './app.module';

function resolveCorsOrigins(): string[] {
  // Accept both spellings — older env files used CORS_ORGINS (typo).
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
  // rawBody required for Paystack webhook HMAC verification
  const app = await NestFactory.create(AppModule, { bodyParser: false });
  // Payment proofs arrive as base64 JSON. Preserve raw webhook bytes while
  // allowing the larger base64-encoded request through.
  app.use(
    express.json({
      limit: '50mb',
      verify: (req: express.Request & { rawBody?: Buffer }, _res, buffer) => {
        req.rawBody = buffer;
      },
    }),
  );
  app.use(express.urlencoded({ extended: true, limit: '50mb' }));
  app.setGlobalPrefix('api/v1');

  app.use(
    helmet({
      crossOriginResourcePolicy: { policy: 'cross-origin' },
    }),
  );

  // List and report payloads are large, repetitive JSON and compress by ~80%,
  // which dominates transfer time for clients on school-grade connections.
  app.use(
    compression({
      threshold: 1024,
      filter: (req, res) => {
        // Allow a caller to opt out (e.g. when debugging with a proxy).
        if (req.headers['x-no-compression']) return false;
        return compression.filter(req, res);
      },
    }),
  );

  // Release tenant connection pools and stop workers on SIGTERM instead of
  // dropping in-flight requests. Without this, OnModuleDestroy never runs.
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
  const port = process.env.PORT || 8001;

  app.enableCors({
    origin: (requestOrigin, callback) => {
      if (!requestOrigin) return callback(null, true);
      if (allowedOrigins.includes(requestOrigin)) {
        return callback(null, true);
      }
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
    exposedHeaders: ['Content-Length', 'Content-Range', 'Authorization'],
    preflightContinue: false,
    optionsSuccessStatus: 204,
  });

  await app.listen(port);
  // Helpful in hosted process logs when diagnosing startup crashes.
  // eslint-disable-next-line no-console
  console.log(
    `[ma-sms] listening on ${port}; cors=${allowedOrigins.join('|')}; hasEncryptionKey=${Boolean(
      process.env.ENCRYPTION_KEY,
    )}; hasJwtSecret=${Boolean(process.env.JWT_SECRET || process.env.ACCESS_TOKEN_SECRET)}; hasControlDb=${Boolean(
      process.env.CONTROL_DB_URL,
    )}`,
  );
}
bootstrap().catch((err) => {
  // eslint-disable-next-line no-console
  console.error('[ma-sms] bootstrap failed:', err);
  process.exit(1);
});
