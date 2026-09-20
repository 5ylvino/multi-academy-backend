import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  Logger,
  NotFoundException,
  Optional,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { mapDataToUpdateKeys } from '../util/mapDataToUpdateKeys';
import { GradingMatrixService } from './grading-matrix.service';
import { DEFAULT_GRADING_MATRIX } from './grading-matrix.defaults';
import { NotificationsService } from '../notifications/notifications.service';
import { CommsService } from '../platform-config/comms.service';
import { ListCacheService, stableStringify } from '../common/cache/list-cache.service';
import {
  hasAcademicManagementRole,
  assertTeacherMayAccess as assertTeacherScope,
  getTeacherScope as loadTeacherScope,
} from '../common/auth/teacher-scope.util';

const DEFAULT_CLASSES = [
  ...Array.from({ length: 3 }, (_, i) => [`Nursery ${i + 1}`, `NUR${i + 1}`, 'nursery']),
  ...Array.from({ length: 6 }, (_, i) => [`Primary ${i + 1}`, `PRI${i + 1}`, 'primary']),
  ...Array.from({ length: 3 }, (_, i) => [`JSS ${i + 1}`, `JSS${i + 1}`, 'jss']),
  ...Array.from({ length: 3 }, (_, i) => [`SSS ${i + 1}`, `SSS${i + 1}`, 'sss']),
] as const;
const DEFAULT_CATEGORIES = [
  ['Science', 'science'],
  ['Art', 'art'],
  ['Commercial', 'commercial'],
] as const;
const SCORE_SHEET_READ_ROLES = [
  'subject_teacher',
  'head_teacher',
  'principal',
  'assistant_head_teacher',
] as const;
export function calculateScoreSheetGrade(
  studentScore: number | null | undefined,
  obtainableScore: number,
): number | null {
  if (studentScore === null || studentScore === undefined) return null;
  if (!Number.isFinite(obtainableScore) || obtainableScore <= 0) return null;
  return Math.round((studentScore / obtainableScore) * 100);
}

export function calculatePerformancePercentage(
  studentScore: number | string | null | undefined,
  obtainableScore: number,
): number | null {
  if (studentScore === null || studentScore === undefined) return null;
  if (typeof studentScore === 'string' && studentScore.trim() === '') return null;
  const score = Number(studentScore);
  const obtainable = Number(obtainableScore);
  if (!Number.isFinite(score) || !Number.isFinite(obtainable) || obtainable <= 0) return null;
  return (score / obtainable) * 100;
}

export function isLowPerformance(
  studentScore: number | string | null | undefined,
  obtainableScore: number,
): boolean {
  const percentage = calculatePerformancePercentage(studentScore, obtainableScore);
  return percentage !== null && percentage < 50;
}

export function calculateGeneratedSubjectRecord(
  subject: Record<string, any>,
  historical: Array<Record<string, any> | undefined>,
  termIndex: number,
  bands: Array<{ min: number; max: number; grade_title: string; grade_name: string }>,
) {
  const caScore = Object.entries(subject)
    .filter(([key]) => !['subjectId', 'subjectName', 'totalScore', 'examination'].includes(key))
    .reduce((sum, [, value]) => sum + Number(value || 0), 0);
  const examScore = Number(subject.examination || 0);
  const totalScore = caScore + examScore;
  const firstTermTotal = historical[0]?.totalScore || 0;
  const secondTermTotal = historical[1]?.totalScore || 0;
  const totalAverage = termIndex <= 0
    ? totalScore
    : termIndex === 1
      ? (totalScore + firstTermTotal) / 2
      : (totalScore + firstTermTotal + secondTermTotal) / 3;
  const band = bands.find((item) => totalAverage >= Number(item.min) && totalAverage <= Number(item.max));
  return {
    subjectId: subject.subjectId,
    subject: subject.subjectName,
    caScore,
    examScore,
    totalScore,
    firstTermTotal,
    secondTermTotal,
    totalAverage: Math.round(totalAverage * 100) / 100,
    grade_title: band?.grade_title || 'F',
    grade_name: band?.grade_name || 'Failed',
  };
}

/** Class catalogs are tenant-wide for users with structure-management/read access. */
export function hasUnscopedClassAccess(
  permissions: string[] = [],
  capabilities: string[] = [],
): boolean {
  const grants = new Set([...permissions, ...capabilities]);
  return (
    grants.has('*') ||
    grants.has('classes:manage') ||
    grants.has('organization:view') ||
    grants.has('organization:manage')
  );
}

export function schoolLevelMatches(userLevel: string, itemLevel: unknown): boolean {
  const user = userLevel.toLowerCase();
  const item = String(itemLevel || '').toLowerCase();
  if (user === 'all') return true;
  if (!item) return false;
  if (user === 'primary') return ['primary', 'nursery'].includes(item);
  if (user === 'secondary') return ['secondary', 'jss', 'sss'].includes(item);
  if (item === 'secondary') return ['secondary', 'jss', 'sss'].includes(user);
  return user === item;
}

@Injectable()
export class AcademicService {
  private readonly logger = new Logger(AcademicService.name);
  private readonly readyDataSources = new WeakSet<object>();

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly listCache: ListCacheService,
    @Optional() private readonly gradingMatrix?: GradingMatrixService,
    @Optional() private readonly notifications?: NotificationsService,
    @Optional() private readonly comms?: CommsService,
  ) {}

  private async getActorSchoolLevel(ds: any, actorUserId?: string): Promise<string | null> {
    if (!actorUserId) return null;
    let rows: any[] = [];
    try {
      rows = await runDbQuery(
        ds,
        `SELECT school_level as "schoolLevel", roles FROM users WHERE id = ? LIMIT 1`,
        [actorUserId],
      );
    } catch {
      // Older tenant databases may not have run the additive migration yet.
      return null;
    }
    const row = rows?.[0] || {};
    const level = row.schoolLevel ?? row.school_level;
    const roles = Array.isArray(row.roles)
      ? row.roles
      : JSON.parse(row.roles || '[]');
    // Tenant-wide hierarchy must not be narrowed by a lower role's
    // school-level assignment when a user holds multiple roles.
    if (roles.some((role: string) => ['director', 'school_admin', 'it_admin'].includes(role))) {
      return 'all';
    }
    if (level) return String(level).toLowerCase();
    if (roles.includes('head_teacher') || roles.includes('assistant_head_teacher')) {
      return 'primary';
    }
    if (roles.includes('principal') || roles.includes('vice_principal')) {
      return 'secondary';
    }
    return 'all';
  }

  private async filterByActorSchoolLevel(ds: any, actorUserId: string | undefined, rows: any[]) {
    const level = await this.getActorSchoolLevel(ds, actorUserId);
    return level ? rows.filter((row) => schoolLevelMatches(level, row.schoolLevel ?? row.schoollevel)) : rows;
  }

  private async assertActorSchoolLevel(
    ds: any,
    actorUserId: string | undefined,
    classId?: string,
    subjectId?: string,
  ) {
    const level = await this.getActorSchoolLevel(ds, actorUserId);
    if (!level || (!classId && !subjectId)) return;
    const classRows = classId
      ? await runDbQuery(ds, `SELECT school_level as "schoolLevel" FROM academic_classes WHERE id = ? LIMIT 1`, [classId])
      : [];
    const subjectRows = subjectId
      ? await runDbQuery(ds, `SELECT school_level as "schoolLevel" FROM academic_subjects WHERE id = ? LIMIT 1`, [subjectId])
      : [];
    if ((classId && (!classRows[0] || !schoolLevelMatches(level, classRows[0].schoolLevel))) ||
        (subjectId && (!subjectRows[0] || !schoolLevelMatches(level, subjectRows[0].schoolLevel)))) {
      throw new ForbiddenException('User is not assigned to this school level');
    }
  }

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    // Intentionally omit dbUri: it contains credentials in production.
    this.logger.debug(`Academic tenant route tenant=${tenant.id} db=${tenant.dbName}`);
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureTables(ds: any) {
    if (this.readyDataSources.has(ds)) return;
    await this.ensureTablesUncached(ds);
    this.readyDataSources.add(ds);
  }

  private async ensureTablesUncached(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_classes (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        code varchar(64) NOT NULL,
        school_level varchar(32) NOT NULL,
        class_teacher_id varchar(64) NULL,
        capacity int NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_subjects (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        code varchar(64) NOT NULL,
        school_level varchar(32) NOT NULL,
        description text NULL,
        class_ids TEXT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_assignments (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        description text NULL,
        class_id varchar(64) NULL,
        subject_id varchar(64) NULL,
        assigned_by varchar(64) NULL,
        due_date varchar(32) NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_assignment_submissions (
        id varchar(64) PRIMARY KEY,
        assignment_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'submitted',
        score decimal(10,2) NULL,
        submitted_at TIMESTAMP NULL,
        note text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (assignment_id, student_id)
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_results (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NULL,
        class_id varchar(64) NOT NULL,
        subject_id varchar(64) NOT NULL,
        term_id varchar(64) NOT NULL,
        ca_score decimal(10,2) NULL,
        exam_score decimal(10,2) NULL,
        total_score decimal(10,2) NULL,
        grade varchar(8) NULL,
        status varchar(32) NOT NULL DEFAULT 'draft',
        approval_comment text NULL,
        approved_by varchar(64) NULL,
        approved_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_generated_results (
        id varchar(64) PRIMARY KEY, session_id varchar(64) NOT NULL, term_id varchar(64) NOT NULL,
        class_id varchar(64) NOT NULL, student_id varchar(64) NOT NULL,
        student_school_level varchar(32) NULL, student_academic_session varchar(128) NULL,
        student_term varchar(128) NULL, student_gender varchar(16) NULL,
        student_name varchar(255) NOT NULL, student_class_name varchar(255) NULL,
        student_class_code varchar(64) NULL, student_class_teacher_id varchar(64) NULL,
        student_class_teacher_name varchar(255) NULL, no_of_times_school_opened int NOT NULL DEFAULT 0,
        admission_no varchar(128) NULL, no_of_times_present int NOT NULL DEFAULT 0,
        term_ended varchar(64) NULL, no_of_times_absent int NOT NULL DEFAULT 0,
        next_term_begins varchar(64) NULL, student_subject_records text NOT NULL DEFAULT '[]',
        release_status varchar(32) NOT NULL DEFAULT 'pending', approved_by varchar(255) NULL,
        approved_at TIMESTAMP NULL, generated_by varchar(255) NOT NULL,
        generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (session_id, term_id, class_id, student_id)
      );
    `);
    await ds.query(
      `ALTER TABLE academic_results ADD COLUMN IF NOT EXISTS approval_comment text NULL`,
    );
    await ds.query(
      `ALTER TABLE academic_results ADD COLUMN IF NOT EXISTS approved_by varchar(64) NULL`,
    );
    await ds.query(
      `ALTER TABLE academic_results ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP NULL`,
    );
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_class_teacher_links (
        id varchar(64) PRIMARY KEY,
        class_id varchar(64) NOT NULL,
        teacher_id varchar(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (class_id, teacher_id)
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_subject_teacher_links (
        id varchar(64) PRIMARY KEY,
        subject_id varchar(64) NOT NULL,
        teacher_id varchar(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (subject_id, teacher_id)
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS parent_student_links (
        id varchar(64) PRIMARY KEY,
        parent_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (parent_id, student_id)
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS student_class_enrollments (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        class_id varchar(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (student_id, class_id)
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_sessions (
        id varchar(64) PRIMARY KEY,
        name varchar(128) NOT NULL,
        start_date varchar(32) NULL,
        end_date varchar(32) NULL,
        is_current boolean NOT NULL DEFAULT false,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_terms (
        id varchar(64) PRIMARY KEY,
        session_id varchar(64) NOT NULL,
        name varchar(64) NOT NULL,
        code varchar(32) NOT NULL,
        start_date varchar(32) NULL,
        end_date varchar(32) NULL,
        is_current boolean NOT NULL DEFAULT false,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_subject_categories (
      id varchar(64) PRIMARY KEY, name varchar(128) NOT NULL, slug varchar(128) NOT NULL UNIQUE,
      is_active boolean NOT NULL DEFAULT true, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_score_categories (
      id varchar(64) PRIMARY KEY, name varchar(128) NOT NULL, slug varchar(128) NOT NULL UNIQUE,
      is_active boolean NOT NULL DEFAULT true, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_score_subcategories (
      id varchar(64) PRIMARY KEY, category_id varchar(64) NOT NULL, name varchar(128) NOT NULL,
      slug varchar(128) NOT NULL, is_active boolean NOT NULL DEFAULT true,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE (category_id, slug)
    )`);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_score_sheets (
      id varchar(64) PRIMARY KEY, session_id varchar(64) NOT NULL, term_id varchar(64) NOT NULL,
      class_id varchar(64) NOT NULL, student_id varchar(64) NOT NULL, subject_id varchar(64) NOT NULL,
      category_id varchar(64) NOT NULL, subcategory_id varchar(64) NULL, student_score decimal(10,2) NULL,
      obtainable_score decimal(10,2) NOT NULL, grade int NULL, status varchar(16) NOT NULL DEFAULT 'draft',
      recorder_user_id varchar(64) NOT NULL, recorded_by varchar(255) NOT NULL,
      recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await ds.query(
      `CREATE INDEX IF NOT EXISTS idx_score_sheets_scope
       ON academic_score_sheets (class_id, subject_id, session_id, term_id)`,
    );
    await ds.query(`ALTER TABLE academic_subjects ADD COLUMN IF NOT EXISTS category_id varchar(64) NULL`);
    await ds.query(`ALTER TABLE academic_subjects ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`);
    await ds.query(`ALTER TABLE academic_classes ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_student_history (
      id varchar(64) PRIMARY KEY, student_id varchar(64) NOT NULL, session_id varchar(64) NULL,
      term_id varchar(64) NULL, class_id varchar(64) NULL, event_type varchar(64) NOT NULL,
      payload text NOT NULL DEFAULT '{}', recorded_by varchar(64) NULL,
      recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_student_promotions (
      id varchar(64) PRIMARY KEY, student_id varchar(64) NOT NULL, from_class_id varchar(64) NULL,
      to_class_id varchar(64) NOT NULL, session_id varchar(64) NULL, promoted_by varchar(64) NULL,
      notes text NULL, promoted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_schemes_of_work (
      id varchar(64) PRIMARY KEY, class_id varchar(64) NOT NULL, subject_id varchar(64) NOT NULL,
      session_id varchar(64) NOT NULL, term_id varchar(64) NOT NULL, title varchar(255) NULL,
      created_by varchar(64) NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE (class_id, subject_id, term_id)
    )`);
    await ds.query(`CREATE TABLE IF NOT EXISTS academic_scheme_topics (
      id varchar(64) PRIMARY KEY, scheme_id varchar(64) NOT NULL, title varchar(255) NOT NULL,
      timeframe varchar(128) NULL, objective text NULL, activities text NULL, resources text NULL,
      assessment text NULL, order_index int NOT NULL DEFAULT 0, status varchar(32) NOT NULL DEFAULT 'planned',
      covered_before_assessment boolean NOT NULL DEFAULT false,
      covered_before_examination boolean NOT NULL DEFAULT false, carry_over_reason text NULL,
      is_next boolean NOT NULL DEFAULT false,
      updated_by varchar(64) NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await ds.query(
      `ALTER TABLE academic_scheme_topics ADD COLUMN IF NOT EXISTS is_next boolean NOT NULL DEFAULT false`,
    );
  }

  private mapSessionRow(r: any) {
    return mapDataToUpdateKeys(r, {
      startdate: 'startDate',
      enddate: 'endDate',
      iscurrent: 'isCurrent',
      createdat: 'createdAt',
      updatedat: 'updatedAt',
    });
  }

  private mapTermRow(r: any) {
    return mapDataToUpdateKeys(r, {
      sessionid: 'sessionId',
      startdate: 'startDate',
      enddate: 'endDate',
      iscurrent: 'isCurrent',
      createdat: 'createdAt',
      updatedat: 'updatedAt',
    });
  }

  async getDefaultStructureStatus(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const classes: any[] = await runDbQuery(
      ds,
      'SELECT code, school_level as "schoolLevel" FROM academic_classes WHERE code = ANY(?)',
      [DEFAULT_CLASSES.map(([, code]) => code)],
    );
    const categories: any[] = await runDbQuery(
      ds,
      'SELECT slug FROM academic_subject_categories WHERE slug = ANY(?)',
      [DEFAULT_CATEGORIES.map(([, slug]) => slug)],
    );
    const classCodes = new Set(classes.map((row) => row.code));
    const categorySlugs = new Set(categories.map((row) => row.slug));
    return {
      ready:
        classCodes.size === DEFAULT_CLASSES.length &&
        categorySlugs.size === DEFAULT_CATEGORIES.length,
      classes: {
        expected: DEFAULT_CLASSES.length,
        present: classCodes.size,
        missing: DEFAULT_CLASSES.filter(([, code]) => !classCodes.has(code)).map(([, code]) => code),
      },
      subjectCategories: {
        expected: DEFAULT_CATEGORIES.length,
        present: categorySlugs.size,
        missing: DEFAULT_CATEGORIES.filter(([, slug]) => !categorySlugs.has(slug)).map(([, slug]) => slug),
      },
    };
  }

  async repairDefaultStructure(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    for (const [name, slug] of DEFAULT_CATEGORIES) {
      await runDbQuery(
        ds,
        `INSERT INTO academic_subject_categories (id, name, slug)
         VALUES (?, ?, ?) ON CONFLICT (slug) DO UPDATE SET is_active = true, name = EXCLUDED.name`,
        [randomToken('cat'), name, slug],
      );
    }
    for (const [name, code, schoolLevel] of DEFAULT_CLASSES) {
      const existing = await runDbQuery(
        ds,
        'SELECT id FROM academic_classes WHERE code = ? LIMIT 1',
        [code],
      );
      if (!existing[0]) {
        await runDbQuery(
          ds,
          `INSERT INTO academic_classes (id, name, code, school_level, is_active)
           VALUES (?, ?, ?, ?, true)`,
          [randomToken('cls'), name, code, schoolLevel],
        );
      } else {
        await runDbQuery(
          ds,
          `UPDATE academic_classes SET is_active = true, archived_at = NULL, updated_at = NOW()
           WHERE code = ?`,
          [code],
        );
      }
    }
    return this.getDefaultStructureStatus(tenantId);
  }

  async listSessions(tenantId: string, force = false) {
    return this.listCache.getOrLoad(`tenant:${tenantId}:academic:sessions`, async () => {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await ds.query(
      `SELECT id, name, start_date as startDate, end_date as endDate, is_current as isCurrent,
              created_at as createdAt, updated_at as updatedAt
       FROM academic_sessions ORDER BY created_at DESC`,
    );
    return rows.map((r: any) => this.mapSessionRow(r));
    }, force);
  }

  async createSession(tenantId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const id = randomToken('sess');
    const isCurrent = !!body.isCurrent;
    if (isCurrent) {
      await runDbQuery(
        ds,
        `UPDATE academic_sessions SET is_current = false, updated_at = NOW()`,
      );
    }
    await runDbQuery(
      ds,
      `INSERT INTO academic_sessions (id, name, start_date, end_date, is_current, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, NOW(), NOW())`,
      [id, body.name, body.startDate || null, body.endDate || null, isCurrent],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:sessions`);
    return { id };
  }

  async updateSession(tenantId: string, id: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing = await this.getRowOrThrow(
      ds,
      'academic_sessions',
      id,
      'Session not found',
    );
    const merged = {
      name: body.name ?? existing.name,
      startDate:
        body.startDate !== undefined ? body.startDate : existing.start_date,
      endDate: body.endDate !== undefined ? body.endDate : existing.end_date,
      isCurrent:
        body.isCurrent !== undefined ? !!body.isCurrent : existing.is_current,
    };
    if (merged.isCurrent) {
      await runDbQuery(
        ds,
        `UPDATE academic_sessions SET is_current = false, updated_at = NOW() WHERE id <> ?`,
        [id],
      );
    }
    await runDbQuery(
      ds,
      `UPDATE academic_sessions SET name = ?, start_date = ?, end_date = ?, is_current = ?, updated_at = NOW() WHERE id = ?`,
      [
        merged.name,
        merged.startDate || null,
        merged.endDate || null,
        merged.isCurrent,
        id,
      ],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:sessions`);
    return { id };
  }

  async deleteSession(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.getRowOrThrow(ds, 'academic_sessions', id, 'Session not found');
    await runDbQuery(ds, `DELETE FROM academic_terms WHERE session_id = ?`, [
      id,
    ]);
    await runDbQuery(ds, `DELETE FROM academic_sessions WHERE id = ?`, [id]);
    this.listCache.invalidate(`tenant:${tenantId}:academic:sessions`);
    return { id };
  }

  async setCurrentSession(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.getRowOrThrow(ds, 'academic_sessions', id, 'Session not found');
    await runDbQuery(
      ds,
      `UPDATE academic_sessions SET is_current = false, updated_at = NOW()`,
    );
    await runDbQuery(
      ds,
      `UPDATE academic_sessions SET is_current = true, updated_at = NOW() WHERE id = ?`,
      [id],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:sessions`);
    return { id, isCurrent: true };
  }

  async listTerms(tenantId: string, sessionId?: string, force = false) {
    return this.listCache.getOrLoad(
      `tenant:${tenantId}:academic:terms:${sessionId || 'all'}`,
      async () => {
    const ds = await this.getTenantDs(tenantId);
    const rows = sessionId
      ? await runDbQuery(
          ds,
          `SELECT id, session_id as sessionId, name, code, start_date as startDate, end_date as endDate,
                  is_current as isCurrent, created_at as createdAt, updated_at as updatedAt
           FROM academic_terms WHERE session_id = ? ORDER BY created_at ASC`,
          [sessionId],
        )
      : await ds.query(
          `SELECT id, session_id as sessionId, name, code, start_date as startDate, end_date as endDate,
                  is_current as isCurrent, created_at as createdAt, updated_at as updatedAt
           FROM academic_terms ORDER BY created_at ASC`,
        );
    return (rows || []).map((r: any) => this.mapTermRow(r));
      },
      force,
    );
  }

  async createTerm(tenantId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.getRowOrThrow(
      ds,
      'academic_sessions',
      body.sessionId,
      'Session not found',
    );
    const id = randomToken('term');
    const isCurrent = !!body.isCurrent;
    if (isCurrent) {
      await runDbQuery(
        ds,
        `UPDATE academic_terms SET is_current = false, updated_at = NOW()`,
      );
    }
    await runDbQuery(
      ds,
      `INSERT INTO academic_terms (id, session_id, name, code, start_date, end_date, is_current, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        body.sessionId,
        body.name,
        body.code,
        body.startDate || null,
        body.endDate || null,
        isCurrent,
      ],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:terms:`);
    return { id };
  }

  async updateTerm(tenantId: string, id: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing = await this.getRowOrThrow(
      ds,
      'academic_terms',
      id,
      'Term not found',
    );
    if (body.sessionId) {
      await this.getRowOrThrow(
        ds,
        'academic_sessions',
        body.sessionId,
        'Session not found',
      );
    }
    const merged = {
      sessionId: body.sessionId ?? existing.session_id,
      name: body.name ?? existing.name,
      code: body.code ?? existing.code,
      startDate:
        body.startDate !== undefined ? body.startDate : existing.start_date,
      endDate: body.endDate !== undefined ? body.endDate : existing.end_date,
      isCurrent:
        body.isCurrent !== undefined ? !!body.isCurrent : existing.is_current,
    };
    if (merged.isCurrent) {
      await runDbQuery(
        ds,
        `UPDATE academic_terms SET is_current = false, updated_at = NOW() WHERE id <> ?`,
        [id],
      );
    }
    await runDbQuery(
      ds,
      `UPDATE academic_terms SET session_id = ?, name = ?, code = ?, start_date = ?, end_date = ?, is_current = ?, updated_at = NOW() WHERE id = ?`,
      [
        merged.sessionId,
        merged.name,
        merged.code,
        merged.startDate || null,
        merged.endDate || null,
        merged.isCurrent,
        id,
      ],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:terms:`);
    return { id };
  }

  async deleteTerm(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.getRowOrThrow(ds, 'academic_terms', id, 'Term not found');
    await runDbQuery(ds, `DELETE FROM academic_terms WHERE id = ?`, [id]);
    this.listCache.invalidate(`tenant:${tenantId}:academic:terms:`);
    return { id };
  }

  async setCurrentTerm(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const term = await this.getRowOrThrow(
      ds,
      'academic_terms',
      id,
      'Term not found',
    );
    await runDbQuery(
      ds,
      `UPDATE academic_terms SET is_current = false, updated_at = NOW()`,
    );
    await runDbQuery(
      ds,
      `UPDATE academic_terms SET is_current = true, updated_at = NOW() WHERE id = ?`,
      [id],
    );
    // Keep session current in sync with the selected term's session
    await runDbQuery(
      ds,
      `UPDATE academic_sessions SET is_current = false, updated_at = NOW()`,
    );
    await runDbQuery(
      ds,
      `UPDATE academic_sessions SET is_current = true, updated_at = NOW() WHERE id = ?`,
      [term.session_id],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:terms:`);
    this.listCache.invalidate(`tenant:${tenantId}:academic:sessions`);
    return { id, isCurrent: true };
  }

  private async seedDefaultCalendar(ds: any) {
    const now = new Date();
    const month = now.getMonth() + 1; // 1-12
    const year = now.getFullYear();
    // School year typically starts around July/August
    const startYear = month >= 7 ? year : year - 1;
    const sessionName = `${startYear}/${startYear + 1}`;
    const sessionId = randomToken('sess');

    let currentTermCode: 'first' | 'second' | 'third' = 'first';
    if (month >= 1 && month <= 3) currentTermCode = 'second';
    else if (month >= 4 && month <= 7) currentTermCode = 'third';
    else currentTermCode = 'first'; // Aug–Dec

    await runDbQuery(
      ds,
      `INSERT INTO academic_sessions (id, name, start_date, end_date, is_current, created_at, updated_at)
       VALUES (?, ?, ?, ?, true, NOW(), NOW())`,
      [sessionId, sessionName, `${startYear}-09-01`, `${startYear + 1}-07-31`],
    );

    const terms = [
      {
        name: 'First Term',
        code: 'first',
        start: `${startYear}-09-01`,
        end: `${startYear}-12-20`,
      },
      {
        name: 'Second Term',
        code: 'second',
        start: `${startYear + 1}-01-08`,
        end: `${startYear + 1}-04-10`,
      },
      {
        name: 'Third Term',
        code: 'third',
        start: `${startYear + 1}-04-20`,
        end: `${startYear + 1}-07-31`,
      },
    ];
    for (const t of terms) {
      await runDbQuery(
        ds,
        `INSERT INTO academic_terms (id, session_id, name, code, start_date, end_date, is_current, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
        [
          randomToken('term'),
          sessionId,
          t.name,
          t.code,
          t.start,
          t.end,
          t.code === currentTermCode,
        ],
      );
    }
  }

  async getCalendar(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    let sessions = await this.listSessions(tenantId);
    let calendarWasSeeded = false;
    if (!sessions.length) {
      await this.seedDefaultCalendar(ds);
      calendarWasSeeded = true;
      sessions = await this.listSessions(tenantId, true);
    }
    const terms = await this.listTerms(tenantId, undefined, calendarWasSeeded);
    const currentSession = sessions.find((s: any) => s.isCurrent) || null;
    const currentTerm = terms.find((t: any) => t.isCurrent) || null;
    return { sessions, terms, currentSession, currentTerm };
  }

  async listClasses(
    tenantId: string,
    actorUserId?: string,
    scopeToTeacher = true,
    force = false,
  ) {
    return this.listCache.getOrLoad(
      `tenant:${tenantId}:academic:classes:${actorUserId || 'all'}:${scopeToTeacher}`,
      async () => {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await ds.query(
      `SELECT c.id,
              c.name,
              c.code,
              c.school_level as schoolLevel,
              c.class_teacher_id as classTeacherId,
              uct.name as classTeacherName,
              c.capacity,
              c.is_active as isActive,
              c.created_at as createdAt,
              c.updated_at as updatedAt,
              COALESCE(ct.assign_count, 0) as teacherAssignedCount,
              COALESCE(ct.teacher_names, ARRAY[]::text[]) as assignedTeacherNames,
              COALESCE(en.enrollment_count, 0) as currentEnrollment
       FROM academic_classes c
       LEFT JOIN users uct ON uct.id = c.class_teacher_id
       LEFT JOIN (
         SELECT ctl.class_id,
                COUNT(*)::int as assign_count,
                array_agg(u.name ORDER BY u.name) as teacher_names
         FROM academic_class_teacher_links ctl
         JOIN users u ON u.id = ctl.teacher_id
         GROUP BY ctl.class_id
       ) ct ON ct.class_id = c.id
       LEFT JOIN (
         SELECT class_id, COUNT(DISTINCT student_id)::int as enrollment_count
         FROM (
           SELECT class_id, student_id FROM student_class_enrollments
           UNION
           SELECT class_id, student_id FROM academic_results WHERE student_id IS NOT NULL
         ) enrolled
         GROUP BY class_id
       ) en ON en.class_id = c.id
       ORDER BY c.created_at DESC`,
    );
    const mapped = mapDataToUpdateKeys(rows, {
      isactive: 'isActive',
      schoollevel: 'schoolLevel',
      classteacherid: 'classTeacherId',
      classteachername: 'classTeacherName',
      createdat: 'createdAt',
      updatedat: 'updatedAt',
      teacherassignedcount: 'teacherAssignedCount',
      assignedteachernames: 'assignedTeacherNames',
      currentenrollment: 'currentEnrollment',
    });
    const levelScoped = await this.filterByActorSchoolLevel(ds, actorUserId, mapped);
    if (!actorUserId || !scopeToTeacher) {
      this.logger.debug(
        `Academic classes tenant=${tenantId} rows=${mapped.length} scope=tenant`,
      );
      return scopeToTeacher ? levelScoped : mapped;
    }
    const scope = await this.getTeacherScope(ds, actorUserId);
    if (!scope.isTeacher) {
      this.logger.debug(
        `Academic classes tenant=${tenantId} rows=${mapped.length} actor=${actorUserId} scope=tenant`,
      );
      return mapped;
    }
    const scoped = levelScoped.filter(
      (row: any) =>
        scope.classIds.includes(String(row.id)) ||
        (scope.subjectIds.length > 0 &&
          scope.subjectClassIds.some(
            (classId) => String(classId) === String(row.id),
          )),
    );
    this.logger.debug(
      `Academic classes tenant=${tenantId} rows=${mapped.length} scopedRows=${scoped.length} actor=${actorUserId} scope=teacher`,
    );
    return scoped;
      },
      force,
    );
  }

  async upsertClass(tenantId: string, id: string | null, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    if (!id) {
      const classId = randomToken('cls');
      await runDbQuery(
        ds,
        `INSERT INTO academic_classes (id, name, code, school_level, class_teacher_id, capacity, is_active, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
        [
          classId,
          body.name,
          body.code,
          body.schoolLevel,
          body.classTeacherId || null,
          body.capacity || null,
          body.isActive !== false,
        ],
      );
      this.listCache.invalidate(`tenant:${tenantId}:academic:classes:`);
      return { id: classId };
    }
    const existing = await this.getRowOrThrow(
      ds,
      'academic_classes',
      id,
      'Class not found',
    );
    // Merge so partial PATCH bodies don't wipe unset columns.
    const merged = {
      name: body.name ?? existing.name,
      code: body.code ?? existing.code,
      schoolLevel: body.schoolLevel ?? existing.school_level,
      classTeacherId:
        body.classTeacherId !== undefined
          ? body.classTeacherId
          : existing.class_teacher_id,
      capacity: body.capacity !== undefined ? body.capacity : existing.capacity,
      isActive:
        body.isActive !== undefined ? !!body.isActive : existing.is_active,
    };
    await runDbQuery(
      ds,
      `UPDATE academic_classes SET name = ?, code = ?, school_level = ?, class_teacher_id = ?, capacity = ?, is_active = ?, updated_at = NOW() WHERE id = ?`,
      [
        merged.name,
        merged.code,
        merged.schoolLevel,
        merged.classTeacherId || null,
        merged.capacity || null,
        merged.isActive,
        id,
      ],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:classes:`);
    return { id };
  }

  async deleteClass(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(ds, `UPDATE academic_classes SET is_active = false, archived_at = NOW(), updated_at = NOW() WHERE id = ?`, [id]);
    this.listCache.invalidate(`tenant:${tenantId}:academic:classes:`);
    return { id };
  }

  async listSubjects(
    tenantId: string,
    actorUserId?: string,
    scopeToTeacher = true,
    force = false,
  ) {
    return this.listCache.getOrLoad(
      `tenant:${tenantId}:academic:subjects:${actorUserId || 'all'}:${scopeToTeacher}`,
      async () => {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await ds.query(
      `SELECT s.id,
              s.name,
              s.code,
              s.school_level as schoolLevel,
              s.description,
              s.category_id as categoryId,
              cat.name as categoryName,
              s.class_ids as classIds,
              s.is_active as isActive,
              COALESCE(st.assign_count, 0) as teacherAssignedCount,
              COALESCE(st.teacher_names, ARRAY[]::text[]) as assignedTeacherNames,
              COALESCE(lc.class_names, ARRAY[]::text[]) as linkedClassNames
       FROM academic_subjects s
       LEFT JOIN academic_subject_categories cat ON cat.id = s.category_id
       LEFT JOIN (
         SELECT stl.subject_id,
                COUNT(*)::int as assign_count,
                array_agg(u.name ORDER BY u.name) as teacher_names
         FROM academic_subject_teacher_links stl
         JOIN users u ON u.id = stl.teacher_id
         GROUP BY stl.subject_id
       ) st ON st.subject_id = s.id
       LEFT JOIN LATERAL (
         SELECT array_agg(c2.name ORDER BY c2.name) as class_names
         FROM academic_classes c2
         WHERE s.class_ids IS NOT NULL
           AND c2.id IN (
             SELECT jsonb_array_elements_text(
               CASE
                 WHEN jsonb_typeof(s.class_ids::jsonb) = 'array' THEN s.class_ids::jsonb
                 ELSE '[]'::jsonb
               END
             )
           )
       ) lc ON TRUE
       ORDER BY s.created_at DESC`,
    );
    const normalized = mapDataToUpdateKeys(rows, {
      isactive: 'isActive',
      schoollevel: 'schoolLevel',
      classids: 'classIds',
      categoryid: 'categoryId',
      categoryname: 'categoryName',
      teacherassignedcount: 'teacherAssignedCount',
      assignedteachernames: 'assignedTeacherNames',
      linkedclassnames: 'linkedClassNames',
    });
    const mapped = normalized.map((r: any) => ({
      ...r,
      classIds: (() => {
        try {
          if (Array.isArray(r.classIds)) return r.classIds;
          if (typeof r.classIds === 'string')
            return JSON.parse(r.classIds || '[]');
          return [];
        } catch {
          return [];
        }
      })(),
    }));
    const levelScoped = await this.filterByActorSchoolLevel(ds, actorUserId, mapped);
    if (!actorUserId || !scopeToTeacher) return scopeToTeacher ? levelScoped : mapped;
    const scope = await loadTeacherScope(ds, actorUserId);
    if (!scope.isTeacher) return levelScoped;
    return levelScoped.filter((row: any) =>
      scope.subjectIds.includes(String(row.id)),
    );
      },
      force,
    );
  }

  async listSubjectCategories(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    return runDbQuery(ds, `SELECT id, name, slug, is_active as "isActive"
      FROM academic_subject_categories WHERE is_active = true ORDER BY name ASC`);
  }

  async createSubjectCategory(tenantId: string, body: { name: string; slug: string }) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const id = randomToken('cat');
    await runDbQuery(ds, `INSERT INTO academic_subject_categories (id, name, slug)
      VALUES (?, ?, ?)`, [id, body.name.trim(), body.slug.trim().toLowerCase()]);
    return { id, name: body.name.trim(), slug: body.slug.trim().toLowerCase() };
  }

  async upsertSubject(tenantId: string, id: string | null, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    if (!id) {
      const subjectId = randomToken('subj');
      await runDbQuery(
        ds,
        `INSERT INTO academic_subjects (id, name, code, school_level, description, category_id, class_ids, is_active, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
        [
          subjectId,
          body.name,
          body.code,
          body.schoolLevel,
          body.description || null,
          body.categoryId || null,
          JSON.stringify(body.classIds || []),
          body.isActive !== false,
        ],
      );
      this.listCache.invalidate(`tenant:${tenantId}:academic:subjects:`);
      return { id: subjectId };
    }
    const existing = await this.getRowOrThrow(
      ds,
      'academic_subjects',
      id,
      'Subject not found',
    );
    const merged = {
      name: body.name ?? existing.name,
      code: body.code ?? existing.code,
      schoolLevel: body.schoolLevel ?? existing.school_level,
      description:
        body.description !== undefined
          ? body.description
          : existing.description,
      categoryId:
        body.categoryId !== undefined ? body.categoryId : existing.category_id,
      classIds:
        body.classIds !== undefined
          ? JSON.stringify(body.classIds || [])
          : existing.class_ids,
      isActive:
        body.isActive !== undefined ? !!body.isActive : existing.is_active,
    };
    await runDbQuery(
      ds,
      `UPDATE academic_subjects SET name = ?, code = ?, school_level = ?, description = ?, category_id = ?, class_ids = ?, is_active = ?, updated_at = NOW() WHERE id = ?`,
      [
        merged.name,
        merged.code,
        merged.schoolLevel,
        merged.description || null,
        merged.categoryId || null,
        merged.classIds,
        merged.isActive,
        id,
      ],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:subjects:`);
    return { id };
  }

  async deleteSubject(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(ds, `UPDATE academic_subjects SET is_active = false, archived_at = NOW(), updated_at = NOW() WHERE id = ?`, [id]);
    return { id };
  }

  private async assertSchemeManager(ds: any, userId: string) {
    const roles = await this.getUserRoles(ds, userId);
    if (!hasAcademicManagementRole(roles)) {
      throw new ForbiddenException('Only the head teacher or principal may manage schemes of work');
    }
  }

  private async schemeVisibility(
    ds: any,
    userId: string,
  ): Promise<{ clause: string; params: string[] }> {
    const roles = await this.getUserRoles(ds, userId);
    if (hasAcademicManagementRole(roles)) {
      return { clause: '1 = 1', params: [] };
    }
    if (roles.includes('subject_teacher') || roles.includes('class_teacher')) {
      const scope = await this.getTeacherScope(ds, userId);
      const visibilityClauses: string[] = [];
      const visibilityParams: string[] = [];
      if (scope.subjectIds.length) {
        visibilityClauses.push(
          `s.subject_id IN (${scope.subjectIds.map(() => '?').join(',')})`,
        );
        visibilityParams.push(...scope.subjectIds);
      }
      if (scope.classIds.length) {
        visibilityClauses.push(
          `s.class_id IN (${scope.classIds.map(() => '?').join(',')})`,
        );
        visibilityParams.push(...scope.classIds);
      }
      if (!visibilityClauses.length) {
        return { clause: '1 = 0', params: [] };
      }
      return {
        clause: `(${visibilityClauses.join(' OR ')})`,
        params: visibilityParams,
      };
    }
    if (roles.includes('student')) {
      return {
        clause: `EXISTS (
          SELECT 1 FROM student_class_enrollments e
          WHERE e.student_id = ? AND e.class_id = s.class_id
        )`,
        params: [userId],
      };
    }
    if (roles.includes('parent')) {
      return {
        clause: `EXISTS (
          SELECT 1 FROM parent_student_links p
          JOIN student_class_enrollments e ON e.student_id = p.student_id
          WHERE p.parent_id = ? AND e.class_id = s.class_id
        )`,
        params: [userId],
      };
    }
    return { clause: '1 = 0', params: [] };
  }

  private mapSchemeTopic(row: any) {
    return {
      id: row.id,
      schemeId: row.schemeId ?? row.scheme_id,
      title: row.title,
      timeframe: row.timeframe,
      objective: row.objective,
      activities: row.activities,
      resources: row.resources,
      assessment: row.assessment,
      orderIndex: Number(row.orderIndex ?? row.order_index ?? 0),
      status: row.status,
      coveredBeforeAssessment: Boolean(row.coveredBeforeAssessment ?? row.covered_before_assessment),
      coveredBeforeExamination: Boolean(row.coveredBeforeExamination ?? row.covered_before_examination),
      isNext: Boolean(row.isNext ?? row.is_next),
      carryOverReason: row.carryOverReason ?? row.carry_over_reason ?? null,
    };
  }

  private mapScheme(row: any, topics: any[] = []) {
    const mappedTopics = topics.map((topic) => this.mapSchemeTopic(topic));
    const covered = mappedTopics.filter((topic) => topic.status === 'covered').length;
    return {
      id: row.id,
      classId: row.classId ?? row.class_id,
      className: row.className ?? row.class_name,
      subjectId: row.subjectId ?? row.subject_id,
      subjectName: row.subjectName ?? row.subject_name,
      sessionId: row.sessionId ?? row.session_id,
      termId: row.termId ?? row.term_id,
      termName: row.termName ?? row.term_name,
      title: row.title,
      createdBy: row.createdBy ?? row.created_by,
      createdAt: row.createdAt ?? row.created_at,
      updatedAt: row.updatedAt ?? row.updated_at,
      topicCount: mappedTopics.length,
      coveredTopicCount: covered,
      progressPercent: mappedTopics.length ? Math.round((covered / mappedTopics.length) * 100) : 0,
      topics: mappedTopics,
    };
  }

  async listSchemes(
    tenantId: string,
    userId: string,
    filters: { classId?: string; subjectId?: string; termId?: string } = {},
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const visibility = await this.schemeVisibility(ds, userId);
    const conditions = [visibility.clause];
    const params = [...visibility.params];
    for (const [column, value] of [
      ['s.class_id', filters.classId],
      ['s.subject_id', filters.subjectId],
      ['s.term_id', filters.termId],
    ] as const) {
      if (value) {
        conditions.push(`${column} = ?`);
        params.push(value);
      }
    }
    const rows = await runDbQuery(
      ds,
      `SELECT s.id, s.class_id as "classId", c.name as "className",
              s.subject_id as "subjectId", sub.name as "subjectName", c.school_level as "schoolLevel",
              s.session_id as "sessionId", s.term_id as "termId", t.name as "termName",
              s.title, s.created_by as "createdBy", s.created_at as "createdAt",
              s.updated_at as "updatedAt",
              COUNT(st.id) as "topicCount",
              SUM(CASE WHEN st.status = 'covered' THEN 1 ELSE 0 END) as "coveredTopicCount"
       FROM academic_schemes_of_work s
       LEFT JOIN academic_classes c ON c.id = s.class_id
       LEFT JOIN academic_subjects sub ON sub.id = s.subject_id
       LEFT JOIN academic_terms t ON t.id = s.term_id
       LEFT JOIN academic_scheme_topics st ON st.scheme_id = s.id
       WHERE ${conditions.join(' AND ')}
       GROUP BY s.id, c.name, sub.name, c.school_level, t.name
       ORDER BY c.name, sub.name, s.updated_at DESC`,
      params,
    );
    const levelScoped = await this.filterByActorSchoolLevel(ds, userId, rows);
    return levelScoped.map((row: any) => ({
      ...this.mapScheme(row),
      topicCount: Number(row.topicCount ?? row.topic_count ?? 0),
      coveredTopicCount: Number(row.coveredTopicCount ?? row.covered_topic_count ?? 0),
      progressPercent: Number(row.topicCount ?? row.topic_count ?? 0)
        ? Math.round((Number(row.coveredTopicCount ?? row.covered_topic_count ?? 0) /
          Number(row.topicCount ?? row.topic_count ?? 0)) * 100)
        : 0,
      topics: undefined,
    }));
  }

  async getScheme(tenantId: string, userId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const visibility = await this.schemeVisibility(ds, userId);
    const rows = await runDbQuery(
      ds,
      `SELECT s.id, s.class_id as "classId", c.name as "className",
              s.subject_id as "subjectId", sub.name as "subjectName", c.school_level as "schoolLevel",
              s.session_id as "sessionId", s.term_id as "termId", t.name as "termName",
              s.title, s.created_by as "createdBy", s.created_at as "createdAt",
              s.updated_at as "updatedAt"
       FROM academic_schemes_of_work s
       LEFT JOIN academic_classes c ON c.id = s.class_id
       LEFT JOIN academic_subjects sub ON sub.id = s.subject_id
       LEFT JOIN academic_terms t ON t.id = s.term_id
       WHERE s.id = ? AND ${visibility.clause}`,
      [id, ...visibility.params],
    );
    if (!rows[0]) throw new NotFoundException('Scheme of work not found');
    await this.assertActorSchoolLevel(ds, userId, rows[0].classId, rows[0].subjectId);
    const topics = await runDbQuery(
      ds,
      `SELECT id, scheme_id as "schemeId", title, timeframe, objective, activities,
              resources, assessment, order_index as "orderIndex", status,
              covered_before_assessment as "coveredBeforeAssessment",
              covered_before_examination as "coveredBeforeExamination",
              is_next as "isNext",
              carry_over_reason as "carryOverReason"
       FROM academic_scheme_topics WHERE scheme_id = ? ORDER BY order_index, created_at`,
      [id],
    );
    return this.mapScheme(rows[0], topics);
  }

  async createScheme(tenantId: string, userId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertSchemeManager(ds, userId);
    const id = randomToken('scheme');
    await runDbQuery(
      ds,
      `INSERT INTO academic_schemes_of_work
       (id, class_id, subject_id, session_id, term_id, title, created_by)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      [id, body.classId, body.subjectId, body.sessionId, body.termId, body.title || null, userId],
    );
    for (const [index, topic] of (body.topics || []).entries()) {
      await this.addSchemeTopic(ds, id, topic, userId, index);
    }
    return this.getScheme(tenantId, userId, id);
  }

  async updateScheme(tenantId: string, userId: string, id: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertSchemeManager(ds, userId);
    const updates = Object.entries(body).filter(([key, value]) =>
      ['classId', 'subjectId', 'sessionId', 'termId', 'title'].includes(key) && value !== undefined,
    );
    if (updates.length) {
      const columns: Record<string, string> = {
        classId: 'class_id', subjectId: 'subject_id', sessionId: 'session_id',
        termId: 'term_id', title: 'title',
      };
      await runDbQuery(
        ds,
        `UPDATE academic_schemes_of_work SET ${updates.map(([key]) => `${columns[key]} = ?`).join(', ')},
         updated_at = NOW() WHERE id = ?`,
        [...updates.map(([, value]) => value), id],
      );
    }
    return this.getScheme(tenantId, userId, id);
  }

  async deleteScheme(tenantId: string, userId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertSchemeManager(ds, userId);
    await runDbQuery(ds, `DELETE FROM academic_scheme_topics WHERE scheme_id = ?`, [id]);
    await runDbQuery(ds, `DELETE FROM academic_schemes_of_work WHERE id = ?`, [id]);
    return { id };
  }

  private async addSchemeTopic(ds: any, schemeId: string, body: any, userId: string, fallbackOrder = 0) {
    const id = randomToken('topic');
    await runDbQuery(
      ds,
      `INSERT INTO academic_scheme_topics
       (id, scheme_id, title, timeframe, objective, activities, resources, assessment, order_index, updated_by)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [id, schemeId, body.title, body.timeframe || null, body.objective || null,
        body.activities || null, body.resources || null, body.assessment || null,
        body.orderIndex ?? fallbackOrder, userId],
    );
    return id;
  }

  async createSchemeTopic(tenantId: string, userId: string, schemeId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertSchemeManager(ds, userId);
    const scheme = await runDbQuery(ds, `SELECT id FROM academic_schemes_of_work WHERE id = ?`, [schemeId]);
    if (!scheme[0]) throw new NotFoundException('Scheme of work not found');
    const id = await this.addSchemeTopic(ds, schemeId, body, userId);
    return { id };
  }

  async updateSchemeTopic(tenantId: string, userId: string, topicId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertSchemeManager(ds, userId);
    const allowed: Record<string, string> = {
      title: 'title', timeframe: 'timeframe', objective: 'objective', activities: 'activities',
      resources: 'resources', assessment: 'assessment', orderIndex: 'order_index',
    };
    const updates = Object.entries(body).filter(([key, value]) => allowed[key] && value !== undefined);
    if (!updates.length) return { id: topicId };
    await runDbQuery(
      ds,
      `UPDATE academic_scheme_topics SET ${updates.map(([key]) => `${allowed[key]} = ?`).join(', ')},
       updated_by = ?, updated_at = NOW() WHERE id = ?`,
      [...updates.map(([, value]) => value), userId, topicId],
    );
    return { id: topicId };
  }

  async deleteSchemeTopic(tenantId: string, userId: string, topicId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertSchemeManager(ds, userId);
    await runDbQuery(ds, `DELETE FROM academic_scheme_topics WHERE id = ?`, [topicId]);
    return { id: topicId };
  }

  async updateSchemeTopicProgress(tenantId: string, userId: string, topicId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await runDbQuery(
      ds,
      `SELECT st.id, st.scheme_id as "schemeId", s.class_id as "classId", s.subject_id as "subjectId"
       FROM academic_scheme_topics st JOIN academic_schemes_of_work s ON s.id = st.scheme_id
       WHERE st.id = ?`,
      [topicId],
    );
    if (!rows[0]) throw new NotFoundException('Scheme topic not found');
    const roles = await this.getUserRoles(ds, userId);
    if (!roles.some((role) => ['head_teacher', 'principal'].includes(role))) {
      await this.assertTeacherMayAccess(ds, userId, rows[0].classId, rows[0].subjectId);
    }
    if (body.status === 'carried_over' && !body.carryOverReason?.trim()) {
      throw new BadRequestException('A reason is required when carrying a topic over');
    }
    await runDbQuery(
      ds,
      `UPDATE academic_scheme_topics
       SET status = ?, covered_before_assessment = ?, covered_before_examination = ?,
           is_next = ?, carry_over_reason = ?, updated_by = ?, updated_at = NOW()
       WHERE id = ?`,
      [body.status, Boolean(body.coveredBeforeAssessment), Boolean(body.coveredBeforeExamination),
        Boolean(body.isNext),
        body.status === 'carried_over' ? body.carryOverReason.trim() : null, userId, topicId],
    );
    if (body.isNext) {
      await runDbQuery(
        ds,
        `UPDATE academic_scheme_topics SET is_next = false, updated_at = NOW()
         WHERE scheme_id = ? AND id <> ?`,
        [rows[0].schemeId, topicId],
      );
    }
    return { id: topicId };
  }

  async listAssignments(tenantId: string, actorUserId?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await ds.query(
      `SELECT 
         a.id,
         a.title,
         a.description,
         a.class_id as classId,
         c.name as className,
         a.subject_id as subjectId,
         c.school_level as schoolLevel,
         s.name as subjectName,
         a.assigned_by as assignedBy,
         u.name as assignedByName,
         a.due_date as dueDate,
         a.is_active as isActive,
         a.created_at as createdAt
       FROM academic_assignments a
       LEFT JOIN academic_classes c ON c.id = a.class_id
       LEFT JOIN academic_subjects s ON s.id = a.subject_id
       LEFT JOIN users u ON u.id = a.assigned_by
       ORDER BY a.created_at DESC
       LIMIT 200`,
    );
    // Normalize any possible lowercase keys from drivers
    const mapped = rows.map((r: any) =>
      mapDataToUpdateKeys(r, {
        classid: 'classId',
        classname: 'className',
        subjectid: 'subjectId',
        subjectname: 'subjectName',
        assignedby: 'assignedBy',
        assignedbyname: 'assignedByName',
        duedate: 'dueDate',
        createdat: 'createdAt',
        isactive: 'isActive',
      }),
    );
    const levelScoped = await this.filterByActorSchoolLevel(ds, actorUserId, mapped);
    if (!actorUserId) return levelScoped;
    const scope = await this.getTeacherScope(ds, actorUserId);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (
      !scope.isTeacher ||
      hasAcademicManagementRole(roles)
    ) {
      return levelScoped;
    }
    return levelScoped.filter(
      (row: any) =>
        scope.classIds.includes(String(row.classId)) ||
        scope.subjectIds.includes(String(row.subjectId)) ||
        String(row.assignedBy) === String(actorUserId),
    );
  }

  async upsertAssignment(tenantId: string, id: string | null, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertActorSchoolLevel(ds, body.assignedBy, body.classId, body.subjectId);
    if (body.assignedBy) {
      await this.assertTeacherMayAccess(
        ds,
        body.assignedBy,
        body.classId,
        body.subjectId,
      );
    }
    if (!id) {
      const assignmentId = randomToken('asg');
      await runDbQuery(
        ds,
        `INSERT INTO academic_assignments (id, title, description, class_id, subject_id, assigned_by, due_date, is_active, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
        [
          assignmentId,
          body.title,
          body.description || null,
          body.classId || null,
          body.subjectId || null,
          body.assignedBy || null,
          body.dueDate,
          body.isActive !== false,
        ],
      );
      return { id: assignmentId };
    }
    const existing = await this.getRowOrThrow(
      ds,
      'academic_assignments',
      id,
      'Assignment not found',
    );
    const merged = {
      title: body.title ?? existing.title,
      description:
        body.description !== undefined
          ? body.description
          : existing.description,
      classId: body.classId !== undefined ? body.classId : existing.class_id,
      subjectId:
        body.subjectId !== undefined ? body.subjectId : existing.subject_id,
      dueDate: body.dueDate ?? existing.due_date,
      isActive:
        body.isActive !== undefined ? !!body.isActive : existing.is_active,
    };
    if (body.assignedBy) {
      await this.assertTeacherMayAccess(
        ds,
        body.assignedBy,
        merged.classId,
        merged.subjectId,
      );
    }
    await runDbQuery(
      ds,
      `UPDATE academic_assignments SET title = ?, description = ?, class_id = ?, subject_id = ?, due_date = ?, is_active = ?, updated_at = NOW() WHERE id = ?`,
      [
        merged.title,
        merged.description || null,
        merged.classId || null,
        merged.subjectId || null,
        merged.dueDate,
        merged.isActive,
        id,
      ],
    );
    return { id };
  }

  async deleteAssignment(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(
      ds,
      `DELETE FROM academic_assignment_submissions WHERE assignment_id = ?`,
      [id],
    );
    await runDbQuery(ds, `DELETE FROM academic_assignments WHERE id = ?`, [id]);
    return { id };
  }

  /**
   * Roster of expected submissions for an assignment: enrolled students for the
   * assignment's class, merged with any rows in academic_assignment_submissions.
   * Students without a submission row appear as pending (or late if past due).
   */
  async listAssignmentSubmissions(
    tenantId: string,
    assignmentId: string,
    actorUserId?: string,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const assignmentRows = await runDbQuery(
      ds,
      `SELECT id, class_id as "classId", subject_id as "subjectId", due_date as "dueDate"
       FROM academic_assignments WHERE id = ? LIMIT 1`,
      [assignmentId],
    );
    const assignment = assignmentRows?.[0];
    if (!assignment) throw new NotFoundException('Assignment not found');
    if (actorUserId) {
      const roles = await this.getUserRoles(ds, actorUserId);
      if (!hasAcademicManagementRole(roles)) {
        await this.assertTeacherMayAccess(
          ds,
          actorUserId,
          assignment.classId,
          assignment.subjectId,
        );
      }
    }

    const dueDate = assignment.dueDate ? new Date(assignment.dueDate) : null;
    const pastDue = dueDate ? dueDate.getTime() < Date.now() : false;

    let students: any[] = [];
    if (assignment.classId) {
      students = await runDbQuery(
        ds,
        `SELECT e.student_id as "studentId", u.name as "studentName"
         FROM student_class_enrollments e
         LEFT JOIN users u ON u.id = e.student_id
         WHERE e.class_id = ?
         ORDER BY u.name ASC NULLS LAST`,
        [assignment.classId],
      );
    }

    const submitted: any[] = await runDbQuery(
      ds,
      `SELECT s.id, s.student_id as "studentId", u.name as "studentName",
              s.status, s.score, s.submitted_at as "submittedAt", s.note
       FROM academic_assignment_submissions s
       LEFT JOIN users u ON u.id = s.student_id
       WHERE s.assignment_id = ?`,
      [assignmentId],
    );
    const byStudent = new Map(
      (submitted || []).map((r: any) => [r.studentId, r]),
    );

    const rosterIds = new Set((students || []).map((s: any) => s.studentId));
    const out: any[] = [];

    for (const stu of students || []) {
      const row = byStudent.get(stu.studentId);
      if (row) {
        out.push({
          id: row.id,
          studentId: row.studentId,
          studentName: row.studentName || stu.studentName || row.studentId,
          status: row.status || 'submitted',
          score: row.score != null ? Number(row.score) : undefined,
          submittedAt: row.submittedAt || undefined,
          note: row.note || undefined,
        });
      } else {
        out.push({
          id: `pending:${stu.studentId}`,
          studentId: stu.studentId,
          studentName: stu.studentName || stu.studentId,
          status: pastDue ? 'late' : 'pending',
        });
      }
    }

    // Include submissions from students no longer enrolled
    for (const row of submitted || []) {
      if (rosterIds.has(row.studentId)) continue;
      out.push({
        id: row.id,
        studentId: row.studentId,
        studentName: row.studentName || row.studentId,
        status: row.status || 'submitted',
        score: row.score != null ? Number(row.score) : undefined,
        submittedAt: row.submittedAt || undefined,
        note: row.note || undefined,
      });
    }

    return out;
  }

  async upsertAssignmentSubmission(
    tenantId: string,
    assignmentId: string,
    body: {
      studentId: string;
      status?: string;
      score?: number | null;
      note?: string | null;
      submittedAt?: string | null;
    },
    actorUserId?: string,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const assignment = await this.getRowOrThrow(
      ds,
      'academic_assignments',
      assignmentId,
      'Assignment not found',
    );
    if (!body.studentId) throw new BadRequestException('studentId is required');
    if (actorUserId) {
      const roles = await this.getUserRoles(ds, actorUserId);
      if (!hasAcademicManagementRole(roles)) {
        await this.assertTeacherMayAccess(
          ds,
          actorUserId,
          assignment.class_id,
          assignment.subject_id,
        );
      }
    }

    const status = (body.status || 'submitted').toLowerCase();
    if (!['submitted', 'pending', 'late', 'graded'].includes(status)) {
      throw new BadRequestException('Invalid submission status');
    }

    const existing: any[] = await runDbQuery(
      ds,
      `SELECT id FROM academic_assignment_submissions
       WHERE assignment_id = ? AND student_id = ? LIMIT 1`,
      [assignmentId, body.studentId],
    );

    const submittedAt =
      body.submittedAt ||
      (status === 'pending' ? null : new Date().toISOString());

    if (existing?.[0]?.id) {
      await runDbQuery(
        ds,
        `UPDATE academic_assignment_submissions
         SET status = ?, score = ?, note = ?, submitted_at = COALESCE(?, submitted_at), updated_at = NOW()
         WHERE id = ?`,
        [
          status,
          body.score ?? null,
          body.note ?? null,
          submittedAt,
          existing[0].id,
        ],
      );
      return {
        id: existing[0].id,
        assignmentId,
        studentId: body.studentId,
        status,
      };
    }

    const id = randomToken('asub');
    await runDbQuery(
      ds,
      `INSERT INTO academic_assignment_submissions
         (id, assignment_id, student_id, status, score, submitted_at, note, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        assignmentId,
        body.studentId,
        status,
        body.score ?? null,
        submittedAt,
        body.note ?? null,
      ],
    );
    return { id, assignmentId, studentId: body.studentId, status };
  }

  private async ensureScoreSheetDefaults(ds: any) {
    const existing: any[] = await runDbQuery(
      ds,
      `SELECT id FROM academic_score_categories WHERE is_active = true LIMIT 1`,
    );
    if (existing.length) return;

    const defaults = [
      {
        name: 'Continuous Assessment',
        slug: 'continuous-assessment',
        subcategories: [
          ['Note', 'note'],
          ['Internal Assessment', 'internal-assessment'],
          ['School Assessment', 'school-assessment'],
        ],
      },
      { name: 'Examination', slug: 'examination', subcategories: [] },
    ];
    for (const category of defaults) {
      const categoryId = randomToken('scorecat');
      await runDbQuery(
        ds,
        `INSERT INTO academic_score_categories (id, name, slug)
         VALUES (?, ?, ?) ON CONFLICT (slug) DO NOTHING`,
        [categoryId, category.name, category.slug],
      );
      const rows: any[] = await runDbQuery(
        ds,
        `SELECT id FROM academic_score_categories WHERE slug = ? LIMIT 1`,
        [category.slug],
      );
      const actualCategoryId = rows[0]?.id || categoryId;
      for (const [name, slug] of category.subcategories) {
        await runDbQuery(
          ds,
          `INSERT INTO academic_score_subcategories
             (id, category_id, name, slug)
           VALUES (?, ?, ?, ?) ON CONFLICT (category_id, slug) DO NOTHING`,
          [randomToken('scoresub'), actualCategoryId, name, slug],
        );
      }
    }
  }

  async createScoreSheetCategory(tenantId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.ensureScoreSheetDefaults(ds);
    const categoryId = randomToken('scorecat');
    await runDbQuery(
      ds,
      `INSERT INTO academic_score_categories (id, name, slug)
       VALUES (?, ?, ?) ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, is_active = true`,
      [categoryId, body.name.trim(), body.slug.trim().toLowerCase()],
    );
    const categoryRows: any[] = await runDbQuery(
      ds,
      `SELECT id FROM academic_score_categories WHERE slug = ? LIMIT 1`,
      [body.slug.trim().toLowerCase()],
    );
    const actualId = categoryRows[0]?.id || categoryId;
    for (const subcategory of body.subcategories || []) {
      await runDbQuery(
        ds,
        `INSERT INTO academic_score_subcategories (id, category_id, name, slug)
         VALUES (?, ?, ?, ?) ON CONFLICT (category_id, slug)
         DO UPDATE SET name = EXCLUDED.name, is_active = true`,
        [
          randomToken('scoresub'),
          actualId,
          subcategory.name.trim(),
          subcategory.slug.trim().toLowerCase(),
        ],
      );
    }
    return { id: actualId };
  }

  async updateScoreSheetCategory(tenantId: string, id: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id FROM academic_score_categories WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!rows[0]) throw new NotFoundException('Score sheet category not found');
    await runDbQuery(
      ds,
      `UPDATE academic_score_categories SET name = ?, slug = ?, is_active = true WHERE id = ?`,
      [body.name.trim(), body.slug.trim().toLowerCase(), id],
    );
    for (const subcategory of body.subcategories || []) {
      await runDbQuery(
        ds,
        `INSERT INTO academic_score_subcategories (id, category_id, name, slug)
         VALUES (?, ?, ?, ?) ON CONFLICT (category_id, slug)
         DO UPDATE SET name = EXCLUDED.name, is_active = true`,
        [
          randomToken('scoresub'),
          id,
          subcategory.name.trim(),
          subcategory.slug.trim().toLowerCase(),
        ],
      );
    }
    return { id };
  }

  async deleteScoreSheetCategory(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(
      ds,
      `UPDATE academic_score_categories SET is_active = false WHERE id = ?`,
      [id],
    );
    await runDbQuery(
      ds,
      `UPDATE academic_score_subcategories SET is_active = false WHERE category_id = ?`,
      [id],
    );
    return { id };
  }

  async listScoreSheetCategories(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.ensureScoreSheetDefaults(ds);
    const categories: any[] = await runDbQuery(
      ds,
      `SELECT id, name, slug, is_active as "isActive"
       FROM academic_score_categories WHERE is_active = true ORDER BY id`,
    );
    const subcategories: any[] = await runDbQuery(
      ds,
      `SELECT id, category_id as "categoryId", name, slug, is_active as "isActive"
       FROM academic_score_subcategories
       WHERE is_active = true ORDER BY name`,
    );
    return categories.map((category) => ({
      ...category,
      subcategories: subcategories.filter(
        (subcategory) => String(subcategory.categoryId) === String(category.id),
      ),
    }));
  }

  async listScoreSheets(
    tenantId: string,
    actorUserId: string,
    filters: {
      sessionId?: string;
      termId?: string;
      classId?: string;
      subjectId?: string;
      categoryId?: string;
      subcategoryId?: string;
    } = {},
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.ensureScoreSheetDefaults(ds);
    const scope = await this.getTeacherScope(ds, actorUserId);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (!roles.some((role) => SCORE_SHEET_READ_ROLES.includes(role as any))) {
      throw new ForbiddenException('Score sheets are restricted to academic management roles');
    }
    const teacher =
      scope.isTeacher &&
      !hasAcademicManagementRole(roles);
    const clauses = ['1 = 1'];
    const params: any[] = [];
    for (const [column, value] of [
      ['session_id', filters.sessionId],
      ['term_id', filters.termId],
      ['class_id', filters.classId],
      ['subject_id', filters.subjectId],
      ['category_id', filters.categoryId],
      ['subcategory_id', filters.subcategoryId],
    ] as Array<[string, string | undefined]>) {
      if (value) {
        clauses.push(`ss.${column} = ?`);
        params.push(value);
      }
    }
    if (teacher) {
      const subjectIds = scope.subjectIds;
      if (!subjectIds.length) return [];
      clauses.push(`ss.subject_id IN (${subjectIds.map(() => '?').join(',')})`);
      params.push(...subjectIds);
    } else if (!roles.length) {
      return [];
    }
    const rows = await runDbQuery(
      ds,
      `SELECT ss.id, ss.session_id as "sessionId", ss.term_id as "termId",
          ss.class_id as "classId", c.name as "className", c.school_level as "schoolLevel",
          ss.student_id as "studentId", u.name as "studentName",
          ss.subject_id as "subjectId", s.name as "subjectName",
          ss.category_id as "categoryId", sc.name as "categoryName",
          ss.subcategory_id as "subcategoryId", ssc.name as "subcategoryName",
          ss.student_score as "studentScore", ss.obtainable_score as "obtainableScore",
          ss.grade, ss.status, ss.recorder_user_id as "recorderUserId",
          ss.recorded_by as "recordedBy", ss.recorded_at as "recordedAt",
          ss.updated_at as "updatedAt"
       FROM academic_score_sheets ss
       LEFT JOIN academic_classes c ON c.id = ss.class_id
       LEFT JOIN users u ON u.id = ss.student_id
       LEFT JOIN academic_subjects s ON s.id = ss.subject_id
       LEFT JOIN academic_score_categories sc ON sc.id = ss.category_id
       LEFT JOIN academic_score_subcategories ssc ON ssc.id = ss.subcategory_id
       WHERE ${clauses.join(' AND ')}
       ORDER BY u.name NULLS LAST, ss.recorded_at DESC`,
      params,
    );
    return this.filterByActorSchoolLevel(ds, actorUserId, rows);
  }

  async previewScoreSheets(
    tenantId: string,
    actorUserId: string,
    sessionId: string,
    termId: string,
    classId: string,
    studentName?: string,
    allowSubjectTeacher = false,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (
      !roles.some((role) =>
        (role !== 'subject_teacher' && SCORE_SHEET_READ_ROLES.includes(role as any)) ||
        role === 'class_teacher' ||
        (allowSubjectTeacher && role === 'subject_teacher'),
      )
    ) {
      throw new ForbiddenException('Score sheet preview is restricted to academic management roles or assigned subject teachers');
    }
    await this.assertActorSchoolLevel(ds, actorUserId, classId);
    const scope = await this.getTeacherScope(ds, actorUserId);
    if (
      scope.isTeacher &&
      !hasAcademicManagementRole(roles) &&
      !scope.classIds.includes(String(classId)) &&
      !scope.subjectClassIds.includes(String(classId))
    ) {
      throw new ForbiddenException('Teacher is not assigned to this class');
    }
    const subjectIds = scope.isTeacher ? scope.subjectIds : [];
    // A user may hold both roles. Class-teacher preview must include every
    // subject in the assigned class, rather than the subject-teacher scope.
    const isSubjectTeacher =
      roles.includes('subject_teacher') &&
      !roles.includes('class_teacher') &&
      !hasAcademicManagementRole(roles);
    const subjectClause = isSubjectTeacher
      ? `AND ss.subject_id IN (${subjectIds.map(() => '?').join(',') || 'NULL'})`
      : '';
    const studentClause = studentName?.trim() ? ' AND u.name ILIKE ?' : '';
    const records: any[] = await runDbQuery(
      ds,
      `SELECT e.student_id as "studentId", u.name as "studentName", u.gender as "studentGender",
          c.school_level as "studentSchoolLevel", c.name as "studentClassName",
          c.code as "studentClassCode", c.class_teacher_id as "studentClassTeacherId",
          teacher.name as "studentClassTeacherName", ses.name as "studentAcademicSession",
          t.name as "studentTerm", ss.subject_id as "subjectId", sub.name as "subjectName",
          sc.name as "categoryName",
          COALESCE(ssc.name, sc.name) as "subcategoryName",
          ss.student_score as "studentScore", ss.obtainable_score as "obtainableScore",
          CASE WHEN EXISTS (
            SELECT 1 FROM academic_generated_results gar
            WHERE gar.student_id = e.student_id AND gar.class_id = e.class_id
              AND gar.term_id = t.id AND gar.session_id = ses.id
          ) THEN 'available' ELSE 'unavailable' END as "resultStatus",
          (SELECT gar.release_status FROM academic_generated_results gar
           WHERE gar.student_id = e.student_id AND gar.class_id = e.class_id
             AND gar.term_id = t.id AND gar.session_id = ses.id LIMIT 1) as "resultReleaseStatus"
       FROM student_class_enrollments e
       JOIN users u ON u.id = e.student_id
       JOIN academic_classes c ON c.id = e.class_id
       LEFT JOIN users teacher ON teacher.id = c.class_teacher_id
       LEFT JOIN academic_sessions ses ON ses.id = ?
       LEFT JOIN academic_terms t ON t.id = ? AND t.session_id = ses.id
       LEFT JOIN academic_score_sheets ss
         ON ss.student_id = e.student_id AND ss.class_id = e.class_id
         AND ss.session_id = ? AND ss.term_id = ? ${subjectClause}
       LEFT JOIN academic_subjects sub ON sub.id = ss.subject_id
       LEFT JOIN academic_score_categories sc ON sc.id = ss.category_id
       LEFT JOIN academic_score_subcategories ssc ON ssc.id = ss.subcategory_id
       WHERE e.class_id = ?${studentClause}
       ORDER BY u.name, sc.name, ssc.name`,
      [
        sessionId,
        termId,
        sessionId,
        termId,
        ...(isSubjectTeacher ? subjectIds : []),
        classId,
        ...(studentName?.trim() ? [`%${studentName.trim()}%`] : []),
      ],
    );
    const grouped = new Map<string, any>();
    for (const record of records) {
      const id = String(record.studentId);
      const row = grouped.get(id) || {
        studentSchoolLevel: record.studentSchoolLevel,
        studentAcademicSession: record.studentAcademicSession,
        studentTerm: record.studentTerm,
        studentId: record.studentId,
        studentGender: record.studentGender,
        studentName: record.studentName,
        studentClassName: record.studentClassName,
        studentClassCode: record.studentClassCode,
        studentClassTeacherId: record.studentClassTeacherId,
        studentClassTeacherName: record.studentClassTeacherName,
        studentScoreSheet: [],
        resultStatus: record.resultStatus || 'unavailable',
        resultReleaseStatus: record.resultReleaseStatus || null,
        sudentTotalSubjects: 0,
        _subjects: new Set<string>(),
      };
      if (record.subjectId && record.categoryName) {
        let subjectRow = row.studentScoreSheet.find(
          (item: any) => String(item.subjectId) === String(record.subjectId),
        );
        if (!subjectRow) {
          subjectRow = {
            subjectId: record.subjectId,
            subjectName: record.subjectName,
            totalScore: 0,
          };
          row.studentScoreSheet.push(subjectRow);
        }
        const key = String(record.subcategoryName || record.categoryName)
          .trim()
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '_');
        const score = record.studentScore == null ? null : Number(record.studentScore);
        if (score == null) {
          if (!(key in subjectRow)) subjectRow[key] = null;
        } else {
          subjectRow[key] =
            subjectRow[key] == null ? score : Number(subjectRow[key]) + score;
          subjectRow.totalScore += score;
        }
        if (record.subjectId) row._subjects.add(String(record.subjectId));
      }
      grouped.set(id, row);
    }
    return Array.from(grouped.values()).map(({ _subjects, ...row }) => ({
      ...row,
      sudentTotalSubjects: _subjects.size,
    }));
  }

  async generateScoreSheetResult(
    tenantId: string,
    actorUserId: string,
    studentId: string,
    sessionId: string,
    termId: string,
    classId: string,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (!roles.includes('subject_teacher')) {
      throw new ForbiddenException('Only subject teachers can generate results');
    }
    await this.assertActorSchoolLevel(ds, actorUserId, classId);
    await this.assertTeacherMayAccess(ds, actorUserId, classId);
    const scope = await this.getTeacherScope(ds, actorUserId);
    if (!scope.subjectIds.length) throw new ForbiddenException('Teacher has no assigned subjects');
    const preview = await this.previewScoreSheets(
      tenantId,
      actorUserId,
      sessionId,
      termId,
      classId,
      undefined,
      true,
    );
    const raw = preview.find((row: any) => String(row.studentId) === String(studentId));
    if (!raw || !raw.studentScoreSheet?.length) {
      throw new NotFoundException('No score sheets found for this student');
    }
    const generatedByRows: any[] = await runDbQuery(
      ds,
      `SELECT name FROM users WHERE id = ? LIMIT 1`,
      [actorUserId],
    );
    const studentRows: any[] = await runDbQuery(
      ds,
      `SELECT admission_no as "admissionNo" FROM users WHERE id = ? LIMIT 1`,
      [studentId],
    );
    const termRows: any[] = await runDbQuery(
      ds,
      `SELECT id, code, name, end_date as "endDate", start_date as "startDate"
       FROM academic_terms WHERE session_id = ? ORDER BY
       CASE code WHEN 'first' THEN 1 WHEN 'second' THEN 2 WHEN 'third' THEN 3 ELSE 4 END`,
      [sessionId],
    );
    const currentTerm = termRows.find((term) => String(term.id) === String(termId));
    const termIndex = termRows.findIndex((term) => String(term.id) === String(termId));
    const previousTermIds = termRows.slice(0, Math.max(0, termIndex));
    const previousGeneratedRows: any[] = previousTermIds.length
      ? await runDbQuery(
          ds,
          `SELECT term_id as "termId", student_subject_records as "studentSubjectRecords"
           FROM academic_generated_results
           WHERE student_id = ? AND class_id = ? AND session_id = ? AND term_id IN (${previousTermIds.map(() => '?').join(',')})`,
          [studentId, classId, sessionId, ...previousTermIds.map((term) => term.id)],
        )
      : [];
    const previousSubjects = new Map<string, any>();
    for (const previous of previousGeneratedRows) {
      let records: any[] = [];
      try {
        records = typeof previous.studentSubjectRecords === 'string'
          ? JSON.parse(previous.studentSubjectRecords)
          : previous.studentSubjectRecords || [];
      } catch {
        records = [];
      }
      for (const record of records) {
        previousSubjects.set(`${previous.termId}:${record.subjectId}`, record);
      }
    }
    const attendanceRows: any[] = await runDbQuery(
      ds,
      `SELECT COUNT(*)::int as "schoolOpened",
          SUM(CASE WHEN LOWER(status) = 'present' THEN 1 ELSE 0 END)::int as present,
          SUM(CASE WHEN LOWER(status) = 'absent' THEN 1 ELSE 0 END)::int as absent
       FROM attendance_students WHERE student_id = ? AND class_id = ?`,
      [studentId, classId],
    );
    let bands: any[] = [];
    try {
      bands = this.gradingMatrix
        ? (await this.gradingMatrix.get(tenantId)).gradeBands || DEFAULT_GRADING_MATRIX.gradeBands
        : DEFAULT_GRADING_MATRIX.gradeBands;
    } catch {
      bands = [];
    }
    const subjectRecords = raw.studentScoreSheet.map((subject: any) =>
      calculateGeneratedSubjectRecord(
        subject,
        previousTermIds.map((term) => previousSubjects.get(`${term.id}:${subject.subjectId}`)),
        termIndex,
        bands,
      ),
    );
    const existingGenerated: any[] = await runDbQuery(
      ds,
      `SELECT id, release_status as "releaseStatus"
       FROM academic_generated_results
       WHERE session_id = ? AND term_id = ? AND class_id = ? AND student_id = ? LIMIT 1`,
      [sessionId, termId, classId, studentId],
    );
    if (existingGenerated[0]?.releaseStatus === 'approved') {
      throw new ForbiddenException('Approved results cannot be regenerated');
    }
    const values = [
      raw.studentSchoolLevel,
      raw.studentAcademicSession,
      raw.studentTerm,
      raw.studentGender || null,
      raw.studentName,
      raw.studentClassName,
      raw.studentClassCode,
      raw.studentClassTeacherId,
      raw.studentClassTeacherName,
      attendanceRows[0]?.schoolOpened || 0,
      studentRows[0]?.admissionNo || null,
      attendanceRows[0]?.present || 0,
      currentTerm?.endDate || null,
      attendanceRows[0]?.absent || 0,
      termRows[termIndex + 1]?.startDate || null,
      JSON.stringify(subjectRecords),
      generatedByRows[0]?.name || actorUserId,
    ];
    if (existingGenerated[0]) {
      await runDbQuery(
        ds,
        `UPDATE academic_generated_results SET
          student_school_level = ?, student_academic_session = ?, student_term = ?,
          student_gender = ?, student_name = ?, student_class_name = ?, student_class_code = ?,
          student_class_teacher_id = ?, student_class_teacher_name = ?, no_of_times_school_opened = ?,
          admission_no = ?, no_of_times_present = ?, term_ended = ?, no_of_times_absent = ?,
          next_term_begins = ?, student_subject_records = ?, release_status = 'pending',
          approved_by = NULL, approved_at = NULL, generated_by = ?, generated_at = NOW(), updated_at = NOW()
         WHERE id = ?`,
        [...values, existingGenerated[0].id],
      );
    } else {
      await runDbQuery(
        ds,
        `INSERT INTO academic_generated_results
          (id, session_id, term_id, class_id, student_id, student_school_level,
           student_academic_session, student_term, student_gender, student_name,
           student_class_name, student_class_code, student_class_teacher_id,
           student_class_teacher_name, no_of_times_school_opened, admission_no,
           no_of_times_present, term_ended, no_of_times_absent, next_term_begins,
           student_subject_records, release_status, generated_by)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)`,
        [randomToken('genres'), sessionId, termId, classId, studentId, ...values],
      );
    }
    // Keep the legacy report-card reader in sync during the transition.
    for (const subject of subjectRecords) {
      const legacy: any[] = await runDbQuery(
        ds,
        `SELECT id FROM academic_results
         WHERE student_id = ? AND class_id = ? AND subject_id = ? AND term_id = ? LIMIT 1`,
        [studentId, classId, subject.subjectId, termId],
      );
      if (legacy[0]) {
        await runDbQuery(
          ds,
          `UPDATE academic_results SET ca_score = ?, exam_score = ?, total_score = ?,
             grade = ?, status = 'submitted', updated_at = NOW() WHERE id = ?`,
          [subject.caScore, subject.examScore, subject.totalScore, subject.grade_title, legacy[0].id],
        );
      } else {
        await runDbQuery(
          ds,
          `INSERT INTO academic_results
           (id, student_id, class_id, subject_id, term_id, ca_score, exam_score,
            total_score, grade, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', NOW(), NOW())`,
          [randomToken('res'), studentId, classId, subject.subjectId, termId,
            subject.caScore, subject.examScore, subject.totalScore, subject.grade_title],
        );
      }
    }
    this.listCache.invalidate(`tenant:${tenantId}:academic:generated-results:`);
    this.listCache.invalidate(`tenant:${tenantId}:academic:results:`);
    return { studentId, classId, termId, resultStatus: 'available', subjectCount: subjectRecords.length };
  }

  async saveScoreSheetBatch(tenantId: string, actorUserId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.ensureScoreSheetDefaults(ds);
    await this.assertActorSchoolLevel(ds, actorUserId, body.classId, body.subjectId);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (!roles.includes('subject_teacher')) {
      throw new ForbiddenException('Only subject teachers can save score sheets');
    }
    await this.assertTeacherMayAccess(
      ds,
      actorUserId,
      body.classId,
      body.subjectId,
    );
    const userRows: any[] = await runDbQuery(
      ds,
      `SELECT name FROM users WHERE id = ? LIMIT 1`,
      [actorUserId],
    );
    const recordedBy = userRows[0]?.name || actorUserId;
    const categoryRows: any[] = await runDbQuery(
      ds,
      `SELECT id, name FROM academic_score_categories WHERE id = ? AND is_active = true`,
      [body.categoryId],
    );
    if (!categoryRows[0]) throw new BadRequestException('Invalid score category');
    let subcategoryName: string | undefined;
    if (body.subcategoryId) {
      const subcategoryRows: any[] = await runDbQuery(
        ds,
        `SELECT id, name FROM academic_score_subcategories
         WHERE id = ? AND category_id = ? AND is_active = true`,
        [body.subcategoryId, body.categoryId],
      );
      if (!subcategoryRows[0]) throw new BadRequestException('Invalid score subcategory');
      subcategoryName = subcategoryRows[0].name;
    }
    const status = body.status === 'published' ? 'published' : 'draft';
    const saved: string[] = [];
    for (const entry of body.entries || []) {
      const score =
        entry.studentScore === null || entry.studentScore === undefined
          ? null
          : Number(entry.studentScore);
      const obtainable = Number(entry.obtainableScore);
      if (!Number.isFinite(obtainable) || obtainable <= 0) {
        throw new BadRequestException('Obtainable score must be greater than zero');
      }
      if (score !== null && (!Number.isFinite(score) || score < 0 || score > obtainable)) {
        throw new BadRequestException('Student score must be between zero and obtainable score');
      }
      const grade = calculateScoreSheetGrade(score, obtainable);
      const existing: any[] = entry.id
        ? await runDbQuery(
            ds,
            `SELECT id, status, session_id as "sessionId", term_id as "termId",
                    class_id as "classId", subject_id as "subjectId",
                    category_id as "categoryId", subcategory_id as "subcategoryId"
             FROM academic_score_sheets WHERE id = ?`,
            [entry.id],
          )
        : await runDbQuery(
            ds,
            `SELECT id, status FROM academic_score_sheets
             WHERE session_id = ? AND term_id = ? AND class_id = ? AND student_id = ?
               AND subject_id = ? AND category_id = ?
               AND ((subcategory_id = CAST(? AS varchar)) OR
                    (subcategory_id IS NULL AND CAST(? AS varchar) IS NULL))
             LIMIT 1`,
            [
              body.sessionId,
              body.termId,
              body.classId,
              entry.studentId,
              body.subjectId,
              body.categoryId,
              body.subcategoryId || null,
              body.subcategoryId || null,
            ],
          );
      if (
        existing[0] &&
        (String(existing[0].sessionId) !== String(body.sessionId) ||
          String(existing[0].termId) !== String(body.termId) ||
          String(existing[0].classId) !== String(body.classId) ||
          String(existing[0].subjectId) !== String(body.subjectId) ||
          String(existing[0].categoryId) !== String(body.categoryId) ||
          String(existing[0].subcategoryId || '') !== String(body.subcategoryId || ''))
      ) {
        throw new ForbiddenException('Score sheet does not belong to this selection');
      }
      if (existing[0]?.status === 'published') {
        throw new ForbiddenException('Published score sheets cannot be edited');
      }
      const id = existing[0]?.id || randomToken('scoresheet');
      if (existing[0]) {
        await runDbQuery(
          ds,
          `UPDATE academic_score_sheets
           SET student_score = ?, obtainable_score = ?, grade = ?, status = ?,
               recorder_user_id = ?, recorded_by = ?, updated_at = NOW()
           WHERE id = ?`,
          [score, obtainable, grade, status, actorUserId, recordedBy, id],
        );
      } else {
        await runDbQuery(
          ds,
          `INSERT INTO academic_score_sheets
           (id, session_id, term_id, class_id, student_id, subject_id, category_id,
            subcategory_id, student_score, obtainable_score, grade, status,
            recorder_user_id, recorded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          [
            id,
            body.sessionId,
            body.termId,
            body.classId,
            entry.studentId,
            body.subjectId,
            body.categoryId,
            body.subcategoryId || null,
            score,
            obtainable,
            grade,
            status,
            actorUserId,
            recordedBy,
          ],
        );
      }
      saved.push(id);
    }
    return { ids: saved, status };
  }

  private async alertApprovedResultPerformance(
    tenantId: string,
    ds: any,
    result: any,
  ) {
    try {
      const scoreSheets = await runDbQuery(
        ds,
        `SELECT ss.student_id as "studentId",
                ss.student_score as "studentScore",
                ss.obtainable_score as "obtainableScore",
                ss.category_id as "categoryId",
                ss.subcategory_id as "subcategoryId",
                COALESCE(cat.name, ss.category_id) as "categoryName",
                COALESCE(sub.name, ss.subcategory_id) as "subcategoryName"
         FROM academic_score_sheets ss
         LEFT JOIN academic_score_categories cat ON cat.id = ss.category_id
         LEFT JOIN academic_score_subcategories sub ON sub.id = ss.subcategory_id
         WHERE ss.student_id = ? AND ss.class_id = ? AND ss.subject_id = ? AND ss.term_id = ?
           AND ss.student_score IS NOT NULL`,
        [result.student_id, result.class_id, result.subject_id, result.term_id],
      );
      const entries = scoreSheets.length > 0
        ? scoreSheets
        : [
            {
              studentId: result.student_id,
              studentScore: result.ca_score,
              obtainableScore: 40,
              categoryName: 'Continuous Assessment',
            },
            {
              studentId: result.student_id,
              studentScore: result.exam_score,
              obtainableScore: 60,
              categoryName: 'Examination',
            },
          ];
      for (const scoreSheet of entries as any[]) {
        await this.alertParentOfLowPerformance(
          tenantId,
          ds,
          scoreSheet,
          { subjectId: result.subject_id },
          scoreSheet.categoryName,
          scoreSheet.subcategoryName,
        );
      }
    } catch (error) {
      this.logger.warn(`Approved performance alert lookup failed: ${error}`);
    }
  }

  private async alertParentOfLowPerformance(
    tenantId: string,
    ds: any,
    entry: any,
    body: any,
    categoryName: string,
    subcategoryName?: string,
  ) {
    const score = Number(entry.studentScore);
    const obtainable = Number(entry.obtainableScore);
    const percentage = calculatePerformancePercentage(entry.studentScore, obtainable);
    if (percentage === null) {
      this.logger.warn(
        `[LOW_PERFORMANCE_ALERT] skipped_invalid_score student=${entry.studentId} score=${entry.studentScore} obtainable=${entry.obtainableScore}`,
      );
      return;
    }
    if (!isLowPerformance(entry.studentScore, obtainable)) return;

    try {
      const [studentRows, subjectRows, parentRows, matrixRows] = await Promise.all([
        runDbQuery(ds, `SELECT name FROM users WHERE id = ? LIMIT 1`, [entry.studentId]),
        runDbQuery(ds, `SELECT name FROM academic_subjects WHERE id = ? LIMIT 1`, [body.subjectId]),
        runDbQuery(
          ds,
          `SELECT p.id as "parentId", p.name, p.email, p.phone
           FROM parent_student_links l
           JOIN users p ON p.id = l.parent_id
           WHERE l.student_id = ?`,
          [entry.studentId],
        ),
        runDbQuery(ds, `SELECT grade_bands as "gradeBands" FROM academic_grading_matrix WHERE id = 'default' LIMIT 1`),
      ]);
      const studentName = studentRows[0]?.name || entry.studentId;
      const subjectName = subjectRows[0]?.name || body.subjectId;
      const rawBands = matrixRows[0]?.gradeBands;
      const bands = rawBands
        ? typeof rawBands === 'string' ? JSON.parse(rawBands) : rawBands
        : DEFAULT_GRADING_MATRIX.gradeBands;
      const band = (Array.isArray(bands) ? bands : []).find(
        (item: any) => percentage >= Number(item.min) && percentage <= Number(item.max),
      );
      const assessment = [categoryName, subcategoryName].filter(Boolean).join(' · ');
      const message =
        `${studentName}'s ${assessment} score in ${subjectName} is ` +
        `${Math.round(percentage * 100) / 100}% (${score}/${obtainable}), below 50%. ` +
        `Grade band: ${band?.grade_name || 'Needs improvement'}.`;
      this.logger.log(
        `[LOW_PERFORMANCE_ALERT] triggered tenant=${tenantId} student=${entry.studentId} subject=${subjectName} assessment=${assessment} percentage=${Math.round(percentage * 100) / 100} parents=${parentRows.length}`,
      );

      for (const parent of parentRows as any[]) {
        if (this.notifications) {
          try {
            const notification = await this.notifications.create({
              tenantId,
              userId: parent.parentId,
              title: 'Low academic performance alert',
              message,
              type: 'warning',
              href: '/dashboard/parent',
            });
            this.logger.log(
              `[LOW_PERFORMANCE_ALERT] notification_created notification=${notification.id} parent=${parent.parentId} student=${entry.studentId} subject=${subjectName} assessment=${assessment} percentage=${Math.round(percentage * 100) / 100}`,
            );
          } catch (error) {
            this.logger.warn(
              `[LOW_PERFORMANCE_ALERT] notification_error parent=${parent.parentId} student=${entry.studentId}: ${error}`,
            );
          }
        } else {
          this.logger.warn(
            `[LOW_PERFORMANCE_ALERT] notification_unavailable parent=${parent.parentId} student=${entry.studentId}`,
          );
        }
        if (this.comms && parent.email) {
          await this.comms.sendEmail(tenantId, {
            to: parent.email,
            subject: 'Low academic performance alert',
            text: message,
          }).catch((error) => this.logger.warn(`Performance alert email failed: ${error}`));
        }
        if (this.comms && parent.phone) {
          await this.comms.sendSms(tenantId, {
            to: parent.phone,
            message,
          }).catch((error) => this.logger.warn(`Performance alert SMS failed: ${error}`));
        }
      }
    } catch (error) {
      this.logger.warn(`Performance alert analysis failed: ${error}`);
    }
  }

  async publishScoreSheets(tenantId: string, actorUserId: string, ids: string[]) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (!roles.includes('subject_teacher')) {
      throw new ForbiddenException('Only subject teachers can publish score sheets');
    }
    if (!ids.length) throw new BadRequestException('At least one score sheet is required');
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, class_id as "classId", subject_id as "subjectId", status
       FROM academic_score_sheets WHERE id = ANY(?)`,
      [ids],
    );
    if (rows.length !== new Set(ids).size) {
      throw new NotFoundException('One or more score sheets were not found');
    }
    for (const row of rows) {
      await this.assertTeacherMayAccess(ds, actorUserId, row.classId, row.subjectId);
      if (row.status !== 'draft') {
        throw new BadRequestException('Only draft score sheets can be published');
      }
    }
    await runDbQuery(
      ds,
      `UPDATE academic_score_sheets SET status = 'published', updated_at = NOW()
       WHERE id = ANY(?)`,
      [ids],
    );
    return { publishedCount: rows.length, ids: rows.map((row) => row.id) };
  }

  async deleteScoreSheet(tenantId: string, actorUserId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (!roles.includes('subject_teacher')) {
      throw new ForbiddenException('Only subject teachers can delete score sheet drafts');
    }
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT class_id as "classId", subject_id as "subjectId", status,
              recorder_user_id as "recorderUserId"
       FROM academic_score_sheets WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!rows[0]) throw new NotFoundException('Score sheet not found');
    await this.assertTeacherMayAccess(ds, actorUserId, rows[0].classId, rows[0].subjectId);
    if (rows[0].status !== 'draft' || rows[0].recorderUserId !== actorUserId) {
      throw new ForbiddenException('Only the recording teacher can delete a draft score sheet');
    }
    await runDbQuery(ds, `DELETE FROM academic_score_sheets WHERE id = ?`, [id]);
    return { id };
  }

  async listResults(tenantId: string, actorUserId?: string, force = false) {
    return this.listCache.getOrLoad(
      `tenant:${tenantId}:academic:results:${actorUserId || 'all'}`,
      () => this.listResultsUncached(tenantId, actorUserId),
      force,
    );
  }

  private async listResultsUncached(tenantId: string, actorUserId?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await ds.query(
      `SELECT r.id, r.student_id as studentId, r.class_id as classId, r.subject_id as subjectId, r.term_id as termId,
              r.ca_score as caScore, r.exam_score as examScore, r.total_score as totalScore, r.grade, r.status,
              r.approval_comment as approvalComment, r.approved_by as approvedBy, r.approved_at as approvedAt,
              c.school_level as schoolLevel
       FROM academic_results r
       LEFT JOIN academic_classes c ON c.id = r.class_id
       ORDER BY r.created_at DESC LIMIT 500`,
    );
    const audienceScoped = await this.filterStudentParentResults(ds, actorUserId, rows);
    const levelScoped = await this.filterByActorSchoolLevel(ds, actorUserId, audienceScoped);
    if (!actorUserId) return levelScoped;
    const scope = await this.getTeacherScope(ds, actorUserId);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (
      !scope.isTeacher ||
      hasAcademicManagementRole(roles)
    ) {
      return levelScoped;
    }
    return levelScoped.filter(
      (row: any) =>
        scope.classIds.includes(String(row.classid ?? row.classId)) ||
        scope.subjectIds.includes(String(row.subjectid ?? row.subjectId)),
    );
  }

  async listGeneratedResults(
    tenantId: string,
    actorUserId: string,
    filters: { sessionId?: string; termId?: string; classId?: string } = {},
    force = false,
  ) {
    const key = `tenant:${tenantId}:academic:generated-results:${actorUserId}:${stableStringify(filters)}`;
    return this.listCache.getOrLoad(
      key,
      () => this.listGeneratedResultsUncached(tenantId, actorUserId, filters),
      force,
    );
  }

  private async listGeneratedResultsUncached(
    tenantId: string,
    actorUserId: string,
    filters: { sessionId?: string; termId?: string; classId?: string } = {},
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const roles = await this.getUserRoles(ds, actorUserId);
    const isStudent = roles.includes('student');
    const isParent = roles.includes('parent');
    if (!isStudent && !isParent && !['director', 'school_admin', 'head_teacher', 'principal', 'vice_principal', 'assistant_head_teacher'].some((role) => roles.includes(role))) {
      throw new ForbiddenException('Generated results are restricted to academic management roles');
    }
    const clauses = ['1 = 1'];
    const params: any[] = [];
    if (isStudent) {
      clauses.push('agr.student_id = ?', 'agr.release_status = ?');
      params.push(actorUserId, 'approved');
    } else if (isParent) {
      clauses.push(
        'agr.release_status = ?',
        'EXISTS (SELECT 1 FROM parent_student_links psl WHERE psl.parent_id = ? AND psl.student_id = agr.student_id)',
      );
      params.push('approved', actorUserId);
    }
    for (const [column, value] of [
      ['session_id', filters.sessionId],
      ['term_id', filters.termId],
      ['class_id', filters.classId],
    ] as Array<[string, string | undefined]>) {
      if (value) {
        clauses.push(`agr.${column} = ?`);
        params.push(value);
      }
    }
    const rows = await runDbQuery(
      ds,
      `SELECT agr.id, agr.session_id as "sessionId", agr.term_id as "termId", agr.class_id as "classId",
          agr.student_id as "studentId", agr.student_name as "studentName",
          COALESCE(agr.student_gender, stu.gender) as "studentGender",
          ses.name as "sessionName", trm.name as "termName",
          agr.student_school_level as "studentSchoolLevel", agr.student_class_name as "studentClassName",
          agr.student_class_code as "studentClassCode",
          agr.admission_no as "admissionNo", agr.student_subject_records as "studentSubjectRecords",
          agr.release_status as "releaseStatus", agr.approved_by as "approvedBy", agr.approved_at as "approvedAt",
          agr.generated_by as "generatedBy", agr.generated_at as "generatedAt",
          agr.no_of_times_school_opened as "noOfTimesSchoolOpened",
          agr.no_of_times_present as "noOfTimesPresent", agr.no_of_times_absent as "noOfTimesAbsent",
          agr.term_ended as "termEnded", agr.next_term_begins as "nextTermBegins",
          agr.created_at as "createdAt", agr.updated_at as "updatedAt",
          c.school_level as "schoolLevel"
       FROM academic_generated_results agr
       LEFT JOIN academic_classes c ON c.id = agr.class_id
       LEFT JOIN users stu ON stu.id = agr.student_id
       LEFT JOIN academic_sessions ses ON ses.id = agr.session_id
       LEFT JOIN academic_terms trm ON trm.id = agr.term_id
       WHERE ${clauses.join(' AND ')}
       ORDER BY agr.student_name`,
      params,
    );
    const scoped = await this.filterByActorSchoolLevel(ds, actorUserId, rows);
    return scoped.map((row: any) => {
      try {
        row.studentSubjectRecords = typeof row.studentSubjectRecords === 'string'
          ? JSON.parse(row.studentSubjectRecords)
          : row.studentSubjectRecords || [];
      } catch {
        row.studentSubjectRecords = [];
      }
      return row;
    });
  }

  async getGeneratedResult(tenantId: string, actorUserId: string, id: string) {
    const rows = await this.listGeneratedResults(tenantId, actorUserId);
    const result = rows.find((row: any) => String(row.id) === String(id));
    if (!result) throw new NotFoundException('Generated result not found');
    return result;
  }

  async releaseGeneratedResult(tenantId: string, actorUserId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (!['director', 'school_admin', 'head_teacher', 'principal', 'vice_principal', 'assistant_head_teacher'].some((role) => roles.includes(role))) {
      throw new ForbiddenException('Only academic management roles can release results');
    }
    const users: any[] = await runDbQuery(ds, `SELECT name FROM users WHERE id = ? LIMIT 1`, [actorUserId]);
    const result: any[] = await runDbQuery(
      ds,
      `SELECT release_status as "releaseStatus" FROM academic_generated_results WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!result[0]) throw new NotFoundException('Generated result not found');
    if (result[0].releaseStatus === 'approved') return { id, releaseStatus: 'approved' };
    await runDbQuery(
      ds,
      `UPDATE academic_generated_results SET release_status = 'approved',
          approved_by = ?, approved_at = NOW(), updated_at = NOW() WHERE id = ?`,
      [users[0]?.name || actorUserId, id],
    );
    this.listCache.invalidate(`tenant:${tenantId}:academic:generated-results:`);
    return { id, releaseStatus: 'approved', approvedBy: users[0]?.name || actorUserId };
  }

  async listResultsSummary(tenantId: string, actorUserId?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await ds.query(
      `SELECT
         r.student_id as studentId,
         COALESCE(u.name, '') as studentName,
         r.class_id as classId,
         COALESCE(c.name, '') as className,
         c.school_level as schoolLevel,
         r.term_id as termId,
         COALESCE(t.name, r.term_id) as termName,
         COUNT(*)::int as totalSubjects,
         COALESCE(AVG(r.total_score), 0)::float as averageScore,
         CASE
           WHEN bool_and(r.status = 'approved') THEN 'approved'
           WHEN bool_or(r.status = 'submitted') THEN 'submitted'
           ELSE 'draft'
         END as status,
         COALESCE(json_agg(
           json_build_object(
             'resultId', r.id,
             'subjectId', r.subject_id,
             'subjectName', s.name,
             'caScore', r.ca_score,
             'examScore', r.exam_score,
             'totalScore', r.total_score,
             'grade', r.grade,
             'status', r.status
           )
           ORDER BY s.name
         ) FILTER (WHERE r.id IS NOT NULL), '[]'::json) as examSubjects
       FROM academic_results r
       LEFT JOIN users u ON u.id = r.student_id
       LEFT JOIN academic_classes c ON c.id = r.class_id
       LEFT JOIN academic_subjects s ON s.id = r.subject_id
       LEFT JOIN academic_terms t ON t.id = r.term_id
       GROUP BY r.student_id, u.name, r.class_id, c.name, c.school_level, r.term_id, t.name
       ORDER BY u.name NULLS LAST, c.name NULLS LAST, r.term_id`,
    );
    const mapped = rows.map((r: any) =>
      mapDataToUpdateKeys(r, {
        studentid: 'studentId',
        classid: 'classId',
        studentname: 'studentName',
        classname: 'className',
        termid: 'termId',
        termname: 'termName',
        totalsubjects: 'totalSubjects',
        averagescore: 'averageScore',
        examsubjects: 'examSubjects',
      }),
    );
    const audienceScoped = await this.filterStudentParentResults(ds, actorUserId, mapped);
    const levelScoped = await this.filterByActorSchoolLevel(ds, actorUserId, audienceScoped);
    if (!actorUserId) return levelScoped;
    const scope = await this.getTeacherScope(ds, actorUserId);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (
      !scope.isTeacher ||
      hasAcademicManagementRole(roles)
    ) {
      return levelScoped;
    }
    return levelScoped.filter(
      (row: any) =>
        scope.classIds.includes(String(row.classId ?? row.classid)) ||
        scope.subjectIds.some((subjectId) =>
          (row.examSubjects || []).some(
            (subject: any) =>
              String(subject.subjectId ?? subject.subjectid) ===
              String(subjectId),
          ),
        ),
    );
  }

  async upsertResult(tenantId: string, id: string | null, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertActorSchoolLevel(ds, body.actorUserId, body.classId, body.subjectId);
    if (body.actorUserId) {
      const existing = id
        ? await this.getRowOrThrow(
            ds,
            'academic_results',
            id,
            'Result not found',
          )
        : null;
      await this.assertTeacherMayAccess(
        ds,
        body.actorUserId,
        body.classId ?? existing?.class_id,
        body.subjectId ?? existing?.subject_id,
      );
    }
    if (body.status === 'approved') {
      throw new BadRequestException(
        'Use the approve endpoint to approve results',
      );
    }
    const requestedStatus =
      body.status === 'submitted'
        ? 'submitted'
        : body.status === 'draft'
        ? 'draft'
        : undefined;
    if (!id) {
      const resultId = randomToken('res');
      let total = body.totalScore ?? null;
      let grade = body.grade || null;
      if (
        this.gradingMatrix &&
        (body.caScore != null || body.examScore != null)
      ) {
        const computed = await this.gradingMatrix.computeTotal(
          tenantId,
          body.caScore ?? null,
          body.examScore ?? null,
        );
        if (body.totalScore === undefined && computed.total != null)
          total = computed.total;
        if (!body.grade && computed.grade) grade = computed.grade;
      }
      await runDbQuery(
        ds,
        `INSERT INTO academic_results (id, student_id, class_id, subject_id, term_id, ca_score, exam_score, total_score, grade, status, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
        [
          resultId,
          body.studentId || null,
          body.classId,
          body.subjectId,
          body.termId,
          body.caScore ?? null,
          body.examScore ?? null,
          total,
          grade,
          requestedStatus || 'draft',
        ],
      );
      if (body.studentId && body.classId) {
        await this.ensureStudentClassEnrollment(
          ds,
          body.studentId,
          body.classId,
        );
      }
      return { id: resultId };
    }
    const existing = await this.getRowOrThrow(
      ds,
      'academic_results',
      id,
      'Result not found',
    );
    const merged = {
      studentId:
        body.studentId !== undefined ? body.studentId : existing.student_id,
      classId: body.classId ?? existing.class_id,
      subjectId: body.subjectId ?? existing.subject_id,
      termId: body.termId ?? existing.term_id,
      caScore: body.caScore !== undefined ? body.caScore : existing.ca_score,
      examScore:
        body.examScore !== undefined ? body.examScore : existing.exam_score,
      totalScore:
        body.totalScore !== undefined ? body.totalScore : existing.total_score,
      grade: body.grade !== undefined ? body.grade : existing.grade,
      status: requestedStatus ?? existing.status ?? 'draft',
    };
    if (
      this.gradingMatrix &&
      (body.caScore !== undefined || body.examScore !== undefined) &&
      body.totalScore === undefined
    ) {
      const computed = await this.gradingMatrix.computeTotal(
        tenantId,
        merged.caScore ?? null,
        merged.examScore ?? null,
      );
      if (computed.total != null) merged.totalScore = computed.total;
      if (!body.grade && computed.grade) merged.grade = computed.grade;
    }
    await runDbQuery(
      ds,
      `UPDATE academic_results SET student_id = ?, class_id = ?, subject_id = ?, term_id = ?, ca_score = ?, exam_score = ?, total_score = ?, grade = ?, status = ?, updated_at = NOW() WHERE id = ?`,
      [
        merged.studentId || null,
        merged.classId,
        merged.subjectId,
        merged.termId,
        merged.caScore ?? null,
        merged.examScore ?? null,
        merged.totalScore ?? null,
        merged.grade || null,
        merged.status,
        id,
      ],
    );
    if (merged.studentId && merged.classId) {
      await this.ensureStudentClassEnrollment(
        ds,
        merged.studentId,
        merged.classId,
      );
    }
    return { id };
  }

  private async ensureStudentClassEnrollment(
    ds: any,
    studentId: string,
    classId: string,
  ) {
    const existing: any[] = await runDbQuery(
      ds,
      `SELECT id FROM student_class_enrollments WHERE student_id = ? AND class_id = ? LIMIT 1`,
      [studentId, classId],
    );
    if (existing?.[0]) return;
    await runDbQuery(
      ds,
      `INSERT INTO student_class_enrollments (id, student_id, class_id) VALUES (?, ?, ?)`,
      [randomToken('enr'), studentId, classId],
    );
  }

  async approveResult(
    tenantId: string,
    id: string,
    approverId?: string,
    comment?: string,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing = await this.getRowOrThrow(
      ds,
      'academic_results',
      id,
      'Result not found',
    );
    const currentStatus = existing.status || 'draft';
    if (currentStatus === 'approved') {
      return { id, status: 'approved' };
    }
    if (currentStatus !== 'submitted' && currentStatus !== 'draft') {
      throw new BadRequestException(
        `Cannot approve result with status '${currentStatus}'`,
      );
    }
    await runDbQuery(
      ds,
      `UPDATE academic_results
       SET status = 'approved', approval_comment = ?, approved_by = ?, approved_at = NOW(), updated_at = NOW()
       WHERE id = ?`,
      [comment?.trim() || null, approverId || null, id],
    );
    void this.alertApprovedResultPerformance(tenantId, ds, existing);
    return { id, status: 'approved' };
  }

  async approveResultsBatch(
    tenantId: string,
    body: { studentId?: string; classId?: string; termId?: string },
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    if (!body.studentId && !body.classId && !body.termId) {
      throw new BadRequestException(
        'Provide at least one of studentId, classId, or termId',
      );
    }
    const clauses: string[] = [`status IN ('draft', 'submitted')`];
    const params: any[] = [];
    if (body.studentId) {
      clauses.push('student_id = ?');
      params.push(body.studentId);
    }
    if (body.classId) {
      clauses.push('class_id = ?');
      params.push(body.classId);
    }
    if (body.termId) {
      clauses.push('term_id = ?');
      params.push(body.termId);
    }
    const where = clauses.join(' AND ');
    const matching: any[] = await runDbQuery(
      ds,
      `SELECT id, student_id, class_id, subject_id, term_id
       FROM academic_results WHERE ${where}`,
      params,
    );
    if (matching.length) {
      await runDbQuery(
        ds,
        `UPDATE academic_results SET status = 'approved', updated_at = NOW() WHERE ${where}`,
        params,
      );
      for (const result of matching) {
        void this.alertApprovedResultPerformance(tenantId, ds, result);
      }
    }
    return { approvedCount: matching.length, ids: matching.map((r) => r.id) };
  }

  async deleteResult(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(ds, `DELETE FROM academic_results WHERE id = ?`, [id]);
    return { id };
  }

  async linkTeacherToClasses(
    tenantId: string,
    teacherId: string,
    classIds: string[],
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const teacherRoles = await this.getUserRoles(ds, teacherId);
    const canAssignClasses =
      teacherRoles.includes('class_teacher') || teacherRoles.includes('subject_teacher');
    if (!canAssignClasses) {
      throw new BadRequestException(
        'Only class_teacher or subject_teacher can be assigned classes',
      );
    }
    if (classIds.length) {
      const rows = await runDbQuery(
        ds,
        `SELECT id FROM academic_classes WHERE is_active = true AND id IN (${classIds.map(() => '?').join(',')})`,
        classIds,
      );
      if (rows.length !== new Set(classIds).size) {
        throw new BadRequestException('Teacher scope contains an unknown or inactive class');
      }
    }
    const existing: Array<{ class_id: string }> = await runDbQuery(
      ds,
      `SELECT class_id FROM academic_class_teacher_links WHERE teacher_id = ?`,
      [teacherId],
    );
    const existingSet = new Set(existing.map((r) => r.class_id));
    const desiredSet = new Set(classIds);
    // Insert missing
    for (const classId of desiredSet) {
      if (!existingSet.has(classId)) {
        await runDbQuery(
          ds,
          `INSERT INTO academic_class_teacher_links (id, class_id, teacher_id) VALUES (?, ?, ?)`,
          [randomToken('ctl'), classId, teacherId],
        );
      }
    }
    // Remove extra
    for (const classId of existingSet) {
      if (!desiredSet.has(classId)) {
        await runDbQuery(
          ds,
          `DELETE FROM academic_class_teacher_links WHERE class_id = ? AND teacher_id = ?`,
          [classId, teacherId],
        );
      }
    }
    this.listCache.invalidate(`tenant:${tenantId}:academic:`);
    return { teacherId, classIds: Array.from(desiredSet) };
  }

  async linkTeacherToSubjects(
    tenantId: string,
    teacherId: string,
    subjectIds: string[],
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const teacherRoles = await this.getUserRoles(ds, teacherId);
    const canAssignSubjects = teacherRoles.includes('subject_teacher');
    if (!canAssignSubjects) {
      throw new BadRequestException(
        'Only subject_teacher can be assigned subjects',
      );
    }
    if (subjectIds.length) {
      const rows = await runDbQuery(
        ds,
        `SELECT id FROM academic_subjects WHERE is_active = true AND id IN (${subjectIds.map(() => '?').join(',')})`,
        subjectIds,
      );
      if (rows.length !== new Set(subjectIds).size) {
        throw new BadRequestException('Teacher scope contains an unknown or inactive subject');
      }
    }
    const existing: Array<{ subject_id: string }> = await runDbQuery(
      ds,
      `SELECT subject_id FROM academic_subject_teacher_links WHERE teacher_id = ?`,
      [teacherId],
    );
    const existingSet = new Set(existing.map((r) => r.subject_id));
    const desiredSet = new Set(subjectIds);
    for (const subjectId of desiredSet) {
      if (!existingSet.has(subjectId)) {
        await runDbQuery(
          ds,
          `INSERT INTO academic_subject_teacher_links (id, subject_id, teacher_id) VALUES (?, ?, ?)`,
          [randomToken('stl'), subjectId, teacherId],
        );
      }
    }
    for (const subjectId of existingSet) {
      if (!desiredSet.has(subjectId)) {
        await runDbQuery(
          ds,
          `DELETE FROM academic_subject_teacher_links WHERE subject_id = ? AND teacher_id = ?`,
          [subjectId, teacherId],
        );
      }
    }
    this.listCache.invalidate(`tenant:${tenantId}:academic:`);
    return { teacherId, subjectIds: Array.from(desiredSet) };
  }

  /**
   * Assign both sides of a teacher's academic scope in one transaction. The
   * UI used to create a user and then issue two unrelated link requests, which
   * could leave a partially configured teacher when the second request failed.
   */
  async linkTeacherScope(
    tenantId: string,
    teacherId: string,
    classIds: string[],
    subjectIds: string[],
    schoolLevels: string[] = [],
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const uniqueClassIds = [...new Set(classIds)];
    const uniqueSubjectIds = [...new Set(subjectIds)];
    const levels = [...new Set(schoolLevels.map((level) => level.toLowerCase()))];

    await ds.transaction(async (manager: any) => {
      const userRows = await manager.query(
        'SELECT roles FROM users WHERE id = $1 LIMIT 1',
        [teacherId],
      );
      if (!userRows[0]) throw new BadRequestException('Teacher not found');
      const roles = Array.isArray(userRows[0].roles)
        ? userRows[0].roles
        : JSON.parse(userRows[0].roles || '[]');
      if (
        (uniqueClassIds.length &&
          !roles.includes('class_teacher') &&
          !roles.includes('subject_teacher')) ||
        (uniqueSubjectIds.length && !roles.includes('subject_teacher'))
      ) {
        throw new BadRequestException(
          'Teacher roles do not permit the requested class or subject scope',
        );
      }

      if (uniqueClassIds.length) {
        const rows = await manager.query(
          `SELECT id, school_level FROM academic_classes
           WHERE is_active = true AND id = ANY($1::varchar[])`,
          [uniqueClassIds],
        );
        if (rows.length !== uniqueClassIds.length) {
          throw new BadRequestException('Teacher scope contains an unknown or inactive class');
        }
        if (
          levels.length &&
          rows.some((row: any) => !levels.includes(String(row.school_level).toLowerCase()))
        ) {
          throw new BadRequestException('Selected classes do not match the selected school levels');
        }
      }
      if (uniqueSubjectIds.length) {
        const rows = await manager.query(
          `SELECT id, school_level FROM academic_subjects
           WHERE is_active = true AND id = ANY($1::varchar[])`,
          [uniqueSubjectIds],
        );
        if (rows.length !== uniqueSubjectIds.length) {
          throw new BadRequestException('Teacher scope contains an unknown or inactive subject');
        }
        if (
          levels.length &&
          rows.some((row: any) => {
            const level = String(row.school_level).toLowerCase();
            return !levels.includes(level) && !(levels.includes('jss') || levels.includes('sss')) &&
              !(level === 'secondary' && levels.some((value) => value === 'jss' || value === 'sss'));
          })
        ) {
          throw new BadRequestException('Selected subjects do not match the selected school levels');
        }
      }

      await manager.query(
        'DELETE FROM academic_class_teacher_links WHERE teacher_id = $1',
        [teacherId],
      );
      await manager.query(
        'DELETE FROM academic_subject_teacher_links WHERE teacher_id = $1',
        [teacherId],
      );
      for (const classId of uniqueClassIds) {
        await manager.query(
          `INSERT INTO academic_class_teacher_links (id, class_id, teacher_id)
           VALUES ($1, $2, $3)`,
          [randomToken('ctl'), classId, teacherId],
        );
      }
      for (const subjectId of uniqueSubjectIds) {
        await manager.query(
          `INSERT INTO academic_subject_teacher_links (id, subject_id, teacher_id)
           VALUES ($1, $2, $3)`,
          [randomToken('stl'), subjectId, teacherId],
        );
      }
    });
    this.listCache.invalidate(`tenant:${tenantId}:academic:`);

    return { teacherId, classIds: uniqueClassIds, subjectIds: uniqueSubjectIds, schoolLevels: levels };
  }

  async linkParentToStudents(
    tenantId: string,
    parentId: string,
    studentIds: string[],
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const parentRoles = await this.getUserRoles(ds, parentId);
    if (!parentRoles.includes('parent')) {
      throw new BadRequestException(
        'Provided parentId does not belong to a parent user',
      );
    }
    for (const studentId of studentIds) {
      const studentRoles = await this.getUserRoles(ds, studentId);
      if (!studentRoles.includes('student')) {
        throw new BadRequestException(`User ${studentId} is not a student`);
      }
    }
    const existing: Array<{ student_id: string }> = await runDbQuery(
      ds,
      `SELECT student_id FROM parent_student_links WHERE parent_id = ?`,
      [parentId],
    );
    const existingSet = new Set(existing.map((r) => r.student_id));
    const desiredSet = new Set(studentIds);
    for (const studentId of desiredSet) {
      if (!existingSet.has(studentId)) {
        await runDbQuery(
          ds,
          `INSERT INTO parent_student_links (id, parent_id, student_id) VALUES (?, ?, ?)`,
          [randomToken('psl'), parentId, studentId],
        );
      }
    }
    for (const studentId of existingSet) {
      if (!desiredSet.has(studentId)) {
        await runDbQuery(
          ds,
          `DELETE FROM parent_student_links WHERE parent_id = ? AND student_id = ?`,
          [parentId, studentId],
        );
      }
    }
    return { parentId, studentIds: Array.from(desiredSet) };
  }

  async getTeacherLinks(tenantId: string, teacherId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const classes: any[] = await runDbQuery(
      ds,
      `SELECT class_id as "classId" FROM academic_class_teacher_links WHERE teacher_id = ?`,
      [teacherId],
    );
    const subjects: any[] = await runDbQuery(
      ds,
      `SELECT subject_id as "subjectId" FROM academic_subject_teacher_links WHERE teacher_id = ?`,
      [teacherId],
    );
    return {
      teacherId,
      classIds: classes
        .map((r) => r.classId ?? r.classid)
        .filter((id) => id != null)
        .map(String),
      subjectIds: subjects
        .map((r) => r.subjectId ?? r.subjectid)
        .filter((id) => id != null)
        .map(String),
    };
  }

  async getParentLinks(tenantId: string, parentId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT student_id as "studentId" FROM parent_student_links WHERE parent_id = ?`,
      [parentId],
    );
    return { parentId, studentIds: rows.map((r) => r.studentId) };
  }

  async getStudentHistory(tenantId: string, studentId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const [events, promotions, results] = await Promise.all([
      runDbQuery(ds, `SELECT id, session_id as "sessionId", term_id as "termId",
        class_id as "classId", event_type as "eventType", payload,
        recorded_by as "recordedBy", recorded_at as "recordedAt"
        FROM academic_student_history WHERE student_id = ? ORDER BY recorded_at ASC`, [studentId]),
      runDbQuery(ds, `SELECT id, from_class_id as "fromClassId", to_class_id as "toClassId",
        session_id as "sessionId", promoted_by as "promotedBy", notes,
        promoted_at as "promotedAt" FROM academic_student_promotions
        WHERE student_id = ? ORDER BY promoted_at ASC`, [studentId]),
      runDbQuery(ds, `SELECT agr.id, agr.session_id as "sessionId",
        agr.term_id as "termId", agr.class_id as "classId",
        agr.student_name as "studentName",
        agr.student_class_name as "className",
        agr.admission_no as "admissionNo",
        agr.student_subject_records as "studentSubjectRecords",
        agr.release_status as "releaseStatus",
        agr.generated_by as "generatedBy",
        agr.approved_by as "approvedBy",
        agr.generated_at as "generatedAt",
        ses.name as "sessionName", trm.name as "termName"
        FROM academic_generated_results agr
        LEFT JOIN academic_sessions ses ON ses.id = agr.session_id
        LEFT JOIN academic_terms trm ON trm.id = agr.term_id
        WHERE agr.student_id = ? ORDER BY agr.generated_at ASC`, [studentId]),
    ]);
    results.forEach((row: any) => {
      row.studentSubjectRecords = typeof row.studentSubjectRecords === 'string'
        ? JSON.parse(row.studentSubjectRecords)
        : row.studentSubjectRecords || [];
    });
    return { studentId, events, promotions, results };
  }

  async promoteStudent(tenantId: string, body: {
    studentId: string; toClassId: string; sessionId?: string; promotedBy?: string; notes?: string;
  }) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const target = await runDbQuery(ds, `SELECT id FROM academic_classes WHERE id = ? AND is_active = true`, [body.toClassId]);
    if (!target.length) throw new NotFoundException('Target class not found');
    const current = await runDbQuery(ds, `SELECT class_id as "classId" FROM student_class_enrollments
      WHERE student_id = ? ORDER BY created_at DESC LIMIT 1`, [body.studentId]);
    const promotionId = randomToken('promotion');
    await runDbQuery(ds, `INSERT INTO academic_student_promotions
      (id, student_id, from_class_id, to_class_id, session_id, promoted_by, notes)
      VALUES (?, ?, ?, ?, ?, ?, ?)`,
      [promotionId, body.studentId, current[0]?.classId || null, body.toClassId,
        body.sessionId || null, body.promotedBy || null, body.notes || null]);
    await runDbQuery(ds, `INSERT INTO academic_student_history
      (id, student_id, session_id, class_id, event_type, payload, recorded_by)
      VALUES (?, ?, ?, ?, 'promotion', ?, ?)`,
      [randomToken('history'), body.studentId, body.sessionId || null, body.toClassId,
        JSON.stringify({ fromClassId: current[0]?.classId || null, toClassId: body.toClassId }), body.promotedBy || null]);
    await runDbQuery(ds, `DELETE FROM student_class_enrollments WHERE student_id = ?`, [body.studentId]);
    await this.ensureStudentClassEnrollment(ds, body.studentId, body.toClassId);
    return { id: promotionId, studentId: body.studentId, fromClassId: current[0]?.classId || null, toClassId: body.toClassId };
  }

  async getClassEnrollments(tenantId: string, classId: string, actorUserId?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertActorSchoolLevel(ds, actorUserId, classId);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT e.student_id as "studentId", u.name as "studentName", u.email as "studentEmail"
       FROM student_class_enrollments e
       LEFT JOIN users u ON u.id = e.student_id
       WHERE e.class_id = ?
       ORDER BY u.name ASC`,
      [classId],
    );
    return { classId, students: rows };
  }

  async enrollStudentsInClass(
    tenantId: string,
    classId: string,
    studentIds: string[],
    actorUserId?: string,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await this.assertActorSchoolLevel(ds, actorUserId, classId);
    const classRows = await runDbQuery(
      ds,
      `SELECT id FROM academic_classes WHERE id = ? LIMIT 1`,
      [classId],
    );
    if (!classRows?.[0]) throw new NotFoundException('Class not found');

    for (const studentId of studentIds) {
      const roles = await this.getUserRoles(ds, studentId);
      if (!roles.includes('student')) {
        throw new BadRequestException(`User ${studentId} is not a student`);
      }
    }

    const existing: any[] = await runDbQuery(
      ds,
      `SELECT student_id FROM student_class_enrollments WHERE class_id = ?`,
      [classId],
    );
    const existingSet = new Set(existing.map((r) => r.student_id));
    const desiredSet = new Set(studentIds);

    for (const studentId of desiredSet) {
      if (!existingSet.has(studentId)) {
        await runDbQuery(
          ds,
          `INSERT INTO student_class_enrollments (id, student_id, class_id) VALUES (?, ?, ?)`,
          [randomToken('enr'), studentId, classId],
        );
      }
    }
    for (const studentId of existingSet) {
      if (!desiredSet.has(studentId)) {
        await runDbQuery(
          ds,
          `DELETE FROM student_class_enrollments WHERE class_id = ? AND student_id = ?`,
          [classId, studentId],
        );
      }
    }

    return { classId, studentIds: Array.from(desiredSet) };
  }

  private async getTeacherScope(ds: any, userId: string) {
    return loadTeacherScope(ds, userId);
  }

  private async filterStudentParentResults(
    ds: any,
    actorUserId: string | undefined,
    rows: any[],
  ) {
    if (!actorUserId) return rows;
    const roles = await this.getUserRoles(ds, actorUserId);
    if (!roles.includes('student') && !roles.includes('parent')) return rows;

    const studentIds = roles.includes('student')
      ? [actorUserId]
      : (
        await runDbQuery(
          ds,
          `SELECT student_id as "studentId"
           FROM parent_student_links
           WHERE parent_id = ?`,
          [actorUserId],
        )
      ).map((row: any) => String(row.studentId ?? row.studentid));
    const visible = new Set(studentIds);
    return rows
      .filter((row: any) => visible.has(String(row.studentId ?? row.studentid)))
      .map((row: any) => {
        if (!Array.isArray(row.examSubjects)) return row;
        return {
          ...row,
          examSubjects: row.examSubjects.filter(
            (subject: any) => subject.status === 'approved',
          ),
        };
      });
  }

  async actorScope(tenantId: string, userId: string) {
    const ds = await this.getTenantDs(tenantId);
    const roles = await this.getUserRoles(ds, userId);
    const scope = await this.getTeacherScope(ds, userId);
    const canApproveResults = roles.some((role) =>
      ['principal', 'head_teacher', 'director', 'school_admin'].includes(role),
    );
    return {
      isTeacher: scope.isTeacher,
      canApproveResults,
      classIds: scope.classIds,
      subjectIds: scope.subjectIds,
      subjectClassIds: scope.subjectClassIds,
      roles,
    };
  }

  async pendingApproval(tenantId: string, actorUserId: string) {
    const ds = await this.getTenantDs(tenantId);
    const roles = await this.getUserRoles(ds, actorUserId);
    if (
      !roles.some((role) =>
        ['principal', 'head_teacher', 'director', 'school_admin'].includes(role),
      )
    ) {
      return [];
    }
    return runDbQuery(
      ds,
      `SELECT r.id, r.student_id as "studentId", u.name as "studentName",
              u.admission_no as "admissionNo",
              r.class_id as "classId", c.name as "className",
              r.subject_id as "subjectId", s.name as "subjectName",
              r.term_id as "termId", t.name as "termName",
              ses.name as "sessionName", r.ca_score as "caScore",
              r.exam_score as "examScore", r.total_score as "totalScore",
              r.grade, r.status, r.updated_at as "updatedAt"
       FROM academic_results r
       LEFT JOIN users u ON u.id = r.student_id
       LEFT JOIN academic_classes c ON c.id = r.class_id
       LEFT JOIN academic_subjects s ON s.id = r.subject_id
       LEFT JOIN academic_terms t ON t.id = r.term_id
       LEFT JOIN academic_sessions ses ON ses.id = t.session_id
       WHERE r.status = 'submitted'
       ORDER BY r.updated_at DESC
       LIMIT 200`,
      [],
    );
  }

  private async assertTeacherMayAccess(
    ds: any,
    userId: string,
    classId?: string,
    subjectId?: string,
  ) {
    await assertTeacherScope(ds, userId, classId, subjectId);
  }

  private async getRowOrThrow(
    ds: any,
    table: string,
    id: string,
    notFoundMessage: string,
  ): Promise<any> {
    const rows = await runDbQuery(
      ds,
      `SELECT * FROM ${table} WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!rows?.[0]) {
      throw new NotFoundException(notFoundMessage);
    }
    return rows[0];
  }

  private async getUserRoles(ds: any, userId: string): Promise<string[]> {
    const rows = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const row = rows?.[0];
    if (!row) {
      throw new BadRequestException('User not found for assignment');
    }
    try {
      if (Array.isArray(row.roles)) return row.roles;
      if (typeof row.roles === 'string') return JSON.parse(row.roles || '[]');
    } catch {
      // fall through
    }
    return [];
  }
}
