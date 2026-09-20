import { UnauthorizedException } from '@nestjs/common';
import { IdentificationService } from './identification.service';

describe('IdentificationService', () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    process.env.JWT_SECRET = 'test-school-jwt-secret-change-me-32b';
    process.env.NODE_ENV = 'test';
    delete process.env.REDIS_URL;
  });

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  function service() {
    return new IdentificationService();
  }

  it('issues and verifies a session pair', () => {
    const id = service();
    const session = id.issueSession({
      sub: 'user-1',
      tenantId: 'tenant-1',
      email: 'a@school.test',
      roles: ['director'],
      permissions: ['*'],
      capabilities: [],
    });
    const access = id.verifyAccessToken(session.accessToken);
    expect(access.user_id).toBe('user-1');
    expect(access.tenant_id).toBe('tenant-1');
    expect(id.verifyRefreshToken(session.refreshToken).sub).toBe('user-1');
  });

  it('rejects a mutated token signature', () => {
    const id = service();
    const session = id.issueSession({
      sub: 'user-1',
      tenantId: 'tenant-1',
      email: 'a@school.test',
      roles: ['director'],
      permissions: ['*'],
      capabilities: [],
    });
    const tampered = `${session.accessToken.slice(0, -2)}aa`;
    expect(() => id.verifyAccessToken(tampered)).toThrow(UnauthorizedException);
  });

  it('uses configured issuer and audience claims', () => {
    process.env.JWT_ISSUER = 'school-issuer';
    process.env.JWT_AUDIENCE = 'school-audience';
    const id = service();
    const session = id.issueSession({
      sub: 'user-1',
      tenantId: 'tenant-1',
      email: 'a@school.test',
      roles: ['director'],
      permissions: ['*'],
      capabilities: [],
    });

    expect(session.payload.iss).toBe('school-issuer');
    expect(session.payload.aud).toBe('school-audience');
  });

  it('rejects an example secret in production', () => {
    process.env.NODE_ENV = 'production';
    process.env.JWT_SECRET = 'dev-school-jwt-secret-change-me-32b';
    const id = service();

    expect(() =>
      id.issueSession({
        sub: 'user-1',
        tenantId: 'tenant-1',
        email: 'a@school.test',
        roles: ['director'],
        permissions: ['*'],
        capabilities: [],
      }),
    ).toThrow(UnauthorizedException);
  });

  it('revokes refresh tokens in memory', async () => {
    const id = service();
    const session = id.issueSession({
      sub: 'user-1',
      tenantId: 'tenant-1',
      email: 'a@school.test',
      roles: ['director'],
      permissions: ['*'],
      capabilities: [],
    });
    await id.revokeRefreshToken(session.refreshToken);
    expect(() => id.verifyRefreshToken(session.refreshToken)).toThrow(UnauthorizedException);
  });
});
