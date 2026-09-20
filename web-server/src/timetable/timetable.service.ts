import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

@Injectable()
export class TimetableService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS timetable_slots (
        id varchar(64) PRIMARY KEY,
        class_id varchar(64) NOT NULL,
        subject_id varchar(64) NULL,
        teacher_id varchar(64) NULL,
        day_of_week int NOT NULL,
        start_time varchar(8) NOT NULL,
        end_time varchar(8) NOT NULL,
        room varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async list(tenantId: string, actorId: string, classId?: string) {
    await this.flags.assertEnabled(tenantId, 'academic.timetable');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.getRoles(ds, actorId);
    if (!roles.some((role) => ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff', 'class_teacher', 'subject_teacher', 'parent', 'student'].includes(role))) {
      throw new NotFoundException('Timetable access is restricted');
    }
    const relationship = roles.includes('parent')
      ? `class_id IN (SELECT e.class_id FROM student_class_enrollments e JOIN parent_student_links p ON p.student_id = e.student_id WHERE p.parent_id = ?)`
      : roles.includes('student')
        ? `class_id IN (SELECT class_id FROM student_class_enrollments WHERE student_id = ?)`
        : roles.includes('subject_teacher') || roles.includes('class_teacher')
          ? `teacher_id = ?`
          : null;
    if (classId) {
      return runDbQuery(
        ds,
        `SELECT id, class_id as "classId", subject_id as "subjectId", teacher_id as "teacherId",
                day_of_week as "dayOfWeek", start_time as "startTime", end_time as "endTime", room
         FROM timetable_slots WHERE class_id = ? ${relationship ? `AND ${relationship}` : ''} ORDER BY day_of_week, start_time`,
        relationship ? [classId, actorId] : [classId],
      );
    }
    return runDbQuery(
      ds,
      `SELECT id, class_id as "classId", subject_id as "subjectId", teacher_id as "teacherId",
              day_of_week as "dayOfWeek", start_time as "startTime", end_time as "endTime", room
       FROM timetable_slots ${relationship ? `WHERE ${relationship}` : ''} ORDER BY day_of_week, start_time LIMIT 500`,
      relationship ? [actorId] : [],
    );
  }

  async create(
    tenantId: string,
    actorId: string,
    body: {
      classId: string;
      subjectId?: string;
      teacherId?: string;
      dayOfWeek: number;
      startTime: string;
      endTime: string;
      room?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.timetable');
    if (body.dayOfWeek < 0 || body.dayOfWeek > 6) {
      throw new BadRequestException('dayOfWeek must be 0–6');
    }
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);
    if (!body.classId || !body.startTime || !body.endTime || body.endTime <= body.startTime) {
      throw new BadRequestException('Timetable slot values are invalid');
    }

    // Clash: same class overlapping time, or same teacher overlapping
    const classClash: any[] = await runDbQuery(
      ds,
      `SELECT id FROM timetable_slots
       WHERE class_id = ? AND day_of_week = ?
         AND start_time < ? AND end_time > ? LIMIT 1`,
      [body.classId, body.dayOfWeek, body.endTime, body.startTime],
    );
    if (classClash?.[0]) {
      throw new BadRequestException('Timetable clash: class already has a slot in this window');
    }
    if (body.teacherId) {
      const teacherClash: any[] = await runDbQuery(
        ds,
        `SELECT id FROM timetable_slots
         WHERE teacher_id = ? AND day_of_week = ?
           AND start_time < ? AND end_time > ? LIMIT 1`,
        [body.teacherId, body.dayOfWeek, body.endTime, body.startTime],
      );
      if (teacherClash?.[0]) {
        throw new BadRequestException('Timetable clash: teacher already assigned in this window');
      }
    }

    const id = randomToken('tts');
    await runDbQuery(
      ds,
      `INSERT INTO timetable_slots
        (id, class_id, subject_id, teacher_id, day_of_week, start_time, end_time, room, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW())`,
      [
        id,
        body.classId,
        body.subjectId || null,
        body.teacherId || null,
        body.dayOfWeek,
        body.startTime,
        body.endTime,
        body.room || null,
      ],
    );
    return { id };
  }

  async reschedule(
    tenantId: string,
    actorId: string,
    slotId: string,
    body: { dayOfWeek: number; startTime: string; endTime: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.timetable');
    if (body.dayOfWeek < 0 || body.dayOfWeek > 6) {
      throw new BadRequestException('dayOfWeek must be 0–6');
    }
    if (!body.startTime || !body.endTime || body.endTime <= body.startTime) {
      throw new BadRequestException('Timetable slot times are invalid');
    }
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);

    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, class_id as "classId", teacher_id as "teacherId"
       FROM timetable_slots WHERE id = ? LIMIT 1`,
      [slotId],
    );
    const slot = rows[0];
    if (!slot) {
      throw new NotFoundException('Timetable slot not found');
    }

    const classClash: any[] = await runDbQuery(
      ds,
      `SELECT id FROM timetable_slots
       WHERE class_id = ? AND day_of_week = ? AND id != ?
         AND start_time < ? AND end_time > ? LIMIT 1`,
      [slot.classId, body.dayOfWeek, slotId, body.endTime, body.startTime],
    );
    if (classClash?.[0]) {
      throw new BadRequestException('Timetable clash: class already has a slot in this window');
    }

    if (slot.teacherId) {
      const teacherClash: any[] = await runDbQuery(
        ds,
        `SELECT id FROM timetable_slots
         WHERE teacher_id = ? AND day_of_week = ? AND id != ?
           AND start_time < ? AND end_time > ? LIMIT 1`,
        [slot.teacherId, body.dayOfWeek, slotId, body.endTime, body.startTime],
      );
      if (teacherClash?.[0]) {
        throw new BadRequestException('Timetable clash: teacher already assigned in this window');
      }
    }

    await runDbQuery(
      ds,
      `UPDATE timetable_slots
       SET day_of_week = ?, start_time = ?, end_time = ?
       WHERE id = ?`,
      [body.dayOfWeek, body.startTime, body.endTime, slotId],
    );
    return {
      slotId,
      classId: slot.classId,
      dayOfWeek: body.dayOfWeek,
      startTime: body.startTime,
      endTime: body.endTime,
    };
  }

  async remove(tenantId: string, actorId: string, id: string) {
    await this.flags.assertEnabled(tenantId, 'academic.timetable');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);
    await runDbQuery(ds, `DELETE FROM timetable_slots WHERE id = ?`, [id]);
    return { id, deleted: true };
  }

  private async getRoles(ds: any, actorId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [actorId]);
    const row = rows[0];
    let roles: unknown[] = row?.role ? [row.role] : [];
    if (row?.roles) {
      try { roles = typeof row.roles === 'string' ? JSON.parse(row.roles) : row.roles; } catch { /* fallback */ }
    }
    return Array.isArray(roles) ? roles.map((role) => String(role).toLowerCase()) : [];
  }

  private async assertManager(ds: any, actorId: string) {
    const roles = await this.getRoles(ds, actorId);
    if (!roles.some((role) => ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff'].includes(role))) {
      throw new NotFoundException('Timetable management is restricted');
    }
  }
}
