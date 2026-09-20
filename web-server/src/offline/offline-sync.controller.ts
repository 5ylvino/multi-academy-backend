import { Body, Controller, Get, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

/**
 * Offline PWA sync handshake — client queues mutations while offline and
 * posts them here when connectivity returns. Persistence is acknowledge-only
 * in this phase (mutations are applied by their domain endpoints).
 */
@Controller('offline')
@RequireFeature('offline.pwa')
export class OfflineSyncController {
  constructor(private readonly flags: FeatureFlagService) {}

  @Get('status')
  async status(@TenantId() tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'offline.pwa');
    return ok('Offline sync status', {
      enabled: true,
      maxQueueSize: 200,
      supportedCollections: [
        'attendance.students',
        'announcements',
        'notifications',
        'academic.results.draft',
      ],
      protocolVersion: 1,
    });
  }

  @Post('sync')
  async sync(
    @TenantId() tenantId: string,
    @Body()
    body: {
      clientId?: string;
      mutations?: Array<{
        id: string;
        collection: string;
        op: string;
        payload?: Record<string, unknown>;
        clientTs?: string;
      }>;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'offline.pwa');
    const mutations = Array.isArray(body.mutations) ? body.mutations : [];
    const accepted = mutations.map((m) => ({
      id: m.id,
      status: 'ack' as const,
      note: 'Apply via domain API; offline queue ack recorded',
    }));
    return ok('Offline sync acknowledged', {
      clientId: body.clientId || null,
      accepted,
      serverTs: new Date().toISOString(),
    });
  }
}
