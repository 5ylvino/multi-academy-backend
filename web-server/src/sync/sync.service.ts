import { Injectable, ConflictException, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { runDbQuery } from '../database/db-driver.util';
import { randomToken } from '../common/utils/id.util';

export function isSafeSyncOperation(body: { operationId?: string; entity?: string; action?: string; payload?: unknown }) {
  return Boolean(
    body.operationId?.trim() &&
    body.entity?.trim() &&
    ['create', 'update'].includes(body.action || '') &&
    body.payload &&
    typeof body.payload === 'object',
  );
}

@Injectable()
export class SyncService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
  ) {}

  async apply(tenantId: string, userId: string, body: {
    operationId: string;
    entity: string;
    action: 'create' | 'update';
    payload: Record<string, unknown>;
    clientUpdatedAt?: string;
  }) {
    await this.flags.assertEnabled(tenantId, 'comms.offline_sync');
    if (!isSafeSyncOperation(body)) {
      throw new ConflictException('Invalid offline operation');
    }
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    const ds = await this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
    await ds.query(`CREATE TABLE IF NOT EXISTS sync_operations (
      operation_id varchar(128) PRIMARY KEY, user_id varchar(64) NOT NULL,
      entity varchar(128) NOT NULL, action varchar(16) NOT NULL, payload text NOT NULL,
      applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_lesson_notes (
      id varchar(64) PRIMARY KEY, class_id varchar(64) NOT NULL, subject_id varchar(64) NULL,
      term_id varchar(64) NULL, title varchar(255) NOT NULL, content text NOT NULL,
      week_label varchar(64) NULL, created_by varchar(64) NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await ds.query(`CREATE TABLE IF NOT EXISTS timetable_slots (
      id varchar(64) PRIMARY KEY, class_id varchar(64) NOT NULL, subject_id varchar(64) NULL,
      teacher_id varchar(64) NULL, day_of_week int NOT NULL, start_time varchar(8) NOT NULL,
      end_time varchar(8) NOT NULL, room varchar(64) NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    const existing: any[] = await runDbQuery(ds, `SELECT operation_id FROM sync_operations WHERE operation_id = ?`, [body.operationId]);
    if (existing.length) return { operationId: body.operationId, status: 'already_applied' };
    const applied = await this.applySupported(ds, userId, body);
    if (!applied) throw new ConflictException('Unsupported offline entity or invalid payload');
    await runDbQuery(
      ds,
      `INSERT INTO sync_operations (operation_id, user_id, entity, action, payload) VALUES (?, ?, ?, ?, ?)`,
      [body.operationId, userId, body.entity.slice(0, 128), body.action, JSON.stringify(body.payload)],
    );
    return { operationId: body.operationId, status: 'applied', entityId: applied, conflictPolicy: 'server-wins-on-replay' };
  }

  private async applySupported(ds: any, userId: string, body: {
    entity: string; action: 'create' | 'update'; payload: Record<string, unknown>;
  }) {
    const p = body.payload;
    if (body.entity === 'lesson_note') {
      const id = String(p.id || randomToken('lnt'));
      if (body.action === 'create') {
        if (!p.classId || !p.title || !p.content) return null;
        await runDbQuery(ds, `INSERT INTO academic_lesson_notes
          (id, class_id, subject_id, term_id, title, content, week_label, created_by)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
          [id, p.classId, p.subjectId || null, p.termId || null, p.title, p.content, p.weekLabel || null, userId]);
      } else {
        if (!p.id || (!p.title && !p.content && !p.weekLabel)) return null;
        await runDbQuery(ds, `UPDATE academic_lesson_notes SET
          title = COALESCE(?, title), content = COALESCE(?, content),
          week_label = COALESCE(?, week_label), updated_at = NOW()
          WHERE id = ? AND created_by = ?`,
          [p.title || null, p.content || null, p.weekLabel || null, p.id, userId]);
      }
      return id;
    }
    if (body.entity === 'timetable_slot' && body.action === 'create') {
      if (!p.classId || p.dayOfWeek === undefined || !p.startTime || !p.endTime) return null;
      const id = String(p.id || randomToken('tts'));
      await runDbQuery(ds, `INSERT INTO timetable_slots
        (id, class_id, subject_id, teacher_id, day_of_week, start_time, end_time, room)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        [id, p.classId, p.subjectId || null, p.teacherId || userId, p.dayOfWeek, p.startTime, p.endTime, p.room || null]);
      return id;
    }
    return null;
  }
}
