import { Injectable } from '@nestjs/common';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { RuntimeConfigService } from '../platform-config/runtime-config.service';

/**
 * Google Workspace extras beyond Meet (Drive / Classroom hooks).
 * Live OAuth wiring uses control vault secrets when available.
 */
@Injectable()
export class WorkspaceIntegrationsService {
  constructor(
    private readonly flags: FeatureFlagService,
    private readonly runtime: RuntimeConfigService,
  ) {}

  async getStatus(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'integrations.workspace');
    const config = await this.runtime.getConfig(tenantId);
    const meeting = config.providers?.meeting;
    return {
      enabled: true,
      meetingProvider: meeting?.providerId || null,
      meetingMode: meeting?.mode || null,
      capabilities: {
        drive: false,
        classroom: false,
        calendar: Boolean(meeting?.providerId),
        meet: Boolean(meeting?.providerId === 'google_meet'),
      },
      note:
        'Workspace extras are provisioned when Google OAuth client secrets are present in the control vault. Meet uses the meetings module.',
      links: {
        meetings: '/dashboard/meetings',
        calendar: '/dashboard/calendar',
      },
    };
  }

  async listExtras(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'integrations.workspace');
    return [
      {
        id: 'drive_lesson_notes',
        name: 'Drive lesson note folders',
        status: 'planned',
        description: 'Auto-create Drive folders per class for lesson notes.',
      },
      {
        id: 'classroom_sync',
        name: 'Google Classroom roster sync',
        status: 'planned',
        description: 'Pull Classroom courses into academic classes.',
      },
      {
        id: 'calendar_workspace',
        name: 'Workspace calendar publish',
        status: 'available_via_meetings',
        description: 'Publish school events via meetings calendar sync.',
      },
    ];
  }
}
