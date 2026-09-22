import {
  BadRequestException,
  ForbiddenException,
  HttpException,
  Injectable,
  Logger,
  ServiceUnavailableException,
  UnauthorizedException,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { IdentificationService } from '../identification/identification.service';
import { RegisterStep1Dto } from './dto/register-step1.dto';
import { RegisterStep2Dto } from './dto/register-step2.dto';
import { LoginDto } from './dto/login.dto';
import {
  effectivePermissions,
  effectiveSchoolLevel,
} from '../common/auth/session-profile.util';
import { ControlApiClient } from '../platform-config/control-api.client';
import { EnforcementService } from '../platform-config/enforcement.service';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { RuntimeConfigService } from '../platform-config/runtime-config.service';
import { buildPolicySnapshot, type PolicySnapshot } from '../platform-config/policy-snapshot.util';
import { CommsService } from '../platform-config/comms.service';

@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  constructor(
    private readonly controlPlaneService: ControlPlaneService,
    private readonly identificationService: IdentificationService,
    private readonly controlApi: ControlApiClient,
    private readonly enforcement: EnforcementService,
    private readonly featureFlags: FeatureFlagService,
    private readonly runtimeConfig: RuntimeConfigService,
    private readonly comms: CommsService,
  ) {}

  /** Embed policy snapshot in JWT — enables guard fast path without control round-trip. */
  private async policySnapshotForTenant(tenantId: string): Promise<PolicySnapshot | undefined> {
    try {
      const cfg = await this.runtimeConfig.getConfig(tenantId);
      if (cfg.configVersion <= 0) return undefined;
      return buildPolicySnapshot(cfg);
    } catch {
      return undefined;
    }
  }

  async registerStep1(payload: RegisterStep1Dto) {
    try {
      // Global registration gate — fail closed when control is configured and flag is off.
      // Without a tenant yet we use a sentinel id; FeatureFlagService falls back to foundation allowlist.
      await this.featureFlags.assertEnabled(
        '__registration__',
        'auth.register',
      );

      if (
        this.controlApi.isConfigured() &&
        process.env.NODE_ENV === 'production'
      ) {
        const check = await this.controlApi.checkRegistration({
          adminEmail: payload.email,
          phone: (payload as any).phone,
          schoolName:
            (payload as any).organizationName || (payload as any).schoolName,
          installToken: payload.installToken,
          deviceHash: payload.deviceHash,
        });
        if (!check.allowed) {
          throw new ForbiddenException(
            check.message ||
              'Registration cannot be completed. Please contact support.',
          );
        }
      }

      const tenant =
        await this.controlPlaneService.createTenantWithOwnerStaging({
          owner: {
            fullName: payload.fullName,
            email: payload.email,
            password: payload.password,
            phone: (payload as any).phone,
            installToken: payload.installToken,
            deviceHash: payload.deviceHash,
          },
        });

      const verificationToken = (
        tenant as typeof tenant & { verificationToken?: string }
      ).verificationToken;
      const clientBase = (
        process.env.CLIENT_APP_URL ||
        process.env.CORS_ORIGINS?.split(',')[0]?.trim() ||
        'http://localhost:3000'
      ).replace(/\/$/, '');
      const verificationUrl = `${clientBase}/register/verify?token=${encodeURIComponent(
        verificationToken || '',
      )}`;
      let verificationEmailSent = false;
      try {
        await this.comms.sendTransactionalEmail('__platform__', {
          to: payload.email,
          subject: 'Verify your school account',
          text: [
            `Your school verification code is: ${verificationToken}`,
            'This code expires in 15 minutes.',
            `You can also open: ${verificationUrl}`,
          ].join('\n'),
          html: `<p>Your school verification code is:</p><p style="font-size:28px;font-weight:700;letter-spacing:8px">${verificationToken}</p><p>This code expires in 15 minutes.</p><p><a href="${verificationUrl}">Verify email</a></p>`,
        });
        verificationEmailSent = true;
      } catch (error) {
        this.logger.error(
          `Registration verification email failed: ${
            error instanceof Error ? error.message : error
          }`,
        );
      }
      if (!verificationEmailSent && process.env.NODE_ENV === 'production') {
        throw new ServiceUnavailableException(
          'Registration email delivery is temporarily unavailable',
        );
      }

      return process.env.NODE_ENV === 'production'
        ? { message: 'Check your email for a 6-digit verification code.' }
        : {
            message: 'Check your email for a 6-digit verification code.',
            verificationCode: verificationToken,
            verificationUrl,
          };
    } catch (error) {
      this.logger.error(
        'Error creating tenant',
        error instanceof Error ? error.stack : String(error),
      );
      if (error instanceof HttpException) throw error;
      throw new BadRequestException('Failed to setup');
    }
  }

  async verifyEmail(token: string) {
    try {
      const tenant = await this.controlPlaneService.verifyOwnerEmail(token);
      const stagedEmail = String(
        tenant.ownerRegistrationPayloadJson?.email || '',
      ).toLowerCase();
      if (this.controlApi.isConfigured()) {
        await this.controlApi.registerTenant(tenant.id, {
          slug: tenant.slug,
          name: tenant.name,
          adminEmail: stagedEmail,
          adminPhone: String(tenant.ownerRegistrationPayloadJson?.phone || ''),
          installToken: String(
            tenant.ownerRegistrationPayloadJson?.installToken || '',
          ),
          deviceHash: String(
            tenant.ownerRegistrationPayloadJson?.deviceHash || '',
          ),
          region: 'NG',
        });
        try {
          await this.controlApi.activateTenant(tenant.id);
        } catch (error) {
          if (process.env.NODE_ENV === 'production') throw error;
          this.logger.warn(
            `Control activation skipped in development: ${
              error instanceof Error ? error.message : error
            }`,
          );
        }
      }
      void this.finishVerifiedTenantOnboarding(tenant, stagedEmail);
      return {
        status: 'processing',
        message:
          'Your email has been verified. We are setting up your school account in the background, and this will take a moment. You will receive an email when you can log in.',
      };
    } catch (error) {
      if (error instanceof HttpException) throw error;
      this.logger.error(
        'Email verification failed',
        error instanceof Error ? error.stack : String(error),
      );
      throw new BadRequestException('Unable to verify email');
    }
  }

  async resendVerificationEmail(email: string) {
    const result = await this.controlPlaneService.refreshOwnerVerificationToken(email);
    const genericMessage =
      'If an incomplete registration exists for that email, a new verification code has been sent.';
    if (!result) return { message: genericMessage };

    const clientBase = (
      process.env.CLIENT_APP_URL ||
      process.env.CORS_ORIGINS?.split(',')[0]?.trim() ||
      'http://localhost:3000'
    ).replace(/\/$/, '');
    const verificationUrl = `${clientBase}/register/verify?token=${encodeURIComponent(
      result.verificationToken,
    )}`;

    await this.comms.sendTransactionalEmail('__platform__', {
      to: result.email,
      subject: 'Your new MA-SMS verification code',
      text: [
        `Your new MA-SMS school verification code is: ${result.verificationToken}`,
        'This code expires in 15 minutes.',
        `Verify email: ${verificationUrl}`,
      ].join('\n'),
      html: `<p>Your new MA-SMS school verification code is:</p><p style="font-size:28px;font-weight:700;letter-spacing:8px">${result.verificationToken}</p><p>This code expires in 15 minutes.</p><p><a href="${verificationUrl}">Verify email</a></p>`,
    });
    return { message: genericMessage };
  }

  private async finishVerifiedTenantOnboarding(
    tenant: Awaited<ReturnType<ControlPlaneService['verifyOwnerEmail']>>,
    email: string,
  ) {
    try {
      await this.controlPlaneService.provisionVerifiedTenant(tenant);
      const { user } = await this.controlPlaneService.createPrimaryOwnerUser({
        schoolBusinessOrganisationId: tenant.schoolBusinessOrganisationId,
        email,
      });
      const clientBase = (
        process.env.CLIENT_APP_URL ||
        process.env.CORS_ORIGINS?.split(',')[0]?.trim() ||
        'http://localhost:3000'
      ).replace(/\/$/, '');

      // The tenant has just been created and cannot have tenant-scoped
      // provider configuration yet. Use the same global platform provider
      // that delivered the verification email.
      const welcomeDelivery = await this.comms.sendTransactionalEmail('__platform__', {
        to: user.email,
        subject: `Welcome to ${tenant.name}`,
        text: [
          `Your school app is ready.`,
          `School Business Organisation ID: ${tenant.schoolBusinessOrganisationId}`,
          `Continue setup and join the organization: ${clientBase}/register`,
        ].join('\n'),
        html: `<p>Your school app is ready.</p><p><strong>School Business Organisation ID:</strong> ${tenant.schoolBusinessOrganisationId}</p><p>Use the organization ID to join your existing organization:</p><p><a href="${clientBase}/register" style="display:inline-block;padding:12px 18px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px">Join Existing Organization</a></p>`,
      });
      this.logger.log(
        `Tenant welcome email sent to ${user.email} via ${welcomeDelivery.providerId} (${welcomeDelivery.messageId})`,
      );
      this.logger.log(`Tenant onboarding completed for ${user.email}`);
    } catch (error) {
      this.logger.error(
        `Tenant onboarding failed for ${email}`,
        error instanceof Error ? error.stack : String(error),
      );
    }
  }

  async registerStep2(payload: RegisterStep2Dto) {
    try {
      await this.featureFlags.assertEnabled(
        '__registration__',
        'auth.register',
      );

      let tenant = await this.controlPlaneService.resolveTenantBySboId(
        payload.schoolBusinessOrganisationId,
      );
      let user;

      if (tenant.provisioningStatus === 'ready') {
        tenant = await this.controlPlaneService.resolveTenantForUserEmail(
          payload.email,
          payload.schoolBusinessOrganisationId,
        );
        user = await this.controlPlaneService.validateUserByTenant(
          tenant.id,
          payload.email,
          payload.password,
        );
      } else {
        const created = await this.controlPlaneService.createPrimaryOwnerUser({
          schoolBusinessOrganisationId: payload.schoolBusinessOrganisationId,
          email: payload.email,
        });
        tenant = created.tenant;
        user = created.user;
      }

      const policy = await this.policySnapshotForTenant(tenant.id);
      const session = this.identificationService.issueSession({
        sub: user.id,
        tenantId: tenant.id,
        email: user.email,
        roles: user.roles,
        permissions: user.permissions,
        capabilities: user.capabilities,
        configVersion: policy?.configVersion,
        tenantStatus: policy?.tenantStatus,
        featuresDigest: policy?.featuresDigest,
      });

      return {
        ...this.sessionTokens(session),
        user: {
          id: user.id,
          email: user.email,
          name: user.name,
          role: user.roles[0],
          roles: user.roles,
          schoolLevel: effectiveSchoolLevel(user.schoolLevel, user.roles, tenant.schoolLevels),
          permissions: user.permissions,
          capabilities: user.capabilities,
        },
      };
    } catch (error) {
      this.logger.error(
        'Error creating primary owner user',
        error instanceof Error ? error.stack : String(error),
      );
      if (error instanceof HttpException) throw error;
      throw new BadRequestException('Failed to complete registration');
    }
  }

  async login(payload: LoginDto) {
    try {
      const tenant = payload.schoolSlug
        ? await this.controlPlaneService.resolveTenantBySlug(payload.schoolSlug)
        : await this.controlPlaneService.resolveTenantForUserEmail(
            payload.email,
            payload.schoolBusinessOrganisationId,
          );

      // Control-plane blocks (blacklist / suspend / user+email blocks) — surface as 403.
      await this.enforcement.assertTenantAllowed(tenant.id, {
        email: payload.email,
      });
      await this.featureFlags.assertEnabled(tenant.id, 'auth.login');

      const user = await this.controlPlaneService.validateUserByTenant(
        tenant.id,
        payload.email,
        payload.password,
      );
      // The platform login is available to primary owners and directors.
      // Other school users should use their school-scoped login so the
      // tenant context is explicit.
      const canUsePlatformLogin =
        user.permissions.includes('*') || user.roles.includes('director');
      if (!payload.schoolSlug && !canUsePlatformLogin) {
        throw new UnauthorizedException('Primary owner login required');
      }
      await this.enforcement.assertTenantAllowed(tenant.id, {
        userId: user.id,
        email: user.email,
      });

      const permissions = effectivePermissions(user.roles, user.permissions);

      // Remember-me: only honor when flag is on; otherwise use default refresh TTL.
      let refreshTtlSeconds: number | undefined;
      if (payload.rememberMe) {
        const rememberOn = await this.featureFlags.resolve(
          tenant.id,
          'auth.remember_me',
        );
        if (rememberOn) {
          refreshTtlSeconds = 60 * 60 * 24 * 30; // 30 days
        }
      }

      const policy = await this.policySnapshotForTenant(tenant.id);
      const session = this.identificationService.issueSession({
        sub: user.id,
        tenantId: tenant.id,
        email: user.email,
        roles: user.roles,
        permissions,
        capabilities: user.capabilities,
        configVersion: policy?.configVersion,
        tenantStatus: policy?.tenantStatus,
        featuresDigest: policy?.featuresDigest,
        refreshTtlSeconds,
      });
      return {
        ...this.sessionTokens(session),
        user: {
          id: user.id,
          email: user.email,
          name: user.name,
          phone: (user as any).phone,
          role: user.roles[0],
          roles: user.roles,
          tenantSlug: tenant.slug,
          schoolLevel: effectiveSchoolLevel(user.schoolLevel, user.roles, tenant.schoolLevels),
          permissions,
          capabilities: user.capabilities,
        },
      };
    } catch (error) {
      if (error instanceof ForbiddenException) throw error;
      this.logger.warn(
        `Login failed for ${payload.email}: ${
          error instanceof Error ? error.message : error
        }`,
      );
      // Uniform message prevents account enumeration.
      throw new UnauthorizedException('Invalid credentials');
    }
  }

  async me(accessToken: string) {
    const claims = this.identificationService.verifyAccessToken(accessToken);
    const tenant = await this.controlPlaneService.getTenantById(
      claims.tenant_id,
    );
    if (!tenant) {
      throw new UnauthorizedException('Invalid tenant context');
    }
    const user = await this.controlPlaneService.getUserById(
      claims.tenant_id,
      claims.sub,
    );
    if (!user) {
      throw new UnauthorizedException('User not found in tenant');
    }

    return {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.roles[0],
      roles: user.roles,
      tenantSlug: tenant.slug,
      schoolLevel: effectiveSchoolLevel(user.schoolLevel, user.roles, tenant.schoolLevels),
      permissions: effectivePermissions(user.roles, user.permissions),
      capabilities: user.capabilities,
      access_token: accessToken,
    };
  }

  async refresh(refreshToken: string) {
    const refreshClaims =
      this.identificationService.verifyRefreshToken(refreshToken);
    if (await this.identificationService.isRefreshRevoked(refreshClaims.jti)) {
      throw new UnauthorizedException('Refresh token revoked');
    }
    const tenant = await this.controlPlaneService.getTenantById(
      refreshClaims.tenant_id,
    );
    if (!tenant) {
      throw new UnauthorizedException('Invalid tenant in refresh token');
    }
    const user = await this.controlPlaneService.getUserById(
      tenant.id,
      refreshClaims.sub,
    );
    if (!user || !user.isActive) {
      throw new UnauthorizedException('User not found or inactive');
    }
    await this.identificationService.revokeRefreshToken(refreshToken);

    const policy = await this.policySnapshotForTenant(tenant.id);
    const session = this.identificationService.issueSession({
      sub: user.id,
      tenantId: tenant.id,
      email: user.email,
      roles: user.roles,
      permissions: effectivePermissions(user.roles, user.permissions),
      capabilities: user.capabilities,
      configVersion: policy?.configVersion,
      tenantStatus: policy?.tenantStatus,
      featuresDigest: policy?.featuresDigest,
    });

    return this.sessionTokens(session);
  }

  private sessionTokens(session: { accessToken: string; refreshToken: string }) {
    return {
      token: session.accessToken,
      access_token: session.accessToken,
      refresh_token: session.refreshToken,
    };
  }

  async requestPasswordReset(email: string, schoolSlug?: string) {
    if (process.env.NODE_ENV !== 'production') {
      this.logger.log(`[DEV EMAIL] Password reset requested for ${email}`);
    }

    // Password reset is public; use foundation allowlist via sentinel tenant.
    await this.featureFlags.assertEnabled(
      '__registration__',
      'auth.password_reset',
    );

    const result = await this.controlPlaneService.createPasswordResetToken(email, schoolSlug);
    if (!result.created && process.env.NODE_ENV !== 'production') {
      this.logger.warn(
        `[DEV EMAIL] No active account found for ${email}; no email content generated`,
      );
    }
    // Always return the same message to prevent account enumeration.
    const clientBase =
      process.env.CLIENT_APP_URL ||
      process.env.CORS_ORIGINS?.split(',')[0]?.trim() ||
      'http://localhost:3000';

    if (result.created && result.token) {
      const resetSlug = schoolSlug || result.tenantSlug;
      console.log('schoolSlug', schoolSlug);
      console.log('result.tenantSlug', result.tenantSlug);

      const resetPath = resetSlug
        ? `/${resetSlug}/reset-password`
        : '/reset-password';
      const resetUrl = `${clientBase.replace(
        /\/$/,
        '',
      )}${resetPath}?token=${encodeURIComponent(result.token)}`;
      const emailContent = {
        to: email,
        subject: 'Password reset request',
        text: `Use this link to reset your password: ${resetUrl}`,
        html: `<p>Use this link to reset your password:</p><p><a href="${resetUrl}">${resetUrl}</a></p>`,
      };

      if (process.env.NODE_ENV !== 'production') {
        this.logger.log(
          `[DEV EMAIL] Password reset request\nTo: ${emailContent.to}\nSubject: ${emailContent.subject}\nText: ${emailContent.text}\nHTML: ${emailContent.html}`,
        );
      }

      // Send email when comms.email is enabled; otherwise log for ops.
      let emailSent = false;
      if (result.tenantId) {
        try {
          const emailOn = await this.featureFlags.resolve(
            result.tenantId,
            'comms.email',
          );
          if (emailOn) {
            await this.comms.sendEmail(result.tenantId, emailContent);
            emailSent = true;
          }
        } catch (err) {
          this.logger.warn(
            `Password reset email failed: ${
              err instanceof Error ? err.message : err
            }`,
          );
        }
      }

      if (!emailSent) {
        if (process.env.NODE_ENV === 'production') {
          this.logger.error(
            'Password reset email was not delivered; token withheld from logs',
          );
        } else {
          this.logger.log(`Password reset link for ${email}: ${resetUrl}`);
        }
      }

      // In non-production, return the URL so local testing works without SMTP.
      if (process.env.NODE_ENV !== 'production') {
        return {
          message:
            'If an account exists for that email, a reset link has been sent.',
          devResetUrl: resetUrl,
        };
      }
    }

    return {
      message:
        'If an account exists for that email, a reset link has been sent.',
    };
  }

  async confirmPasswordReset(token: string, password: string) {
    await this.featureFlags.assertEnabled(
      '__registration__',
      'auth.password_reset',
    );

    if (!token || !password || password.length < 8) {
      throw new BadRequestException(
        'A valid token and password (min 8 characters) are required',
      );
    }
    await this.controlPlaneService.resetPasswordWithToken(token, password);
    return { message: 'Password has been reset successfully' };
  }
}
