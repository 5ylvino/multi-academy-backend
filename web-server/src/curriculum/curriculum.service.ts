import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

const NERDC_PACKS: Record<string, object> = {
  nerdc_basic_jss: {
    id: 'nerdc_basic_jss',
    name: 'NERDC Basic Education (JSS)',
    level: 'JSS',
    subjects: [
      { code: 'ENG', name: 'English Language', topics: ['Composition', 'Comprehension', 'Grammar'] },
      { code: 'MTH', name: 'Mathematics', topics: ['Number & Numeration', 'Algebra', 'Geometry'] },
      { code: 'BSC', name: 'Basic Science', topics: ['Living Things', 'Energy', 'Ecology'] },
      { code: 'CIV', name: 'Civic Education', topics: ['Citizenship', 'Human Rights', 'Governance'] },
    ],
  },
  nerdc_sss_science: {
    id: 'nerdc_sss_science',
    name: 'NERDC SSS Science',
    level: 'SSS',
    subjects: [
      { code: 'PHY', name: 'Physics', topics: ['Mechanics', 'Waves', 'Electricity'] },
      { code: 'CHM', name: 'Chemistry', topics: ['Stoichiometry', 'Organic Chemistry', 'Equilibrium'] },
      { code: 'BIO', name: 'Biology', topics: ['Ecology', 'Genetics', 'Physiology'] },
      { code: 'FMT', name: 'Further Mathematics', topics: ['Calculus', 'Matrices', 'Statistics'] },
    ],
  },
};

@Injectable()
export class CurriculumService {
  constructor(
    private readonly flags: FeatureFlagService,
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureWaec(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS waec_neco_results (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        exam_type varchar(16) NOT NULL,
        subject varchar(128) NOT NULL,
        score decimal(7,2) NOT NULL DEFAULT 0,
        session_label varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  private async ensureJamb(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS jamb_cbt_questions (
        id varchar(64) PRIMARY KEY,
        subject varchar(128) NOT NULL,
        prompt text NOT NULL,
        options text NOT NULL DEFAULT '[]',
        correct_index int NOT NULL DEFAULT 0,
        year int NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async listNerdcPacks(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'academic.nerdc_curriculum');
    return {
      packs: Object.values(NERDC_PACKS).map((p: any) => ({
        id: p.id,
        name: p.name,
        subjectCount: p.subjects?.length ?? 0,
        status: 'loadable',
      })),
    };
  }

  async getNerdcPack(tenantId: string, packId: string) {
    await this.flags.assertEnabled(tenantId, 'academic.nerdc_curriculum');
    const pack = NERDC_PACKS[packId];
    if (!pack) throw new NotFoundException('Pack not found');
    return pack;
  }

  async waecNecoAnalytics(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'academic.waec_neco_analytics');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureWaec(ds);
    const bySubject: any[] = await runDbQuery(
      ds,
      `SELECT exam_type as "examType", subject,
              AVG(score) as "avgScore", COUNT(*) as "count"
       FROM waec_neco_results GROUP BY exam_type, subject ORDER BY subject`,
      [],
    );
    return { averages: bySubject, note: 'Stub analytics from stored WAEC/NECO result rows' };
  }

  async seedWaecResult(
    tenantId: string,
    body: { studentId: string; examType: 'WAEC' | 'NECO'; subject: string; score: number; sessionLabel?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.waec_neco_analytics');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureWaec(ds);
    const id = randomToken('wnr');
    await runDbQuery(
      ds,
      `INSERT INTO waec_neco_results (id, student_id, exam_type, subject, score, session_label, created_at)
       VALUES (?, ?, ?, ?, ?, ?, NOW())`,
      [id, body.studentId, body.examType, body.subject, body.score, body.sessionLabel || null],
    );
    return { id };
  }

  async listJambQuestions(tenantId: string, subject?: string) {
    await this.flags.assertEnabled(tenantId, 'academic.jamb_cbt');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureJamb(ds);
    const rows: any[] = subject
      ? await runDbQuery(
          ds,
          `SELECT id, subject, prompt, options, correct_index as "correctIndex", year
           FROM jamb_cbt_questions WHERE subject = ? ORDER BY created_at DESC LIMIT 100`,
          [subject],
        )
      : await runDbQuery(
          ds,
          `SELECT id, subject, prompt, options, correct_index as "correctIndex", year
           FROM jamb_cbt_questions ORDER BY created_at DESC LIMIT 100`,
          [],
        );
    if (!rows.length) {
      return {
        questions: [
          {
            id: 'stub_jamb_1',
            subject: 'Use of English',
            prompt: 'Choose the word nearest in meaning to BENEVOLENT',
            options: ['Kind', 'Cruel', 'Lazy', 'Proud'],
            correctIndex: 0,
            year: 2024,
          },
        ],
        note: 'Seed questions via POST /curriculum/jamb/questions',
      };
    }
    return {
      questions: rows.map((r) => ({
        ...r,
        options: JSON.parse(r.options || '[]'),
      })),
    };
  }

  async addJambQuestion(
    tenantId: string,
    body: { subject: string; prompt: string; options: string[]; correctIndex: number; year?: number },
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.jamb_cbt');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureJamb(ds);
    const id = randomToken('jamb');
    await runDbQuery(
      ds,
      `INSERT INTO jamb_cbt_questions (id, subject, prompt, options, correct_index, year, created_at)
       VALUES (?, ?, ?, ?, ?, ?, NOW())`,
      [id, body.subject, body.prompt, JSON.stringify(body.options || []), body.correctIndex ?? 0, body.year ?? null],
    );
    return { id };
  }
}
