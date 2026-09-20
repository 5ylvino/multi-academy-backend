import type { Request } from 'express';

function parseOriginHost(origin?: string | null): string | null {
  if (!origin || typeof origin !== 'string') return null;
  try {
    return new URL(origin).hostname || null;
  } catch {
    return null;
  }
}

/** Resolve WebAuthn RP ID and allowed origins for the current request. */
export function resolveWebAuthnConfig(req?: Request) {
  const headerOrigin = req?.headers?.origin;
  const refererOrigin = (() => {
    const referer = req?.headers?.referer;
    if (!referer || typeof referer !== 'string') return null;
    try {
      return new URL(referer).origin;
    } catch {
      return null;
    }
  })();

  const requestOrigin = headerOrigin || refererOrigin || null;
  const requestHost = parseOriginHost(requestOrigin);

  const envOrigins =
    process.env.WEBAUTHN_ORIGIN ||
    process.env.CORS_ORIGINS ||
    process.env.CORS_ORGINS ||
    'http://localhost:3000';

  const origins = envOrigins
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  if (requestOrigin && !origins.includes(requestOrigin)) {
    origins.unshift(requestOrigin);
  }

  const envRpId = process.env.WEBAUTHN_RP_ID || 'localhost';
  const rpID =
    requestHost && (requestHost === 'localhost' || envRpId === 'localhost' || requestHost.includes('.'))
      ? requestHost
      : envRpId;

  return {
    rpID,
    rpName: process.env.WEBAUTHN_RP_NAME || 'MA-SMS',
    origins,
  };
}
