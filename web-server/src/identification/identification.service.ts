import { Injectable, Logger, OnModuleDestroy, OnModuleInit, UnauthorizedException } from '@nestjs/common';
import { createHmac, timingSafeEqual } from 'crypto';
import { randomToken } from '../common/utils/id.util';

type RedisClient = {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, mode: string, ttlSeconds: number): Promise<unknown>;
  quit(): Promise<string>;
};

type AccessPayload = {
  sub: string;
  user_id: string;
  tenant_id: string;
  email: string;
  user_details: {
    email: string;
    roles: string[];
    permissions: string[];
    capabilities: string[];
  };
  roles: string[];
  permissions: string[];
  capabilities: string[];
  config_version?: number;
  tenant_status?: string;
  features_digest?: string;
  iat: number;
  exp: number;
  jti: string;
  iss: string;
  aud: string;
};

type RefreshPayload = {
  iss: string;
  aud: string;
  sub: string;
  tenant_id: string;
  session_id: string;
  jti: string;
  iat: number;
  exp: number;
};

@Injectable()
export class IdentificationService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(IdentificationService.name);
  private revokedRefreshTokenJtis = new Map<string, number>();
  private redis: RedisClient | null = null;
  private readonly accessTokenTtlSeconds =
    Number(process.env.ACCESS_TOKEN_TTL_SECONDS) || 60 * 30;
  private readonly refreshTokenTtlSeconds =
    Number(process.env.REFRESH_TOKEN_TTL_SECONDS) || 60 * 60 * 24 * 7;
  private jwtSecret =
    process.env.JWT_SECRET || process.env.ACCESS_TOKEN_SECRET || 'dev-local-jwt-secret-change-me';
  private readonly insecureJwtSecrets = new Set([
    'dev-local-jwt-secret-change-me',
    'dev-school-jwt-secret-change-me-32b',
    'change-me',
    'change-me-in-production',
  ]);

  async onModuleInit(): Promise<void> {
    await this.connectRevocationStore();
    const isProduction = process.env.NODE_ENV === 'production';
    const usingDefaultSecret =
      !process.env.JWT_SECRET &&
      !process.env.ACCESS_TOKEN_SECRET;
    const usingInsecureSecret = this.insecureJwtSecrets.has(this.jwtSecret);
    if (isProduction && usingDefaultSecret) {
      // eslint-disable-next-line no-console
      console.error(
        '[ma-sms] JWT_SECRET (or ACCESS_TOKEN_SECRET) must be set in production. Auth will fail until configured.',
      );
    }
    if (isProduction && usingInsecureSecret) {
      this.logger.error('JWT_SECRET is a development/example value in production');
    }
  }

  async onModuleDestroy(): Promise<void> {
    if (!this.redis) return;
    try {
      await this.redis.quit();
    } catch {
      /* ignore */
    }
    this.redis = null;
  }

  private async connectRevocationStore(): Promise<void> {
    const url = (process.env.REDIS_URL || '').trim();
    if (!url) return;
    try {
      const mod = await import('ioredis');
      const Redis = mod.default as new (
        url: string,
        opts: { maxRetriesPerRequest: number; connectTimeout: number; lazyConnect: boolean },
      ) => RedisClient & { connect(): Promise<void> };
      const client = new Redis(url, {
        maxRetriesPerRequest: 1,
        connectTimeout: 2_000,
        lazyConnect: true,
      });
      await client.connect();
      this.redis = client;
      this.logger.log('Refresh-token revocation store enabled on Redis');
    } catch (err) {
      this.logger.warn(
        `Refresh revocation Redis unavailable — using in-memory store only: ${
          err instanceof Error ? err.message : err
        }`,
      );
      this.redis = null;
    }
  }

  private revocationKey(jti: string): string {
    return `mas:revoked-refresh:${jti}`;
  }

  private assertJwtSecret(): void {
    const isProduction = process.env.NODE_ENV === 'production';
    const usingDefaultSecret =
      !process.env.JWT_SECRET &&
      !process.env.ACCESS_TOKEN_SECRET;
    if (isProduction && (usingDefaultSecret || this.insecureJwtSecrets.has(this.jwtSecret))) {
      throw new UnauthorizedException(
        'Server misconfigured: JWT_SECRET must be a unique production secret.',
      );
    }
  }

  private base64urlEncode(input: string): string {
    return Buffer.from(input, 'utf8').toString('base64url');
  }

  private base64urlDecode(input: string): string {
    return Buffer.from(input, 'base64url').toString('utf8');
  }

  private assertTokenClaims(payload: { iss?: unknown; aud?: unknown }): void {
    const issuer = process.env.JWT_ISSUER || 'mas-school-server';
    const audience = process.env.JWT_AUDIENCE || 'mas-school-api';
    if (payload.iss !== issuer || payload.aud !== audience) {
      throw new UnauthorizedException('Invalid token issuer or audience');
    }
  }

  private signToken(payload: AccessPayload | RefreshPayload, tokenType: 'access' | 'refresh'): string {
    const header = {
      alg: 'HS256',
      typ: 'JWT',
      tkn: tokenType,
    };
    const encodedHeader = this.base64urlEncode(JSON.stringify(header));
    const encodedPayload = this.base64urlEncode(JSON.stringify(payload));
    const signingInput = `${encodedHeader}.${encodedPayload}`;
    const signature = createHmac('sha256', this.jwtSecret).update(signingInput).digest('base64url');
    return `${signingInput}.${signature}`;
  }

  private verifySignature(token: string): { header: Record<string, unknown>; payload: Record<string, unknown> } {
    const parts = token.split('.');
    if (parts.length !== 3) {
      throw new UnauthorizedException('Invalid token format');
    }
    const [encodedHeader, encodedPayload, encodedSignature] = parts;
    const signingInput = `${encodedHeader}.${encodedPayload}`;
    const expectedSignature = createHmac('sha256', this.jwtSecret).update(signingInput).digest('base64url');
    const actual = Uint8Array.from(Buffer.from(encodedSignature));
    const expected = Uint8Array.from(Buffer.from(expectedSignature));
    if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
      throw new UnauthorizedException('Invalid token signature');
    }

    try {
      const header = JSON.parse(this.base64urlDecode(encodedHeader));
      const payload = JSON.parse(this.base64urlDecode(encodedPayload));
      return { header, payload };
    } catch {
      throw new UnauthorizedException('Invalid token payload');
    }
  }

  issueSession(input: {
    sub: string;
    tenantId: string;
    email: string;
    roles: string[];
    permissions: string[];
    capabilities: string[];
    /** Control-plane config version at issue time — guard fast path when cache matches. */
    configVersion?: number;
    tenantStatus?: string;
    featuresDigest?: string;
    /** Override refresh TTL (seconds). Used for remember-me sessions. */
    refreshTtlSeconds?: number;
  }) {
    this.assertJwtSecret();
    const nowSeconds = Math.floor(Date.now() / 1000);
    const refreshTtl =
      typeof input.refreshTtlSeconds === 'number' && input.refreshTtlSeconds > 0
        ? input.refreshTtlSeconds
        : this.refreshTokenTtlSeconds;

    const accessPayload: AccessPayload = {
      sub: input.sub,
      user_id: input.sub,
      tenant_id: input.tenantId,
      email: input.email,
      user_details: {
        email: input.email,
        roles: input.roles,
        permissions: input.permissions,
        capabilities: input.capabilities,
      },
      roles: input.roles,
      permissions: input.permissions,
      capabilities: input.capabilities,
      config_version:
        typeof input.configVersion === 'number' && input.configVersion > 0
          ? input.configVersion
          : undefined,
      tenant_status: input.tenantStatus || undefined,
      features_digest: input.featuresDigest || undefined,
      iat: nowSeconds,
      exp: nowSeconds + this.accessTokenTtlSeconds,
      jti: randomToken('jti'),
      iss: process.env.JWT_ISSUER || 'mas-school-server',
      aud: process.env.JWT_AUDIENCE || 'mas-school-api',
    };

    const refreshPayload: RefreshPayload = {
      iss: process.env.JWT_ISSUER || 'mas-school-server',
      aud: process.env.JWT_AUDIENCE || 'mas-school-api',
      sub: input.sub,
      tenant_id: input.tenantId,
      session_id: randomToken('sid'),
      jti: randomToken('rjti'),
      iat: nowSeconds,
      exp: nowSeconds + refreshTtl,
    };

    const accessToken = this.signToken(accessPayload, 'access');
    const refreshToken = this.signToken(refreshPayload, 'refresh');

    return { accessToken, refreshToken, payload: accessPayload };
  }

  verifyAccessToken(token?: string): AccessPayload {
    this.assertJwtSecret();
    if (!token) {
      throw new UnauthorizedException('Missing access token');
    }
    const { header, payload } = this.verifySignature(token);
    if (header.tkn !== 'access') {
      throw new UnauthorizedException('Invalid access token type');
    }
    const accessPayload = payload as AccessPayload;
    this.assertTokenClaims(accessPayload);
    const now = Math.floor(Date.now() / 1000);
    if (accessPayload.exp <= now) {
      throw new UnauthorizedException('Access token expired');
    }
    if (!accessPayload.tenant_id || !accessPayload.user_id || !accessPayload.user_details) {
      throw new UnauthorizedException('Missing zero-trust required claims in access token');
    }
    return accessPayload;
  }

  verifyRefreshToken(refreshToken?: string): RefreshPayload {
    this.assertJwtSecret();
    if (!refreshToken) {
      throw new UnauthorizedException('Missing refresh token');
    }
    const { header, payload } = this.verifySignature(refreshToken);
    if (header.tkn !== 'refresh') {
      throw new UnauthorizedException('Invalid refresh token type');
    }
    const refreshPayload = payload as RefreshPayload;
    this.assertTokenClaims(refreshPayload);
    const now = Math.floor(Date.now() / 1000);
    if (refreshPayload.exp <= now) {
      throw new UnauthorizedException('Refresh token expired');
    }
    this.cleanupRevokedRefreshTokens(now);
    if (this.revokedRefreshTokenJtis.has(refreshPayload.jti)) {
      throw new UnauthorizedException('Refresh token revoked');
    }
    return refreshPayload;
  }

  async isRefreshRevoked(jti: string): Promise<boolean> {
    if (!jti) return false;
    if (this.revokedRefreshTokenJtis.has(jti)) return true;
    if (!this.redis) return false;
    try {
      const hit = await this.redis.get(this.revocationKey(jti));
      if (!hit) return false;
      const exp = Number(hit) || 0;
      this.revokedRefreshTokenJtis.set(jti, exp);
      return true;
    } catch (err) {
      this.logger.warn(
        `Refresh revocation Redis read failed: ${err instanceof Error ? err.message : err}`,
      );
      return false;
    }
  }

  async revokeRefreshToken(refreshToken: string) {
    const { payload } = this.verifySignature(refreshToken);
    const refreshPayload = payload as RefreshPayload;
    if (!refreshPayload?.jti || typeof refreshPayload.exp !== 'number') return;
    this.revokedRefreshTokenJtis.set(refreshPayload.jti, refreshPayload.exp);
    if (!this.redis) return;
    const ttl = Math.max(1, refreshPayload.exp - Math.floor(Date.now() / 1000));
    try {
      await this.redis.set(this.revocationKey(refreshPayload.jti), String(refreshPayload.exp), 'EX', ttl);
    } catch (err) {
      this.logger.warn(
        `Refresh revocation Redis write failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  private cleanupRevokedRefreshTokens(nowSeconds: number) {
    for (const [jti, exp] of this.revokedRefreshTokenJtis.entries()) {
      if (exp <= nowSeconds) {
        this.revokedRefreshTokenJtis.delete(jti);
      }
    }
  }
}

