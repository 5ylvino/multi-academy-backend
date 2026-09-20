import { createHmac, timingSafeEqual } from 'crypto';

export type ServiceJwtPayload = {
  iss: string;
  aud: string;
  sub: string;
  tenant_id: string;
  actor_id: string;
  roles: string[];
  features: string[];
  iat: number;
  exp: number;
  jti: string;
};

function b64urlDecode(input: string): string {
  const padded = input.replace(/-/g, '+').replace(/_/g, '/');
  const pad = padded.length % 4 === 0 ? '' : '='.repeat(4 - (padded.length % 4));
  return Buffer.from(padded + pad, 'base64').toString('utf8');
}

function b64urlEncode(input: string): string {
  return Buffer.from(input, 'utf8').toString('base64url');
}

export function signServiceJwt(payload: ServiceJwtPayload, secret: string): string {
  const header = b64urlEncode(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = b64urlEncode(JSON.stringify(payload));
  const input = `${header}.${body}`;
  const sig = createHmac('sha256', secret).update(input).digest('base64url');
  return `${input}.${sig}`;
}

export function verifyServiceJwt(
  token: string,
  secret: string,
  expectedAudience: string,
  expectedIssuer?: string,
): ServiceJwtPayload {
  const parts = token.split('.');
  if (parts.length !== 3) throw new Error('Invalid service token');
  const [headerPart, bodyPart, sigPart] = parts;
  const input = `${headerPart}.${bodyPart}`;
  const expectedSig = createHmac('sha256', secret).update(input).digest('base64url');
  const a = new Uint8Array(Buffer.from(sigPart));
  const b = new Uint8Array(Buffer.from(expectedSig));
  if (a.length !== b.length || !timingSafeEqual(a, b)) {
    throw new Error('Invalid service token signature');
  }
  const payload = JSON.parse(b64urlDecode(bodyPart)) as ServiceJwtPayload;
  const now = Math.floor(Date.now() / 1000);
  if (payload.exp <= now) throw new Error('Service token expired');
  if (payload.aud !== expectedAudience) throw new Error('Service token audience mismatch');
  if (expectedIssuer && payload.iss !== expectedIssuer) {
    throw new Error('Service token issuer mismatch');
  }
  if (!payload.tenant_id || !payload.actor_id) {
    throw new Error('Service token missing tenant_id or actor_id');
  }
  return payload;
}
