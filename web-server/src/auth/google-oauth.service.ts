import { ForbiddenException, Injectable, UnauthorizedException } from '@nestjs/common';
import { createHash, randomBytes } from 'crypto';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { ProviderSecretsService } from '../platform-config/provider-secrets.service';
import { RuntimeConfigService } from '../platform-config/runtime-config.service';

/**
 * Google OAuth (auth.google_oauth) — authorization-code exchange MVP.
 * Client ID / secret come from control vault when available; optional env fallback for local.
 * Fail closed when flag off or secrets missing.
 */
@Injectable()
export class GoogleOauthService {
  constructor(
    private readonly flags: FeatureFlagService,
    private readonly secrets: ProviderSecretsService,
    private readonly runtime: RuntimeConfigService,
  ) {}

  async start(tenantId: string | undefined, redirectUri: string) {
    if (tenantId) {
      await this.flags.assertEnabled(tenantId, 'auth.google_oauth');
    }
    const cfg = await this.resolveGoogleConfig(tenantId);
    if (!cfg.clientId) {
      throw new ForbiddenException('Google OAuth not configured in control plane');
    }
    const state = randomBytes(16).toString('hex');
    const url = new URL('https://accounts.google.com/o/oauth2/v2/auth');
    url.searchParams.set('client_id', cfg.clientId);
    url.searchParams.set('redirect_uri', redirectUri);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('scope', 'openid email profile');
    url.searchParams.set('state', state);
    url.searchParams.set('access_type', 'online');
    url.searchParams.set('prompt', 'select_account');
    return { authorizationUrl: url.toString(), state, providerId: 'google' };
  }

  async callback(input: { tenantId?: string; code: string; redirectUri: string }) {
    if (input.tenantId) {
      await this.flags.assertEnabled(input.tenantId, 'auth.google_oauth');
    }
    const cfg = await this.resolveGoogleConfig(input.tenantId);
    if (!cfg.clientId || !cfg.clientSecret) {
      throw new ForbiddenException('Google OAuth secrets missing in control vault');
    }

    const tokenRes = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code: input.code,
        client_id: cfg.clientId,
        client_secret: cfg.clientSecret,
        redirect_uri: input.redirectUri,
        grant_type: 'authorization_code',
      }),
    });
    if (!tokenRes.ok) {
      throw new UnauthorizedException('Google token exchange failed');
    }
    const tokens = (await tokenRes.json()) as { access_token?: string };
    if (!tokens.access_token) {
      throw new UnauthorizedException('Google access token missing');
    }

    const profileRes = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
      headers: { Authorization: `Bearer ${tokens.access_token}` },
    });
    if (!profileRes.ok) {
      throw new UnauthorizedException('Google profile fetch failed');
    }
    const profile = (await profileRes.json()) as {
      email?: string;
      email_verified?: boolean;
      name?: string;
      sub?: string;
    };
    if (!profile.email || profile.email_verified === false) {
      throw new UnauthorizedException('Google email not verified');
    }

    return {
      email: profile.email,
      name: profile.name || profile.email,
      googleSub: profile.sub,
      linked: false,
      message:
        'Google identity verified. Complete account linking via email login once, or invite flow.',
      fingerprint: createHash('sha256')
        .update(profile.sub || profile.email)
        .digest('hex')
        .slice(0, 16),
    };
  }

  private async resolveGoogleConfig(tenantId?: string) {
    let clientId = '';
    let clientSecret = '';
    try {
      if (tenantId) {
        const rt = await this.runtime.getConfig(tenantId);
        const oauth =
          (rt as any)?.providers?.oauth || (rt as any)?.providers?.google_oauth || {};
        clientId = oauth.clientId || oauth.client_id || '';
        const payload = await this.secrets.getSecrets(tenantId, 'oauth');
        if (payload?.secrets) {
          clientId = clientId || payload.secrets.client_id || payload.secrets.clientId || '';
          clientSecret =
            payload.secrets.client_secret || payload.secrets.clientSecret || '';
        }
        if (payload?.settings) {
          clientId =
            clientId ||
            String(payload.settings.clientId || payload.settings.client_id || '');
        }
      }
    } catch {
      /* fail closed below */
    }
    clientId = clientId || process.env.GOOGLE_OAUTH_CLIENT_ID || '';
    clientSecret = clientSecret || process.env.GOOGLE_OAUTH_CLIENT_SECRET || '';
    return { clientId, clientSecret };
  }
}
