import { ForbiddenException, Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { ProviderRegistryService } from '../platform-config/providers/provider-registry.service';
import { CalendarService } from '../calendar/calendar.service';

function withAudioOnlyJoinUrl(url: string): string {
  if (!url) return url;
  try {
    const parsed = new URL(url);
    parsed.searchParams.set('audioOnly', '1');
    if (parsed.hostname.includes('zoom.us')) parsed.searchParams.set('audio', '1');
    return parsed.toString();
  } catch {
    return `${url}${url.includes('?') ? '&' : '?'}audioOnly=1`;
  }
}

@Injectable()
export class MeetingsService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly providers: ProviderRegistryService,
    private readonly calendar: CalendarService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS virtual_meetings (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        starts_at TIMESTAMP NOT NULL,
        ends_at TIMESTAMP NULL,
        provider_id varchar(64) NOT NULL,
        provider_meeting_id varchar(128) NOT NULL,
        join_url text NOT NULL,
        host_url text NULL,
        attendee_ids text NOT NULL DEFAULT '[]',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(
      `ALTER TABLE virtual_meetings ADD COLUMN IF NOT EXISTS attendee_ids text NOT NULL DEFAULT '[]'`,
    );
    await ds.query(`ALTER TABLE virtual_meetings ADD COLUMN IF NOT EXISTS class_id varchar(64) NULL`);
    await ds.query(`ALTER TABLE virtual_meetings ADD COLUMN IF NOT EXISTS subject_id varchar(64) NULL`);
    await ds.query(`ALTER TABLE virtual_meetings ADD COLUMN IF NOT EXISTS kind varchar(32) NOT NULL DEFAULT 'meeting'`);
    await ds.query(`ALTER TABLE virtual_meetings ADD COLUMN IF NOT EXISTS audio_only boolean NOT NULL DEFAULT false`);
    await ds.query(`ALTER TABLE virtual_meetings ADD COLUMN IF NOT EXISTS recording_url text NULL`);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS live_class_attendance (
        id varchar(64) PRIMARY KEY,
        meeting_id varchar(64) NOT NULL,
        user_id varchar(64) NOT NULL,
        joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        left_at TIMESTAMP NULL
      )
    `);
  }

  async list(tenantId: string, userId: string) {
    await this.flags.assertEnabled(tenantId, 'comms.meetings');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const extraClassIds = await this.classIdsForUser(ds, userId);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, title, starts_at as "startsAt", ends_at as "endsAt",
              provider_id as "providerId", provider_meeting_id as "providerMeetingId",
              join_url as "joinUrl", host_url as "hostUrl", created_by as "createdBy",
              attendee_ids as "attendeeIds", class_id as "classId", subject_id as "subjectId",
              kind, audio_only as "audioOnly", recording_url as "recordingUrl",
              created_at as "createdAt"
       FROM virtual_meetings ORDER BY starts_at DESC LIMIT 100`,
      [],
    );
    return rows
      .map((row) => ({
        ...row,
        attendeeIds: parseIds(row.attendeeIds),
      }))
      .filter((row) => canJoinMeeting(row, userId, extraClassIds))
      .map(({ attendeeIds, ...row }) => ({
        ...row,
        hostUrl: row.createdBy === userId ? row.hostUrl : null,
      }));
  }

  async listRecipients(tenantId: string, userId: string) {
    await this.flags.assertEnabled(tenantId, 'comms.meetings');
    const ds = await this.getTenantDs(tenantId);
    const roles = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const rawRoles = roles[0]?.roles;
    const actorRoles = Array.isArray(rawRoles)
      ? rawRoles.map(String)
      : typeof rawRoles === 'string'
        ? parseIds(rawRoles)
        : roles[0]?.role
          ? [String(roles[0].role)]
          : [];
    const isAdmin = actorRoles.some((role) =>
      ['director', 'school_admin', 'it_admin', 'administrative_staff'].includes(
        role.toLowerCase(),
      ),
    );
    if (isAdmin) {
      return runDbQuery(
        ds,
        `SELECT id, name,
                CASE
                  WHEN roles LIKE '%parent%' THEN 'parent'
                  WHEN roles LIKE '%class_teacher%' THEN 'class_teacher'
                  WHEN roles LIKE '%subject_teacher%' THEN 'subject_teacher'
                  WHEN roles LIKE '%head_teacher%' THEN 'head_teacher'
                  WHEN roles LIKE '%principal%' THEN 'principal'
                END as role
         FROM users
         WHERE id <> ? AND (
           roles LIKE '%parent%' OR
           roles LIKE '%class_teacher%' OR
           roles LIKE '%subject_teacher%' OR
           roles LIKE '%head_teacher%' OR
           roles LIKE '%principal%'
         )
         ORDER BY name LIMIT 500`,
        [userId],
      );
    }
    if (actorRoles.some((role) => role.toLowerCase() === 'parent')) {
      return runDbQuery(
        ds,
        `SELECT DISTINCT u.id, u.name,
                CASE
                  WHEN u.roles LIKE '%class_teacher%' THEN 'class_teacher'
                  WHEN u.roles LIKE '%subject_teacher%' THEN 'subject_teacher'
                  WHEN u.roles LIKE '%head_teacher%' THEN 'head_teacher'
                  WHEN u.roles LIKE '%principal%' THEN 'principal'
                END as role
         FROM users u
         WHERE (
           u.roles LIKE '%class_teacher%' OR
           u.roles LIKE '%subject_teacher%' OR
           u.roles LIKE '%head_teacher%' OR
           u.roles LIKE '%principal%'
         )
           AND (
             u.id IN (
               SELECT ctl.teacher_id FROM academic_class_teacher_links ctl
               JOIN student_class_enrollments e ON e.class_id = ctl.class_id
               JOIN parent_student_links psl ON psl.student_id = e.student_id
               WHERE psl.parent_id = ?
             )
             OR u.id IN (
               SELECT stl.teacher_id FROM academic_subject_teacher_links stl
               JOIN academic_results r ON r.subject_id = stl.subject_id
               JOIN parent_student_links psl ON psl.student_id = r.student_id
               WHERE psl.parent_id = ?
             )
           )
         ORDER BY u.name LIMIT 200`,
        [userId, userId],
      );
    }
    if (
      actorRoles.some((role) =>
        ['class_teacher', 'subject_teacher', 'head_teacher', 'principal'].includes(
          role.toLowerCase(),
        ),
      )
    ) {
      return runDbQuery(
        ds,
        `SELECT DISTINCT u.id, u.name, 'parent' as role
         FROM users u
         JOIN parent_student_links psl ON psl.parent_id = u.id
         WHERE u.roles LIKE '%parent%'
           AND (
             psl.student_id IN (
               SELECT e.student_id FROM student_class_enrollments e
               JOIN academic_class_teacher_links ctl ON ctl.class_id = e.class_id
               WHERE ctl.teacher_id = ?
             )
             OR psl.student_id IN (
               SELECT r.student_id FROM academic_results r
               JOIN academic_subject_teacher_links stl ON stl.subject_id = r.subject_id
               WHERE stl.teacher_id = ?
             )
           )
         ORDER BY u.name LIMIT 200`,
        [userId, userId],
      );
    }
    return [];
  }

  async create(
    tenantId: string,
    userId: string,
    body: {
      title: string;
      startsAt: string;
      endsAt?: string;
      attendees?: string[];
      syncCalendar?: boolean;
      classId?: string;
      subjectId?: string;
      audioOnly?: boolean;
      kind?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'comms.meetings');
    const gw = await this.providers.resolveMeeting(tenantId);
    if (gw.id === 'disabled') {
      throw new NotFoundException('Meeting provider unavailable');
    }
    const meeting = await gw.createMeeting({
      title: body.title,
      startsAt: body.startsAt,
      endsAt: body.endsAt,
      attendees: body.attendees,
    });
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const id = randomToken('mtg');
    await runDbQuery(
      ds,
      `INSERT INTO virtual_meetings
        (id, title, starts_at, ends_at, provider_id, provider_meeting_id, join_url, host_url, attendee_ids, created_by, class_id, subject_id, kind, audio_only, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())`,
      [
        id,
        body.title,
        body.startsAt,
        body.endsAt || null,
        gw.id,
        meeting.providerMeetingId,
        meeting.joinUrl,
        meeting.hostUrl || null,
        JSON.stringify(body.attendees || []),
        userId,
        body.classId || null,
        body.subjectId || null,
        body.kind || (body.classId ? 'live_class' : 'meeting'),
        body.audioOnly ? true : false,
      ],
    );

    if (
      body.syncCalendar !== false &&
      (await this.flags.resolve(tenantId, 'comms.meetings_calendar_sync'))
    ) {
      try {
        await this.calendar.create(tenantId, userId, {
          title: `Meeting: ${body.title}`,
          description: meeting.joinUrl,
          eventType: 'meeting',
          startsAt: body.startsAt,
          endsAt: body.endsAt,
          location: meeting.joinUrl,
        });
      } catch {
        /* calendar flag may be off */
      }
    }

    return {
      id,
      providerId: gw.id,
      joinUrl: meeting.joinUrl,
      hostUrl: meeting.hostUrl,
      providerMeetingId: meeting.providerMeetingId,
      classId: body.classId || null,
      kind: body.kind || (body.classId ? 'live_class' : 'meeting'),
    };
  }

  async get(tenantId: string, userId: string, meetingId: string) {
    const meeting = await this.assertCanAccess(tenantId, userId, meetingId);
    return {
      ...meeting,
      hostUrl: meeting.createdBy === userId ? meeting.hostUrl : null,
    };
  }

  async join(
    tenantId: string,
    userId: string,
    meetingId: string,
    opts?: { audioOnly?: boolean },
  ) {
    const meeting = await this.assertCanAccess(tenantId, userId, meetingId);
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const id = randomToken('lca');
    await runDbQuery(
      ds,
      `INSERT INTO live_class_attendance (id, meeting_id, user_id, joined_at) VALUES (?, ?, ?, NOW())`,
      [id, meetingId, userId],
    );
    const preferAudio = Boolean(opts?.audioOnly || meeting.audioOnly);
    const joinUrl = preferAudio
      ? withAudioOnlyJoinUrl(String(meeting.joinUrl || ''))
      : String(meeting.joinUrl || '');
    return {
      meetingId,
      joined: true,
      joinUrl,
      audioOnly: preferAudio,
      recordingUrl: meeting.recordingUrl || null,
      hostUrl: meeting.createdBy === userId ? meeting.hostUrl : null,
    };
  }

  async leave(tenantId: string, userId: string, meetingId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await runDbQuery(
      ds,
      `UPDATE live_class_attendance SET left_at = NOW()
       WHERE meeting_id = ? AND user_id = ? AND left_at IS NULL`,
      [meetingId, userId],
    );
    return { meetingId, left: true };
  }

  async saveRecording(
    tenantId: string,
    userId: string,
    meetingId: string,
    body: { url?: string; audioOnly?: boolean },
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows = await runDbQuery(
      ds,
      `SELECT id, created_by as "createdBy" FROM virtual_meetings WHERE id = ? LIMIT 1`,
      [meetingId],
    );
    if (!rows.length) throw new NotFoundException('Session not found');
    if (rows[0].createdBy && rows[0].createdBy !== userId) {
      throw new ForbiddenException('Only the host can attach a recording');
    }
    await runDbQuery(
      ds,
      `UPDATE virtual_meetings SET recording_url = ?, audio_only = COALESCE(?, audio_only) WHERE id = ?`,
      [body.url || null, body.audioOnly ?? null, meetingId],
    );
    return { meetingId, recordingUrl: body.url || null };
  }

  private async assertCanAccess(tenantId: string, userId: string, meetingId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, title, starts_at as "startsAt", ends_at as "endsAt",
              provider_id as "providerId", provider_meeting_id as "providerMeetingId",
              join_url as "joinUrl", host_url as "hostUrl", created_by as "createdBy",
              attendee_ids as "attendeeIds", class_id as "classId", subject_id as "subjectId",
              kind, audio_only as "audioOnly", recording_url as "recordingUrl"
       FROM virtual_meetings WHERE id = ? LIMIT 1`,
      [meetingId],
    );
    if (!rows.length) throw new NotFoundException('Session not found');
    const meeting = { ...rows[0], attendeeIds: parseIds(rows[0].attendeeIds) };
    const classIds = await this.classIdsForUser(ds, userId);
    if (!canJoinMeeting(meeting, userId, classIds)) {
      throw new ForbiddenException('You are not in this class');
    }
    const { attendeeIds, ...rest } = meeting;
    return rest;
  }

  private async classIdsForUser(ds: any, userId: string): Promise<Set<string>> {
    const extraClassIds = new Set<string>();
    try {
      const enrolled: any[] = await runDbQuery(
        ds,
        `SELECT class_id as "classId" FROM student_class_enrollments WHERE student_id = ?
         UNION
         SELECT e.class_id as "classId"
         FROM student_class_enrollments e
         JOIN parent_student_links p ON p.student_id = e.student_id
         WHERE p.parent_id = ?
         UNION
         SELECT class_id as "classId" FROM academic_class_teacher_links WHERE teacher_id = ?
         UNION
         SELECT id as "classId" FROM academic_classes WHERE class_teacher_id = ?`,
        [userId, userId, userId, userId],
      );
      for (const row of enrolled) {
        if (row.classId) extraClassIds.add(String(row.classId));
      }
    } catch {
      /* academic tables may not exist yet on a brand-new tenant */
    }
    return extraClassIds;
  }
}

function parseIds(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw !== 'string') return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

export function canViewMeeting(
  meeting: { createdBy?: string; attendeeIds?: string[] },
  userId: string,
): boolean {
  return (
    meeting.createdBy === userId ||
    Boolean(meeting.attendeeIds?.some((attendee) => attendee === userId))
  );
}

export function canJoinMeeting(
  meeting: { createdBy?: string; attendeeIds?: string[]; classId?: string | null },
  userId: string,
  classIds: Set<string>,
): boolean {
  if (canViewMeeting(meeting, userId)) return true;
  return Boolean(meeting.classId && classIds.has(String(meeting.classId)));
}
