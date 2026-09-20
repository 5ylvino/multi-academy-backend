import { ForbiddenException, Injectable } from '@nestjs/common';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RuntimeConfigService } from './runtime-config.service';
import { policySnapshotMatches } from './policy-snapshot.util';
import type { TenantRuntimeConfig } from './runtime-config.types';

type PolicyClaims = Pick<
  AuthUserClaims,
  'config_version' | 'features_digest' | 'tenant_status'
>;

@Injectable()
export class EnforcementService {
  constructor(private readonly runtimeConfig: RuntimeConfigService) {}

  /**
   * Apply control-plane blocks before sensitive work. Call from login,
   * registration, and the global EnforcementGuard.
   */
  async assertTenantAllowed(
    tenantId: string,
    opts?: {
      userId?: string;
      email?: string;
      ip?: string;
    },
    claims?: PolicyClaims,
  ): Promise<void> {
    const peek = this.runtimeConfig.peekCached(tenantId);
    if (
      policySnapshotMatches(peek, {
        configVersion: Number(claims?.config_version),
        featuresDigest: claims?.features_digest,
      })
    ) {
      this.assertFromConfig(peek, opts);
      return;
    }

    const config = await this.runtimeConfig.getConfig(tenantId);
    this.assertFromConfig(config, opts);
  }

  private assertFromConfig(
    config: TenantRuntimeConfig,
    opts?: {
      userId?: string;
      email?: string;
      ip?: string;
    },
  ): void {
    const enf = config.enforcement;

    if (config.status === 'blacklisted' || enf.schoolBlacklisted) {
      throw new ForbiddenException(
        enf.message || 'This school has been blocked from the platform. Contact support.',
      );
    }

    if (config.status === 'suspended' || config.status === 'restricted' || enf.denyLogin) {
      throw new ForbiddenException(
        enf.message ||
          (config.status === 'restricted'
            ? 'This school is restricted. Contact support.'
            : 'This school is suspended. Contact support.'),
      );
    }

    if (opts?.userId && enf.blockedUserIds?.includes(opts.userId)) {
      throw new ForbiddenException('Your account has been blocked.');
    }
    if (opts?.email && enf.blockedEmails?.map((e) => e.toLowerCase()).includes(opts.email.toLowerCase())) {
      throw new ForbiddenException('Your account has been blocked.');
    }
    if (opts?.ip && this.ipBlocked(opts.ip, enf.blockedIpCidrs || [])) {
      throw new ForbiddenException('Access denied from this network.');
    }
  }

  private ipBlocked(ip: string, cidrs: string[]): boolean {
    if (cidrs.includes(ip)) return true;
    return cidrs.some((cidr) => this.ipv4InCidr(ip, cidr));
  }

  private ipv4InCidr(ip: string, cidr: string): boolean {
    if (!cidr.includes('/')) return false;
    const [range, bitsRaw] = cidr.split('/');
    const bits = Number(bitsRaw);
    const toInt = (value: string) => {
      const parts = value.split('.').map((part) => Number(part));
      if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
        return null;
      }
      return ((parts[0] << 24) >>> 0) + (parts[1] << 16) + (parts[2] << 8) + parts[3];
    };
    const ipInt = toInt(ip);
    const rangeInt = toInt(range);
    if (ipInt == null || rangeInt == null || !Number.isInteger(bits) || bits < 0 || bits > 32) {
      return false;
    }
    const mask = bits === 0 ? 0 : (~0 << (32 - bits)) >>> 0;
    return (ipInt & mask) === (rangeInt & mask);
  }
}
