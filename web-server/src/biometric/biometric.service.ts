import {
  BadRequestException,
  Injectable,
  NotFoundException,
  UnauthorizedException,
} from '@nestjs/common';
import {
  generateAuthenticationOptions,
  generateRegistrationOptions,
  verifyAuthenticationResponse,
  verifyRegistrationResponse,
} from '@simplewebauthn/server';
import type {
  AuthenticationResponseJSON,
  AuthenticatorTransportFuture,
  RegistrationResponseJSON,
} from '@simplewebauthn/types';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { resolveWebAuthnConfig } from './webauthn-config.util';
import type { Request } from 'express';

type ChallengeEntry = { challenge: string; expiresAt: number };
type AssertionEntry = {
  userId: string;
  tenantId: string;
  purpose: string;
  expiresAt: number;
  used: boolean;
};

/** Postgres often returns unquoted aliases lowercased (`credentialid`). */
function rowField(row: Record<string, any>, ...keys: string[]): any {
  for (const key of keys) {
    if (row[key] !== undefined && row[key] !== null) return row[key];
    const lower = key.toLowerCase();
    for (const actual of Object.keys(row)) {
      if (actual.toLowerCase() === lower && row[actual] !== undefined && row[actual] !== null) {
        return row[actual];
      }
    }
  }
  return undefined;
}

@Injectable()
export class BiometricService {
  private readonly registerChallenges = new Map<string, ChallengeEntry>();
  private readonly authChallenges = new Map<string, ChallengeEntry>();
  private readonly assertions = new Map<string, AssertionEntry>();

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
  ) {}

  private getWebAuthnConfig(req?: Request) {
    return resolveWebAuthnConfig(req);
  }

  private parseTransports(raw: unknown): AuthenticatorTransportFuture[] | undefined {
    if (!raw) return undefined;
    try {
      const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
      return Array.isArray(parsed) ? (parsed as AuthenticatorTransportFuture[]) : undefined;
    } catch {
      return undefined;
    }
  }

  private buildCredentialDescriptors(
    rows: any[],
  ): Array<{ id: string; transports?: AuthenticatorTransportFuture[] }> {
    return rows
      .map((c) => {
        const id = rowField(c, 'credentialId', 'credential_id');
        if (!id || typeof id !== 'string' || !/^[A-Za-z0-9_-]+$/.test(id)) return null;
        return {
          id,
          transports: this.parseTransports(rowField(c, 'transports')),
        };
      })
      .filter(Boolean) as Array<{ id: string; transports?: AuthenticatorTransportFuture[] }>;
  }

  private challengeKey(tenantId: string, userId: string) {
    return `${tenantId}:${userId}`;
  }

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureTables(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS biometric_credentials (
        id varchar(64) PRIMARY KEY,
        user_id varchar(64) NOT NULL,
        credential_id text NOT NULL,
        public_key text NOT NULL,
        counter bigint NOT NULL DEFAULT 0,
        transports text NULL,
        device_type varchar(32) NULL,
        backed_up boolean NOT NULL DEFAULT false,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (credential_id)
      );
    `);
    for (const column of [
      `ALTER TABLE biometric_credentials ADD COLUMN IF NOT EXISTS transports text NULL`,
      `ALTER TABLE biometric_credentials ADD COLUMN IF NOT EXISTS device_type varchar(32) NULL`,
      `ALTER TABLE biometric_credentials ADD COLUMN IF NOT EXISTS backed_up boolean NOT NULL DEFAULT false`,
      `ALTER TABLE biometric_credentials ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`,
    ]) {
      try {
        await ds.query(column);
      } catch {
        // Existing deployments may already have the column.
      }
    }
    await ds.query(`
      CREATE INDEX IF NOT EXISTS idx_biometric_credentials_user
      ON biometric_credentials (user_id);
    `);
  }

  private purgeExpired() {
    const now = Date.now();
    for (const [k, v] of this.registerChallenges) {
      if (v.expiresAt < now) this.registerChallenges.delete(k);
    }
    for (const [k, v] of this.authChallenges) {
      if (v.expiresAt < now) this.authChallenges.delete(k);
    }
    for (const [k, v] of this.assertions) {
      if (v.expiresAt < now || v.used) this.assertions.delete(k);
    }
  }

  async getStatus(tenantId: string, userId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, device_type as deviceType, backed_up as backedUp, created_at as createdAt
       FROM biometric_credentials WHERE user_id = ? ORDER BY created_at DESC`,
      [userId],
    );
    return {
      enrolled: rows.length > 0,
      credentials: rows.map((r) => ({
        id: rowField(r, 'id'),
        deviceType: rowField(r, 'deviceType', 'device_type'),
        backedUp: !!rowField(r, 'backedUp', 'backed_up'),
        createdAt: rowField(r, 'createdAt', 'created_at'),
      })),
    };
  }

  async beginRegistration(
    tenantId: string,
    userId: string,
    userName: string,
    userDisplayName: string,
    req?: Request,
  ) {
    if (!userId?.trim()) {
      throw new BadRequestException('Authenticated user id is required for biometric enrollment');
    }

    this.purgeExpired();
    const webauthn = this.getWebAuthnConfig(req);
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing: any[] = await runDbQuery(
      ds,
      `SELECT credential_id as credentialId, transports FROM biometric_credentials WHERE user_id = ?`,
      [userId],
    );

    const excludeCredentials = this.buildCredentialDescriptors(existing);

    try {
      const options = await generateRegistrationOptions({
        rpName: webauthn.rpName,
        rpID: webauthn.rpID,
        userName: userName || userId,
        userDisplayName: userDisplayName || userName || userId,
        userID: new TextEncoder().encode(userId),
        attestationType: 'none',
        authenticatorSelection: {
          residentKey: 'preferred',
          userVerification: 'required',
          // Prefer platform fingerprint/Touch ID but allow cross-platform passkeys.
          authenticatorAttachment: 'platform',
        },
        excludeCredentials,
      });

      this.registerChallenges.set(this.challengeKey(tenantId, userId), {
        challenge: options.challenge,
        expiresAt: Date.now() + 5 * 60_000,
      });

      return options;
    } catch (err) {
      throw new BadRequestException(
        err instanceof Error
          ? `Could not start biometric enrollment: ${err.message}`
          : 'Could not start biometric enrollment',
      );
    }
  }

  async finishRegistration(
    tenantId: string,
    userId: string,
    response: RegistrationResponseJSON,
    req?: Request,
  ) {
    this.purgeExpired();
    const webauthn = this.getWebAuthnConfig(req);
    const key = this.challengeKey(tenantId, userId);
    const expected = this.registerChallenges.get(key);
    if (!expected) throw new BadRequestException('Registration challenge expired. Try again.');

    let verification;
    try {
      verification = await verifyRegistrationResponse({
        response,
        expectedChallenge: expected.challenge,
        expectedOrigin: webauthn.origins,
        expectedRPID: webauthn.rpID,
        requireUserVerification: true,
      });
    } catch (err) {
      throw new BadRequestException(
        err instanceof Error ? err.message : 'Biometric registration failed',
      );
    }

    if (!verification.verified || !verification.registrationInfo) {
      throw new BadRequestException('Biometric registration could not be verified');
    }

    const { credential, credentialDeviceType, credentialBackedUp } = verification.registrationInfo;
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);

    const id = randomToken('bio');
    const publicKey = Buffer.from(credential.publicKey).toString('base64url');
    await runDbQuery(
      ds,
      `INSERT INTO biometric_credentials
        (id, user_id, credential_id, public_key, counter, transports, device_type, backed_up, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        userId,
        credential.id,
        publicKey,
        credential.counter,
        credential.transports ? JSON.stringify(credential.transports) : null,
        credentialDeviceType,
        !!credentialBackedUp,
      ],
    );

    this.registerChallenges.delete(key);
    return { id, enrolled: true };
  }

  async beginAuthentication(tenantId: string, userId: string, req?: Request) {
    this.purgeExpired();
    const webauthn = this.getWebAuthnConfig(req);
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing: any[] = await runDbQuery(
      ds,
      `SELECT credential_id as credentialId, transports FROM biometric_credentials WHERE user_id = ?`,
      [userId],
    );
    if (!existing.length) {
      throw new BadRequestException(
        'No biometric credential enrolled. Enroll a passkey/fingerprint in Settings first.',
      );
    }

    const allowCredentials = this.buildCredentialDescriptors(existing);

    if (!allowCredentials.length) {
      throw new BadRequestException(
        'Stored biometric credentials are invalid. Remove them in Settings and enroll again.',
      );
    }

    const options = await generateAuthenticationOptions({
      rpID: webauthn.rpID,
      userVerification: 'required',
      allowCredentials,
    });

    this.authChallenges.set(this.challengeKey(tenantId, userId), {
      challenge: options.challenge,
      expiresAt: Date.now() + 5 * 60_000,
    });

    return options;
  }

  async finishAuthentication(
    tenantId: string,
    userId: string,
    response: AuthenticationResponseJSON,
    purpose = 'general',
    req?: Request,
  ) {
    this.purgeExpired();
    const webauthn = this.getWebAuthnConfig(req);
    const key = this.challengeKey(tenantId, userId);
    const expected = this.authChallenges.get(key);
    if (!expected) throw new BadRequestException('Authentication challenge expired. Try again.');

    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, credential_id as credentialId, public_key as publicKey, counter, transports
       FROM biometric_credentials WHERE user_id = ? AND credential_id = ? LIMIT 1`,
      [userId, response.id],
    );
    const cred = rows[0]
      ? {
          id: rowField(rows[0], 'id'),
          credentialId: rowField(rows[0], 'credentialId', 'credential_id'),
          publicKey: rowField(rows[0], 'publicKey', 'public_key'),
          counter: rowField(rows[0], 'counter'),
          transports: rowField(rows[0], 'transports'),
        }
      : null;
    if (!cred?.credentialId || !cred.publicKey) {
      throw new UnauthorizedException('Unknown biometric credential');
    }

    let verification;
    try {
      verification = await verifyAuthenticationResponse({
        response,
        expectedChallenge: expected.challenge,
        expectedOrigin: webauthn.origins,
        expectedRPID: webauthn.rpID,
        requireUserVerification: true,
        credential: {
          id: cred.credentialId,
          publicKey: Uint8Array.from(Buffer.from(cred.publicKey, 'base64url')),
          counter: Number(cred.counter),
          transports: cred.transports
            ? (JSON.parse(cred.transports) as AuthenticatorTransportFuture[])
            : undefined,
        },
      });
    } catch (err) {
      throw new UnauthorizedException(
        err instanceof Error ? err.message : 'Biometric verification failed',
      );
    }

    if (!verification.verified) {
      throw new UnauthorizedException('Biometric verification failed');
    }

    await runDbQuery(
      ds,
      `UPDATE biometric_credentials SET counter = ?, updated_at = NOW() WHERE id = ?`,
      [verification.authenticationInfo.newCounter, cred.id],
    );

    this.authChallenges.delete(key);

    const assertionId = randomToken('assert');
    this.assertions.set(assertionId, {
      userId,
      tenantId,
      purpose,
      expiresAt: Date.now() + 10 * 60_000,
      used: false,
    });

    return {
      verified: true,
      assertionId,
      purpose,
      expiresInSeconds: 600,
    };
  }

  /**
   * Consume a one-time biometric assertion issued after successful WebAuthn auth.
   * Used by payment / staff attendance to prove biometric was completed server-side.
   */
  consumeAssertion(params: {
    assertionId: string;
    tenantId: string;
    userId: string;
    purpose: string;
  }) {
    this.purgeExpired();
    const entry = this.assertions.get(params.assertionId);
    if (!entry || entry.used) {
      throw new UnauthorizedException('Biometric assertion is invalid or already used');
    }
    if (entry.expiresAt < Date.now()) {
      this.assertions.delete(params.assertionId);
      throw new UnauthorizedException('Biometric assertion expired');
    }
    if (entry.tenantId !== params.tenantId || entry.userId !== params.userId) {
      throw new UnauthorizedException('Biometric assertion does not belong to this user');
    }
    const purposeMatches =
      entry.purpose === params.purpose ||
      entry.purpose === 'general' ||
      (params.purpose === 'staff_attendance' && entry.purpose === 'staff_login');
    if (!purposeMatches) {
      throw new UnauthorizedException(`Biometric assertion was issued for ${entry.purpose}, not ${params.purpose}`);
    }
    entry.used = true;
    this.assertions.delete(params.assertionId);
    return true;
  }

  async removeCredential(tenantId: string, userId: string, credentialRowId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(
      ds,
      `DELETE FROM biometric_credentials WHERE id = ? AND user_id = ?`,
      [credentialRowId, userId],
    );
    return { id: credentialRowId };
  }
}
