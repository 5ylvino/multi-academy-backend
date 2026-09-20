import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { loadUserRoles } from '../common/auth/teacher-scope.util';

@Injectable()
export class PerformanceService {
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

  async detect(tenantId: string, actorId: string, classId?: string) {
    await this.flags.assertEnabled(tenantId, 'ai.performance_detection');
    const ds = await this.getTenantDs(tenantId);
    const roles = await loadUserRoles(ds, actorId);
    const scope = roles.includes('parent')
      ? ` AND student_id IN (SELECT student_id FROM parent_student_links WHERE parent_id = ?)`
      : roles.includes('student')
        ? ` AND student_id = ?`
        : '';
    const scopeParams = scope ? [actorId] : [];
    const since = new Date(Date.now() - 90 * 86400000).toISOString();

    let resultsSql = `
      SELECT student_id as "studentId", AVG(total_score) as "avgScore", COUNT(*) as "resultCount"
      FROM academic_results
      WHERE created_at >= ? AND total_score IS NOT NULL`;
    const params: any[] = [since];
    if (classId) {
      resultsSql += ` AND class_id = ?`;
      params.push(classId);
    }
    params.push(...scopeParams);
    resultsSql += scope + ` GROUP BY student_id HAVING COUNT(*) >= 2`;

    const results: any[] = await runDbQuery(ds, resultsSql, params);

    const attendance: any[] = await runDbQuery(
      ds,
      `SELECT student_id as "studentId",
              SUM(CASE WHEN LOWER(status) = 'present' THEN 1 ELSE 0 END) as "present",
              COUNT(*) as "total"
       FROM attendance_students
       WHERE created_at >= ?
       GROUP BY student_id`,
      [since],
    );
    const attMap = new Map<string, number>();
    for (const row of attendance) {
      const rate = row.total > 0 ? Number(row.present) / Number(row.total) : 0;
      attMap.set(row.studentId, rate);
    }

    const flagged = [];
    for (const r of results) {
      const avg = Number(r.avgScore);
      const attRate = attMap.get(r.studentId) ?? 1;
      const issues: string[] = [];
      if (avg < 50) issues.push('low_average');
      if (attRate < 0.75) issues.push('low_attendance');
      if (issues.length) {
        flagged.push({
          studentId: r.studentId,
          avgScore: Math.round(avg * 10) / 10,
          attendanceRate: Math.round(attRate * 100),
          issues,
        });
      }
    }
    return { scanned: results.length, flagged };
  }

  async recommend(tenantId: string, actorId: string, studentId: string) {
    await this.flags.assertEnabled(tenantId, 'ai.performance_recommendations');
    let flagged: Array<{ studentId: string; issues: string[] }> = [];
    try {
      const detection = await this.detect(tenantId, actorId);
      flagged = detection.flagged;
    } catch {
      /* recommendations still work when detection is off or tables are empty */
    }
    const roles = await loadUserRoles(await this.getTenantDs(tenantId), actorId);
    if (roles.includes('student') && actorId !== studentId) {
      throw new NotFoundException('Student recommendations are restricted');
    }
    if (roles.includes('parent')) {
      const linked: any[] = await runDbQuery(
        await this.getTenantDs(tenantId),
        `SELECT 1 FROM parent_student_links WHERE parent_id = ? AND student_id = ? LIMIT 1`,
        [actorId, studentId],
      );
      if (!linked.length) throw new NotFoundException('Parent is not linked to this student');
    }
    const student = flagged.find((f) => f.studentId === studentId);
    const tips: string[] = [];
    if (!student) {
      tips.push('Keep up consistent study habits and attendance.');
    } else {
      if (student.issues.includes('low_average')) {
        tips.push('Schedule remedial sessions for weak subjects.');
        tips.push('Review CA vs exam breakdown with class teacher.');
      }
      if (student.issues.includes('low_attendance')) {
        tips.push('Contact parent/guardian about attendance pattern.');
        tips.push('Consider morning check-in or mentor assignment.');
      }
    }
    return { studentId, recommendations: tips };
  }

  async homeSupport(tenantId: string, actorId: string, studentId: string) {
    try {
      await this.flags.assertEnabled(tenantId, 'ai.performance_recommendations');
    } catch {
      return {
        studentId,
        audience: 'parent',
        atHome: ['Ask your child what they learned today and review one homework item together.'],
        sources: [],
        disclaimer: 'AI guidance is off for this school. This is a generic study prompt.',
      };
    }
    try {
      const rec = await this.recommend(tenantId, actorId, studentId);
      const home = rec.recommendations.map((tip) => {
        if (tip.includes('Contact parent')) {
          return 'Agree a morning routine so your child arrives on time.';
        }
        if (tip.includes('remedial')) {
          return 'Spend 20 minutes reviewing this week’s homework together. Ask them to explain one topic out loud.';
        }
        if (tip.includes('CA vs exam')) {
          return 'Ask which classwork felt hardest this week, then practise that topic with a short quiz at home.';
        }
        return tip;
      });
      return {
        studentId,
        audience: 'parent',
        atHome: home,
        sources: ['academic_results', 'attendance_students'],
        disclaimer:
          'This is study support, not a diagnosis. It does not compare your child with classmates.',
      };
    } catch {
      return {
        studentId,
        audience: 'parent',
        atHome: ['Review tonight’s homework together and confirm tomorrow’s timetable.'],
        sources: [],
        disclaimer: 'Detailed AI guidance is unavailable right now.',
      };
    }
  }

  async studyPlan(tenantId: string, actorId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureStudyPlanTable(ds);
    const classId = await this.getStudentClassId(ds, actorId);
    const planKey = `${actorId}:${classId || 'unassigned'}`;
    let aiEnabled = true;
    try {
      await this.flags.assertEnabled(tenantId, 'ai.performance_recommendations');
    } catch {
      aiEnabled = false;
    }

    const cached: any[] = await runDbQuery(
      ds,
      `SELECT actions, sources, source_mode as "sourceMode", disclaimer, plan_version as "planVersion"
       FROM academic_study_plans WHERE plan_key = ? LIMIT 1`,
      [planKey],
    );
    const saved = cached[0];
    if (
      saved &&
      Number(saved.planVersion) === 2 &&
      ((aiEnabled && saved.sourceMode === 'ai') || (!aiEnabled && saved.sourceMode === 'scheme'))
    ) {
      return {
        studentId: actorId,
        audience: 'student',
        actions: this.parseJsonArray(saved.actions),
        sources: this.parseJsonArray(saved.sources),
        sourceMode: saved.sourceMode,
        disclaimer: saved.disclaimer,
        persisted: true,
      };
    }

    let plan: { actions: string[]; sources: string[]; sourceMode: 'ai' | 'scheme'; disclaimer: string };
    if (!aiEnabled) {
      const schemeActions = await this.schemeStudyActions(tenantId, actorId);
      plan = {
        actions: schemeActions.length ? schemeActions : ['Review tonight’s homework and pack your bag before bed.'],
        sources: schemeActions.length ? ['academic_scheme_topics'] : [],
        sourceMode: 'scheme',
        disclaimer: schemeActions.length
          ? 'This study plan is based on your class scheme of work. AI guidance is off for this school.'
          : 'AI guidance is off for this school. This is a generic study prompt.',
      };
    } else {
      try {
        const rec = await this.recommend(tenantId, actorId, actorId);
        const schemeActions = await this.schemeStudyActions(tenantId, actorId);
        const performanceActions = rec.recommendations.map((tip) =>
          tip
            .replace('Contact parent/guardian about attendance pattern.', 'Aim to be present every school day this week.')
            .replace('Schedule remedial sessions for weak subjects.', 'Practise the topic you scored lowest on for 15 minutes today.')
            .replace('Review CA vs exam breakdown with class teacher.', 'Ask your teacher which CA items to revise first.')
            .replace('Consider morning check-in or mentor assignment.', 'Pack your bag the night before and leave 10 minutes earlier.'),
        );
        plan = {
          actions: [...performanceActions, ...schemeActions].slice(0, 8),
          sources: ['academic_results', 'attendance_students', ...(schemeActions.length ? ['academic_scheme_topics'] : [])],
          sourceMode: 'ai',
          disclaimer: 'This study plan is generated by AI using your academic performance, attendance, and class Scheme of Work.',
        };
      } catch {
        const schemeActions = await this.schemeStudyActions(tenantId, actorId);
        plan = {
          actions: schemeActions.length
            ? schemeActions
            : ['Revise one subject for 15 minutes and confirm tomorrow’s timetable.'],
          sources: schemeActions.length ? ['academic_scheme_topics'] : [],
          sourceMode: 'scheme',
          disclaimer: schemeActions.length
          ? 'AI guidance is unavailable. This study plan is based on your class scheme of work.'
          : 'Detailed AI guidance is unavailable right now.',
        };
      }
    }
    await runDbQuery(
      ds,
      `INSERT INTO academic_study_plans
         (plan_key, student_id, class_id, actions, sources, source_mode, disclaimer, plan_version)
       VALUES (?, ?, ?, ?, ?, ?, ?, 2)
       ON CONFLICT (plan_key) DO UPDATE SET
         actions = EXCLUDED.actions, sources = EXCLUDED.sources,
         source_mode = EXCLUDED.source_mode, disclaimer = EXCLUDED.disclaimer,
         plan_version = EXCLUDED.plan_version,
         updated_at = NOW()`,
      [planKey, actorId, classId, JSON.stringify(plan.actions), JSON.stringify(plan.sources), plan.sourceMode, plan.disclaimer],
    );
    return { studentId: actorId, audience: 'student', ...plan, persisted: true };
  }

  async saveStudyPlan(
    tenantId: string,
    actorId: string,
    plan: {
      actions: string[];
      sources: string[];
      sourceMode: 'ai' | 'scheme';
      disclaimer: string;
      focusTopics?: string[];
      masterySummary?: unknown[];
    },
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureStudyPlanTable(ds);
    const classId = await this.getStudentClassId(ds, actorId);
    const planKey = `${actorId}:${classId || 'unassigned'}`;
    await runDbQuery(
      ds,
      `INSERT INTO academic_study_plans
         (plan_key, student_id, class_id, actions, sources, source_mode, disclaimer, plan_version)
       VALUES (?, ?, ?, ?, ?, ?, ?, 2)
       ON CONFLICT (plan_key) DO UPDATE SET
         actions = EXCLUDED.actions, sources = EXCLUDED.sources,
         source_mode = EXCLUDED.source_mode, disclaimer = EXCLUDED.disclaimer,
         plan_version = EXCLUDED.plan_version,
         updated_at = NOW()`,
      [
        planKey,
        actorId,
        classId,
        JSON.stringify(plan.actions),
        JSON.stringify([
          ...plan.sources,
          ...(plan.focusTopics?.length ? ['focus_topics'] : []),
          ...(plan.masterySummary?.length ? ['topic_mastery'] : []),
        ]),
        plan.sourceMode,
        plan.disclaimer,
      ],
    );
  }

  private async schemeStudyActions(tenantId: string, studentId: string): Promise<string[]> {
    try {
      const ds = await this.getTenantDs(tenantId);
      const rows: any[] = await runDbQuery(
        ds,
        `SELECT sub.name as "subjectName", st.title, st.timeframe, st.objective,
                st.status, st.is_next as "isNext"
         FROM student_class_enrollments e
         JOIN academic_schemes_of_work s ON s.class_id = e.class_id
         JOIN academic_scheme_topics st ON st.scheme_id = s.id
         JOIN academic_subjects sub ON sub.id = s.subject_id
         WHERE e.student_id = ?
         ORDER BY st.is_next DESC, st.order_index ASC
         LIMIT 8`,
        [studentId],
      );
      return rows
        .filter((row) => row.status !== 'covered')
        .slice(0, 5)
        .map((row) => {
          const prefix = row.isNext ? 'Next: ' : 'Revise: ';
          const objective = row.objective ? ` — ${row.objective}` : '';
          return `${prefix}${row.subjectName || 'Subject'}: ${row.title}${row.timeframe ? ` (${row.timeframe})` : ''}${objective}`;
        });
    } catch {
      return [];
    }
  }

  private async ensureStudyPlanTable(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_study_plans (
        plan_key varchar(160) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        class_id varchar(64),
        actions TEXT NOT NULL,
        sources TEXT NOT NULL,
        source_mode varchar(16) NOT NULL,
        disclaimer TEXT,
        plan_version integer NOT NULL DEFAULT 2,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
      )
    `);
    await ds.query(`ALTER TABLE academic_study_plans ADD COLUMN IF NOT EXISTS plan_version integer NOT NULL DEFAULT 2`);
  }

  private async getStudentClassId(ds: any, studentId: string): Promise<string | null> {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT class_id FROM student_class_enrollments WHERE student_id = ? LIMIT 1`,
      [studentId],
    );
    return rows[0]?.class_id || null;
  }

  private parseJsonArray(value: unknown): string[] {
    if (Array.isArray(value)) return value.map(String);
    try {
      const parsed = typeof value === 'string' ? JSON.parse(value) : [];
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  }

}
