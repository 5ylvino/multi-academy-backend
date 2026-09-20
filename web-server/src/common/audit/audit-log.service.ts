import { Injectable } from '@nestjs/common';
import { ControlDbService } from '../../database/control-db.service';
import { AuditLogEntity } from '../../control-plane/entities/audit-log.entity';
import { randomUUID } from 'crypto';

const MAX_JSON_BYTES = 8_192;

function sanitizePayload(input: any): any {
  if (!input || typeof input !== 'object') return input;
  const redactions = new Set([
    'password',
    'password_hash',
    'ownerStagedPasswordHash',
    'token',
    'refresh_token',
    'access_token',
    'passportPhoto',
    'passport_photo',
  ]);
  const out: any = Array.isArray(input) ? [] : {};
  for (const [k, v] of Object.entries(input)) {
    if (redactions.has(k)) {
      out[k] = '[redacted]';
    } else if (v && typeof v === 'object') {
      out[k] = sanitizePayload(v);
    } else {
      out[k] = v;
    }
  }
  return out;
}

function compactJson(input: unknown): string | null {
  if (input === undefined || input === null) return null;
  const raw = JSON.stringify(sanitizePayload(input));
  if (!raw) return null;
  if (raw.length <= MAX_JSON_BYTES) return raw;
  return `${raw.slice(0, MAX_JSON_BYTES)}…[truncated]`;
}

@Injectable()
export class AuditLogService {
  constructor(private readonly controlDb: ControlDbService) {}

  async log(entry: {
    userId?: string | null;
    tenantId?: string | null;
    method: string;
    path: string;
    statusCode: number;
    ip?: string | null;
    userAgent?: string | null;
    payload?: any;
    response?: any;
  }): Promise<void> {
    const ds = await this.controlDb.getDataSource();
    const repo = ds.getRepository(AuditLogEntity);
    const rec = repo.create({
      id: randomUUID(),
      userId: entry.userId || null,
      tenantId: entry.tenantId || null,
      method: entry.method.slice(0, 16),
      path: entry.path.slice(0, 255),
      statusCode: entry.statusCode,
      ip: entry.ip ? String(entry.ip).slice(0, 255) : null,
      userAgent: entry.userAgent ? String(entry.userAgent).slice(0, 512) : null,
      payloadJson: compactJson(entry.payload),
      responseJson: compactJson(entry.response),
    });
    await repo.save(rec);
  }

  /**
   * Structured privilege-change audit for HIPAA / compliance.
   * Uses a stable path so audit search can filter on role/capability grants.
   */
  async logPrivilegeChange(entry: {
    actorUserId: string;
    tenantId: string;
    targetUserId: string;
    reason: string;
    before: { roles: string[]; capabilities: string[] };
    after: { roles: string[]; capabilities: string[] };
    ip?: string | null;
    userAgent?: string | null;
  }): Promise<void> {
    const rolesAdded = entry.after.roles.filter((r) => !entry.before.roles.includes(r));
    const rolesRemoved = entry.before.roles.filter((r) => !entry.after.roles.includes(r));
    const capsAdded = entry.after.capabilities.filter((c) => !entry.before.capabilities.includes(c));
    const capsRemoved = entry.before.capabilities.filter((c) => !entry.after.capabilities.includes(c));

    await this.log({
      userId: entry.actorUserId,
      tenantId: entry.tenantId,
      method: 'PRIVILEGE',
      path: `/audit/privilege-change/${entry.targetUserId}`,
      statusCode: 200,
      ip: entry.ip,
      userAgent: entry.userAgent,
      payload: {
        event: 'privilege_change',
        targetUserId: entry.targetUserId,
        reason: entry.reason,
        before: entry.before,
        after: entry.after,
        delta: { rolesAdded, rolesRemoved, capsAdded, capsRemoved },
      },
      response: { recorded: true },
    });
  }
}

