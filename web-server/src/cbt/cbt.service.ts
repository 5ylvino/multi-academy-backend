import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

/** CBT / digital exams — create exam + questions, attempts with scoring. */
@Injectable()
export class CbtService {
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
      CREATE TABLE IF NOT EXISTS cbt_exams (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        subject_id varchar(64) NULL,
        class_id varchar(64) NULL,
        duration_minutes int NOT NULL DEFAULT 60,
        status varchar(32) NOT NULL DEFAULT 'draft',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(
      `ALTER TABLE cbt_exams ADD COLUMN IF NOT EXISTS class_id varchar(64) NULL`,
    );
    await ds.query(`
      CREATE TABLE IF NOT EXISTS cbt_questions (
        id varchar(64) PRIMARY KEY,
        exam_id varchar(64) NOT NULL,
        prompt text NOT NULL,
        options text NOT NULL DEFAULT '[]',
        correct_index int NOT NULL DEFAULT 0,
        points int NOT NULL DEFAULT 1
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS cbt_attempts (
        id varchar(64) PRIMARY KEY,
        exam_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'in_progress',
        answers text NULL,
        score int NOT NULL DEFAULT 0,
        max_score int NOT NULL DEFAULT 0,
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        submitted_at TIMESTAMP NULL
      );
    `);
  }

  async listExams(tenantId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'academic.cbt');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.getUserRoles(ds, actorId);
    if (roles.includes('parent')) return [];
    const studentScope = roles.includes('student')
      ? `WHERE e.status = 'published' AND (e.class_id IS NULL OR e.class_id IN (
          SELECT class_id FROM student_class_enrollments WHERE student_id = ?
        ))`
      : '';
    const params = roles.includes('student') ? [actorId] : [];
    return runDbQuery(
      ds,
      `SELECT e.id, e.title, e.subject_id as "subjectId", e.class_id as "classId",
              e.duration_minutes as "durationMinutes", e.status, e.created_by as "createdBy",
              e.created_at as "createdAt",
              (SELECT COUNT(*) FROM cbt_questions q WHERE q.exam_id = e.id) as "questionCount"
       FROM cbt_exams e ${studentScope} ORDER BY e.created_at DESC LIMIT 100`,
      params,
    );
  }

  async createExam(
    tenantId: string,
    userId: string,
    body: {
      title: string;
      subjectId?: string;
      classId?: string;
      durationMinutes?: number;
      questions?: { prompt: string; options: string[]; correctIndex: number; points?: number }[];
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.cbt');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    validateExamBody(body);
    const id = randomToken('cbt');
    await runDbQuery(
      ds,
      `INSERT INTO cbt_exams (id, title, subject_id, class_id, duration_minutes, status, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, 'draft', ?, NOW())`,
      [id, body.title.trim(), body.subjectId || null, body.classId || null, body.durationMinutes || 60, userId],
    );
    for (const q of body.questions || []) {
      await runDbQuery(
        ds,
        `INSERT INTO cbt_questions (id, exam_id, prompt, options, correct_index, points)
         VALUES (?, ?, ?, ?, ?, ?)`,
        [
          randomToken('cbq'),
          id,
          q.prompt,
          JSON.stringify(q.options || []),
          q.correctIndex ?? 0,
          q.points ?? 1,
        ],
      );
    }
    return { id, questionCount: (body.questions || []).length };
  }

  async publishExam(tenantId: string, actorId: string, examId: string) {
    await this.flags.assertEnabled(tenantId, 'academic.cbt');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, created_by as "createdBy", status FROM cbt_exams WHERE id = ? LIMIT 1`,
      [examId],
    );
    if (!rows.length) throw new NotFoundException('Exam not found');
    const roles = await this.getUserRoles(ds, actorId);
    if (rows[0].createdBy !== actorId && !roles.some((role) =>
      ['director', 'school_admin', 'principal', 'head_teacher'].includes(role),
    )) {
      throw new BadRequestException('Only the author or school leadership may publish this exam');
    }
    await runDbQuery(ds, `UPDATE cbt_exams SET status = 'published' WHERE id = ?`, [examId]);
    return { id: examId, status: 'published' };
  }

  async getExamQuestions(tenantId: string, examId: string, includeAnswers = false) {
    await this.flags.assertEnabled(tenantId, 'academic.cbt');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, prompt, options, points${includeAnswers ? ', correct_index as "correctIndex"' : ''}
       FROM cbt_questions WHERE exam_id = ? ORDER BY id`,
      [examId],
    );
    return (rows || []).map((r) => ({
      ...r,
      options: typeof r.options === 'string' ? JSON.parse(r.options) : r.options,
    }));
  }

  async startAttempt(tenantId: string, studentId: string, examId: string) {
    await this.flags.assertEnabled(tenantId, 'academic.cbt');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);

    const exams: any[] = await runDbQuery(
      ds,
      `SELECT id, title, class_id as "classId", duration_minutes as "durationMinutes", status
       FROM cbt_exams WHERE id = ? LIMIT 1`,
      [examId],
    );
    if (!exams.length) throw new NotFoundException('Exam not found');
    const roles = await this.getUserRoles(ds, studentId);
    if (!roles.includes('student')) {
      throw new BadRequestException('Only student accounts may start a CBT attempt');
    }
    if (exams[0].status !== 'published') {
      throw new BadRequestException('This exam is not published');
    }
    if (exams[0].classId) {
      const enrolled = await runDbQuery(
        ds,
        `SELECT 1 FROM student_class_enrollments WHERE student_id = ? AND class_id = ? LIMIT 1`,
        [studentId, exams[0].classId],
      );
      if (!enrolled.length) throw new BadRequestException('Student is not enrolled in this exam class');
    }

    const existing: any[] = await runDbQuery(
      ds,
      `SELECT id FROM cbt_attempts WHERE exam_id = ? AND student_id = ? AND status = 'in_progress' LIMIT 1`,
      [examId, studentId],
    );
    if (existing.length) {
      const questions = await this.getExamQuestions(tenantId, examId, false);
      return { attemptId: existing[0].id, exam: exams[0], questions };
    }

    const attemptId = randomToken('cba');
    await runDbQuery(
      ds,
      `INSERT INTO cbt_attempts (id, exam_id, student_id, status, answers, score, max_score, started_at)
       VALUES (?, ?, ?, 'in_progress', '{}', 0, 0, NOW())`,
      [attemptId, examId, studentId],
    );
    const questions = await this.getExamQuestions(tenantId, examId, false);
    return { attemptId, exam: exams[0], questions };
  }

  async submitAnswers(
    tenantId: string,
    studentId: string,
    attemptId: string,
    answers: Record<string, number>,
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.cbt');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);

    const attempts: any[] = await runDbQuery(
      ds,
      `SELECT id, exam_id as "examId", student_id as "studentId", status, started_at as "startedAt"
       FROM cbt_attempts WHERE id = ? LIMIT 1`,
      [attemptId],
    );
    if (!attempts.length) throw new NotFoundException('Attempt not found');
    const attempt = attempts[0];
    if (attempt.studentId !== studentId) {
      throw new BadRequestException('Attempt does not belong to this student');
    }
    if (attempt.status !== 'in_progress') {
      throw new BadRequestException('Attempt already submitted');
    }
    const examRows: any[] = await runDbQuery(
      ds,
      `SELECT duration_minutes as "durationMinutes" FROM cbt_exams WHERE id = ? LIMIT 1`,
      [attempt.examId],
    );
    const startedAt = new Date(attempt.startedAt).getTime();
    const duration = Number(examRows[0]?.durationMinutes || 60);
    if (Number.isFinite(startedAt) && Date.now() > startedAt + duration * 60_000) {
      throw new BadRequestException('Exam time has expired');
    }

    await runDbQuery(
      ds,
      `UPDATE cbt_attempts SET answers = ? WHERE id = ?`,
      [JSON.stringify(answers || {}), attemptId],
    );
    return this.scoreAttempt(tenantId, attemptId);
  }

  async scoreAttempt(tenantId: string, attemptId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);

    const attempts: any[] = await runDbQuery(
      ds,
      `SELECT id, exam_id as "examId", student_id as "studentId", answers, status
       FROM cbt_attempts WHERE id = ? LIMIT 1`,
      [attemptId],
    );
    if (!attempts.length) throw new NotFoundException('Attempt not found');
    const attempt = attempts[0];

    const questions = await this.getExamQuestions(tenantId, attempt.examId, true);
    let answers: Record<string, number> = {};
    try {
      answers = attempt.answers ? JSON.parse(attempt.answers) : {};
    } catch {
      answers = {};
    }

    let score = 0;
    let maxScore = 0;
    for (const q of questions) {
      maxScore += Number(q.points || 1);
      const chosen = answers[q.id];
      if (chosen !== undefined && chosen === q.correctIndex) {
        score += Number(q.points || 1);
      }
    }

    await runDbQuery(
      ds,
      `UPDATE cbt_attempts
       SET status = 'submitted', score = ?, max_score = ?, submitted_at = NOW()
       WHERE id = ?`,
      [score, maxScore, attemptId],
    );

    return {
      attemptId,
      examId: attempt.examId,
      studentId: attempt.studentId,
      score,
      maxScore,
      percent: maxScore > 0 ? Math.round((score / maxScore) * 100) : 0,
      status: 'submitted',
    };
  }

  private async getUserRoles(ds: any, userId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const row = rows[0];
    if (!row) return [];
    if (Array.isArray(row.roles)) return row.roles.map((role: string) => String(role).toLowerCase());
    if (typeof row.roles === 'string') {
      try {
        const parsed = JSON.parse(row.roles);
        if (Array.isArray(parsed)) return parsed.map((role: string) => String(role).toLowerCase());
      } catch {
        // Legacy role column fallback.
      }
    }
    return row.role ? [String(row.role).toLowerCase()] : [];
  }
}

export function validateExamBody(body: {
  title: string;
  durationMinutes?: number;
  questions?: { prompt: string; options: string[]; correctIndex: number; points?: number }[];
}) {
  if (!body.title?.trim()) throw new BadRequestException('Exam title is required');
  const duration = Number(body.durationMinutes ?? 60);
  if (!Number.isFinite(duration) || duration < 1 || duration > 480) {
    throw new BadRequestException('Exam duration must be between 1 and 480 minutes');
  }
  for (const question of body.questions || []) {
    if (!question.prompt?.trim() || !Array.isArray(question.options) || question.options.length < 2) {
      throw new BadRequestException('Every question needs a prompt and at least two options');
    }
    if (
      !Number.isInteger(question.correctIndex) ||
      question.correctIndex < 0 ||
      question.correctIndex >= question.options.length
    ) {
      throw new BadRequestException('Every question needs a valid correct answer');
    }
    if (question.points !== undefined && (!Number.isFinite(Number(question.points)) || Number(question.points) < 1)) {
      throw new BadRequestException('Question points must be positive');
    }
  }
}
