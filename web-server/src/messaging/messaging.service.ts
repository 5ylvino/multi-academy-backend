import {
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { NotificationsService } from '../notifications/notifications.service';
import { RealtimeService } from '../realtime/realtime.service';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

@Injectable()
export class MessagingService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly notifications: NotificationsService,
    private readonly realtime: RealtimeService,
    private readonly flags: FeatureFlagService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureTables(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS message_threads (
        id varchar(64) PRIMARY KEY,
        subject varchar(255) NOT NULL DEFAULT '',
        participant_ids text NOT NULL DEFAULT '[]',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS messages (
        id varchar(64) PRIMARY KEY,
        thread_id varchar(64) NOT NULL,
        sender_id varchar(64) NOT NULL,
        body text NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(
      `CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (thread_id, created_at)`,
    );
  }

  private async assertMessaging(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'comms.messaging');
  }

  async listThreads(tenantId: string, userId: string) {
    await this.assertMessaging(tenantId);
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, subject, participant_ids as "participantIds", created_by as "createdBy",
              created_at as "createdAt", updated_at as "updatedAt"
       FROM message_threads ORDER BY updated_at DESC LIMIT 100`,
      [],
    );
    return rows
      .map((r) => ({
        ...r,
        participantIds: safeJsonArray(r.participantIds),
      }))
      .filter((r) => r.participantIds.includes(userId));
  }

  /**
   * Parent-safe recipient list. The general users endpoint is intentionally
   * restricted to administrators, so messaging resolves class teachers from
   * the parent's linked children and adds the school's academic heads.
   */
  async listParentRecipients(tenantId: string, parentId: string) {
    await this.assertMessaging(tenantId);
    const ds = await this.getTenantDs(tenantId);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT DISTINCT u.id, u.name, 'class_teacher' as role
       FROM users u
       JOIN academic_classes c ON c.class_teacher_id = u.id
       JOIN student_class_enrollments e ON e.class_id = c.id
       JOIN parent_student_links p ON p.student_id = e.student_id
       WHERE p.parent_id = ? AND c.is_active = true
       UNION
       SELECT DISTINCT u.id, u.name,
              CASE
                WHEN u.roles LIKE '%"principal"%' THEN 'principal'
                ELSE 'academic_head'
              END as role
       FROM users u
       WHERE (
           u.roles LIKE '%"principal"%'
           OR u.roles LIKE '%"head_teacher"%'
           OR u.roles LIKE '%"assistant_head_teacher"%'
         )
         AND EXISTS (
           SELECT 1 FROM parent_student_links linked
           WHERE linked.parent_id = ?
         )
       ORDER BY name ASC`,
      [parentId, parentId],
    );
    return rows.map((row) => ({
      id: String(row.id),
      name: String(row.name || 'School staff'),
      role: String(row.role),
    }));
  }

  async createThread(
    tenantId: string,
    userId: string,
    body: { subject?: string; participantIds: string[]; body?: string },
  ) {
    await this.assertMessaging(tenantId);
    const subject = body.subject?.trim() || 'Conversation';
    const openingMessage = body.body?.trim();
    if (subject.length > 255) {
      throw new ForbiddenException('Conversation subject is too long');
    }
    if (body.body !== undefined && !openingMessage) {
      throw new ForbiddenException('Message body cannot be empty');
    }
    if (openingMessage && openingMessage.length > 5000) {
      throw new ForbiddenException('Message body is too long');
    }
    const participants = Array.from(
      new Set([userId, ...(body.participantIds || [])].filter(Boolean)),
    );
    if (participants.length < 2) {
      throw new ForbiddenException('At least one other participant required');
    }
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertAllowedAudience(ds, userId, participants.slice(1));
    const id = randomToken('thr');
    await runDbQuery(
      ds,
      `INSERT INTO message_threads (id, subject, participant_ids, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        subject,
        JSON.stringify(participants),
        userId,
      ],
    );
    if (openingMessage) {
      await this.postMessage(tenantId, userId, id, openingMessage);
    }
    return { id, participantIds: participants };
  }

  private async assertAllowedAudience(
    ds: any,
    senderId: string,
    participantIds: string[],
  ) {
    const senderRoles = await this.getUserRoles(ds, senderId);
    const people = await Promise.all(
      participantIds.map((id) => this.getUserRoles(ds, id)),
    );
    if (people.some((roles) => roles.length === 0)) {
      throw new NotFoundException('Message recipient not found');
    }
    const allowed = isMessagingAudienceAllowed(senderRoles, people);
    if (!allowed.allowed) throw new ForbiddenException(allowed.reason);
  }

  private async getUserRoles(ds: any, userId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT roles, role FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    if (!rows?.[0]) return [];
    const raw = rows[0].roles;
    if (Array.isArray(raw)) return raw.map(String).map(normalizeRole);
    if (typeof raw === 'string') {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed.map(String).map(normalizeRole);
      } catch {
        return [normalizeRole(raw)];
      }
    }
    return rows[0].role ? [normalizeRole(rows[0].role)] : [];
  }

  async listMessages(tenantId: string, userId: string, threadId: string) {
    await this.assertMessaging(tenantId);
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertParticipant(ds, threadId, userId);
    return runDbQuery(
      ds,
      `SELECT id, thread_id as "threadId", sender_id as "senderId", body, created_at as "createdAt"
       FROM messages WHERE thread_id = ? ORDER BY created_at ASC LIMIT 500`,
      [threadId],
    );
  }

  async postMessage(
    tenantId: string,
    userId: string,
    threadId: string,
    text: string,
  ) {
    await this.assertMessaging(tenantId);
    const message = text?.trim();
    if (!message) throw new ForbiddenException('Message body cannot be empty');
    if (message.length > 5000) {
      throw new ForbiddenException('Message body is too long');
    }
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const thread = await this.assertParticipant(ds, threadId, userId);
    const id = randomToken('msg');
    await runDbQuery(
      ds,
      `INSERT INTO messages (id, thread_id, sender_id, body, created_at) VALUES (?, ?, ?, ?, NOW())`,
      [id, threadId, userId, message],
    );
    await runDbQuery(
      ds,
      `UPDATE message_threads SET updated_at = NOW() WHERE id = ?`,
      [threadId],
    );
    const others = (thread.participantIds as string[]).filter(
      (p) => p !== userId,
    );
    for (const uid of others) {
      await this.notifications.create({
        tenantId,
        userId: uid,
        title: thread.subject || 'New message',
        message: message.slice(0, 160),
        type: 'info',
        href: '/dashboard/messaging',
      });
      this.realtime.notification(tenantId, uid, {
        title: 'New message',
        message: message.slice(0, 120),
        type: 'info',
      });
    }
    this.realtime.notifyTenant(tenantId, 'message:new', {
      threadId,
      messageId: id,
      senderId: userId,
    });
    return { id };
  }

  private async assertParticipant(ds: any, threadId: string, userId: string) {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, subject, participant_ids as "participantIds" FROM message_threads WHERE id = ? LIMIT 1`,
      [threadId],
    );
    const row = rows?.[0];
    if (!row) throw new NotFoundException('Thread not found');
    const participantIds = safeJsonArray(row.participantIds);
    if (!participantIds.includes(userId)) {
      throw new ForbiddenException('Not a participant');
    }
    return { ...row, participantIds };
  }
}

export function isMessagingAudienceAllowed(
  senderRoles: string[],
  participantRoles: string[][],
): { allowed: boolean; reason?: string } {
  const privileged = [
    'director',
    'school_admin',
    'it_admin',
    'administrative_staff',
  ];
  if (senderRoles.some((role) => privileged.includes(role)))
    return { allowed: true };
  if (
    senderRoles.includes('parent') &&
    participantRoles.some(
      (roles) =>
        !roles.some((role) =>
          [
            'class_teacher',
            'subject_teacher',
            'head_teacher',
            'principal',
            ...privileged,
          ].includes(role),
        ),
    )
  ) {
    return { allowed: false, reason: 'Parents may message school staff only' };
  }
  if (
    (senderRoles.includes('class_teacher') ||
      senderRoles.includes('subject_teacher')) &&
    participantRoles.some((roles) => !roles.includes('parent'))
  ) {
    return { allowed: false, reason: 'Teachers may message parents only' };
  }
  return { allowed: true };
}

function normalizeRole(role: string): string {
  return role
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_');
}

function safeJsonArray(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  }
  return [];
}
