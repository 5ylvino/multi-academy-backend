import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

@Injectable()
export class LessonNotesService {
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

  private async ensureTables(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_lesson_notes (
        id varchar(64) PRIMARY KEY,
        class_id varchar(64) NOT NULL,
        subject_id varchar(64) NULL,
        term_id varchar(64) NULL,
        title varchar(255) NOT NULL,
        content text NOT NULL,
        week_label varchar(64) NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async list(
    tenantId: string,
    actorId: string,
    filters?: { classId?: string; subjectId?: string; termId?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.lesson_notes');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const roles = await this.getRoles(ds, actorId);
    if (!roles.some((role) => ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff', 'class_teacher', 'subject_teacher', 'parent', 'student'].includes(role))) {
      throw new NotFoundException('Lesson-note access is restricted');
    }

    const clauses: string[] = [];
    const params: any[] = [];
    if (filters?.classId) {
      clauses.push('class_id = ?');
      params.push(filters.classId);
    }
    if (filters?.subjectId) {
      clauses.push('subject_id = ?');
      params.push(filters.subjectId);
    }
    if (filters?.termId) {
      clauses.push('term_id = ?');
      params.push(filters.termId);
    }
    const where = clauses.length ? `WHERE ${clauses.join(' AND ')}` : '';

    return runDbQuery(
      ds,
      `SELECT id, class_id as "classId", subject_id as "subjectId", term_id as "termId",
              title, content, week_label as "weekLabel", created_by as "createdBy",
              created_at as "createdAt", updated_at as "updatedAt"
       FROM academic_lesson_notes ${where} ${where ? 'AND' : 'WHERE'} ${
         roles.includes('parent')
           ? 'class_id IN (SELECT e.class_id FROM student_class_enrollments e JOIN parent_student_links p ON p.student_id = e.student_id WHERE p.parent_id = ?)'
           : roles.includes('student')
             ? 'class_id IN (SELECT class_id FROM student_class_enrollments WHERE student_id = ?)'
             : roles.includes('subject_teacher') || roles.includes('class_teacher')
               ? 'created_by = ?'
               : '1 = 1'
       }
       ORDER BY updated_at DESC LIMIT 200`,
      [
        ...params,
        ...(roles.includes('parent') ||
        roles.includes('student') ||
        roles.includes('subject_teacher') ||
        roles.includes('class_teacher')
          ? [actorId]
          : []),
      ],
    );
  }

  async create(
    tenantId: string,
    userId: string,
    body: {
      classId: string;
      subjectId?: string;
      termId?: string;
      title: string;
      content: string;
      weekLabel?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.lesson_notes');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertManager(ds, userId);
    if (!body.classId || !body.title?.trim() || !body.content?.trim()) {
      throw new NotFoundException('Lesson note values are invalid');
    }
    const id = randomToken('lnt');
    await runDbQuery(
      ds,
      `INSERT INTO academic_lesson_notes
        (id, class_id, subject_id, term_id, title, content, week_label, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        body.classId,
        body.subjectId || null,
        body.termId || null,
        body.title,
        body.content,
        body.weekLabel || null,
        userId,
      ],
    );
    return { id };
  }

  async update(
    tenantId: string,
    actorId: string,
    id: string,
    body: Partial<{
      classId: string;
      subjectId: string;
      termId: string;
      title: string;
      content: string;
      weekLabel: string;
    }>,
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.lesson_notes');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertManager(ds, actorId);
    const existing: any[] = await runDbQuery(
      ds,
      `SELECT id FROM academic_lesson_notes WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!existing.length) throw new NotFoundException('Lesson note not found');

    const updates: string[] = [];
    const values: any[] = [];
    const set = (col: string, val: any) => {
      updates.push(`${col} = ?`);
      values.push(val);
    };
    if (body.classId !== undefined) set('class_id', body.classId);
    if (body.subjectId !== undefined) set('subject_id', body.subjectId || null);
    if (body.termId !== undefined) set('term_id', body.termId || null);
    if (body.title !== undefined) set('title', body.title);
    if (body.content !== undefined) set('content', body.content);
    if (body.weekLabel !== undefined) set('week_label', body.weekLabel || null);
    if (!updates.length) return { id };
    updates.push('updated_at = NOW()');
    values.push(id);
    await runDbQuery(
      ds,
      `UPDATE academic_lesson_notes SET ${updates.join(', ')} WHERE id = ?`,
      values,
    );
    return { id };
  }

  async remove(tenantId: string, actorId: string, id: string) {
    await this.flags.assertEnabled(tenantId, 'academic.lesson_notes');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertManager(ds, actorId);
    await runDbQuery(ds, `DELETE FROM academic_lesson_notes WHERE id = ?`, [id]);
    return { id };
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
      throw new NotFoundException('Lesson-note management is restricted');
    }
  }
}
