import 'reflect-metadata';
import { createHash } from 'crypto';
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';
import { DataSource } from 'typeorm';
import { buildControlDataSourceOptions } from '../src/database/control-db.config';
import { buildTenantDataSourceOptions } from '../src/database/typeorm.config';
import { TenantConfigEntity } from '../src/control-plane/entities/tenant-config.entity';
import { CryptoService } from '../src/common/security/crypto.service';

const SESSION_NAME = '2026/2027';
const TERM_CODE = 'first';
const CLASS_CODE = 'SSS3';

const ASSESSMENTS = [
  {
    categoryName: 'Continuous Assessment',
    categorySlug: 'continuous-assessment',
    name: 'Note',
    slug: 'note',
    obtainable: 10,
  },
  {
    categoryName: 'Continuous Assessment',
    categorySlug: 'continuous-assessment',
    name: 'Internal Assessment',
    slug: 'internal-assessment',
    obtainable: 15,
  },
  {
    categoryName: 'Continuous Assessment',
    categorySlug: 'continuous-assessment',
    name: 'School Assessment',
    slug: 'school-assessment',
    obtainable: 15,
  },
  {
    categoryName: 'Examination',
    categorySlug: 'examination',
    name: null,
    slug: null,
    obtainable: 60,
  },
] as const;

type SubjectRow = {
  id: string;
  name: string;
  schoolLevel: string;
  classIds: string | null;
};

function loadLocalEnv() {
  const envPath = resolve(__dirname, '../.env');
  if (!existsSync(envPath)) return;
  for (const line of readFileSync(envPath, 'utf8').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const separator = trimmed.indexOf('=');
    if (separator < 1) continue;
    const key = trimmed.slice(0, separator).trim();
    if (process.env[key] !== undefined) continue;
    process.env[key] = trimmed
      .slice(separator + 1)
      .trim()
      .replace(/^(['"])(.*)\1$/, '$2');
  }
}

function seedId(prefix: string, key: string) {
  const digest = createHash('sha256').update(key).digest('hex').slice(0, 32);
  return `seed_${prefix}_${digest}`.slice(0, 64);
}

function randomScore(key: string, obtainable: number) {
  const digest = createHash('sha256').update(key).digest();
  return digest.readUInt32BE(0) % (obtainable + 1);
}

function grade(score: number, obtainable: number) {
  return Math.round((score / obtainable) * 100);
}

function parseClassIds(value: string | null): string[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

const DEFAULT_PASSWORD = 'Password@123';
const CONTROL_DB_URL = 'postgresql://neondb_owner:npg_4NI6KspBHRbv@ep-cold-math-amz06dq7-pooler.c-5.us-east-1.aws.neon.tech/tenant_control_db?sslmode=require'//process.env.CONTROL_DB_URL;
const SCHOOL_TENANT_ID = 'tnt_1786280134145_bjmq7w'//process.env.SCHOOL_TENANT_ID;
const SCHOOL_TENANT_SLUG = 'kristbethel-college'//process.env.SCHOOL_TENANT_SLUG;

async function main() {
  loadLocalEnv();
  const controlUrl = CONTROL_DB_URL;
  const tenantId = SCHOOL_TENANT_ID;
  const tenantSlug = SCHOOL_TENANT_SLUG;
  if (!controlUrl) throw new Error('CONTROL_DB_URL is required');
  if (!tenantId && !tenantSlug)
    throw new Error('SCHOOL_TENANT_ID or SCHOOL_TENANT_SLUG is required');

  const control = new DataSource(buildControlDataSourceOptions(controlUrl));
  await control.initialize();
  let db: DataSource | undefined;

  try {
    const tenantRepo = control.getRepository(TenantConfigEntity);
    const tenant = tenantId
      ? await tenantRepo.findOneBy({ id: tenantId })
      : await tenantRepo.findOneBy({ slug: tenantSlug });
    if (!tenant) throw new Error('Tenant was not found');
    if (tenant.provisioningStatus !== 'ready') {
      throw new Error(`Tenant is not ready: ${tenant.provisioningStatus}`);
    }

    const tenantUrl =
      tenant.dbUri.startsWith('postgres://') ||
      tenant.dbUri.startsWith('postgresql://')
        ? tenant.dbUri
        : new CryptoService().decrypt(tenant.dbUri);
    db = new DataSource(buildTenantDataSourceOptions(tenantUrl));
    await db.initialize();

    const result = await db.transaction(async (manager) => {
      const sessionRows = await manager.query(
        `SELECT id, name FROM academic_sessions WHERE name = $1 LIMIT 1`,
        [SESSION_NAME],
      );
      if (!sessionRows[0])
        throw new Error(`Academic session "${SESSION_NAME}" was not found`);

      const termRows = await manager.query(
        `SELECT id, name FROM academic_terms WHERE session_id = $1 AND code = $2 LIMIT 1`,
        [sessionRows[0].id, TERM_CODE],
      );
      if (!termRows[0])
        throw new Error(
          `First Term for session "${SESSION_NAME}" was not found`,
        );

      const classRows = await manager.query(
        `SELECT id, name FROM academic_classes
         WHERE code = $1 AND school_level = 'sss' AND is_active = true LIMIT 1`,
        [CLASS_CODE],
      );
      if (!classRows[0]) throw new Error('Active SSS 3 class was not found');

      const classId = classRows[0].id as string;
      const sessionId = sessionRows[0].id as string;
      const termId = termRows[0].id as string;

      const students = await manager.query(
        `SELECT DISTINCT u.id, u.name
         FROM student_class_enrollments enrollment
         JOIN users u ON u.id = enrollment.student_id
         WHERE enrollment.class_id = $1 AND u.is_active = true
         ORDER BY u.name`,
        [classId],
      );
      if (!students.length)
        throw new Error('No active students are enrolled in SSS 3');

      const allSubjects: SubjectRow[] = await manager.query(
        `SELECT id, name, school_level as "schoolLevel", class_ids as "classIds"
         FROM academic_subjects
         WHERE is_active = true AND school_level IN ('sss', 'secondary')
         ORDER BY name`,
      );
      const subjects = allSubjects.filter((subject) =>
        parseClassIds(subject.classIds).includes(classId),
      );
      if (!subjects.length)
        throw new Error('No active subjects are assigned to SSS 3');

      const recorderRows = await manager.query(
        `SELECT id, name FROM users
         WHERE is_active = true
           AND (roles::text ILIKE '%subject_teacher%'
             OR roles::text ILIKE '%principal%'
             OR roles::text ILIKE '%school_admin%')
         ORDER BY id LIMIT 1`,
      );
      const recorder = recorderRows[0];
      if (!recorder)
        throw new Error('No active user is available to record score sheets');

      const categoryIds = new Map<string, string>();
      for (const assessment of ASSESSMENTS) {
        if (categoryIds.has(assessment.categorySlug)) continue;
        const categoryId = seedId('scorecat', assessment.categorySlug);
        await manager.query(
          `INSERT INTO academic_score_categories (id, name, slug, is_active)
           VALUES ($1, $2, $3, true)
           ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, is_active = true`,
          [categoryId, assessment.categoryName, assessment.categorySlug],
        );
        const rows = await manager.query(
          `SELECT id FROM academic_score_categories WHERE slug = $1 LIMIT 1`,
          [assessment.categorySlug],
        );
        categoryIds.set(assessment.categorySlug, rows[0].id);
      }

      const subcategoryIds = new Map<string, string>();
      for (const assessment of ASSESSMENTS) {
        if (!assessment.slug) continue;
        const categoryId = categoryIds.get(assessment.categorySlug)!;
        const subcategoryId = seedId(
          'scoresub',
          `${categoryId}:${assessment.slug}`,
        );
        await manager.query(
          `INSERT INTO academic_score_subcategories
             (id, category_id, name, slug, is_active)
           VALUES ($1, $2, $3, $4, true)
           ON CONFLICT (category_id, slug)
           DO UPDATE SET name = EXCLUDED.name, is_active = true`,
          [subcategoryId, categoryId, assessment.name, assessment.slug],
        );
        const rows = await manager.query(
          `SELECT id FROM academic_score_subcategories
           WHERE category_id = $1 AND slug = $2 LIMIT 1`,
          [categoryId, assessment.slug],
        );
        subcategoryIds.set(assessment.slug, rows[0].id);
      }

      let seeded = 0;
      for (const student of students) {
        for (const subject of subjects) {
          for (const assessment of ASSESSMENTS) {
            const categoryId = categoryIds.get(assessment.categorySlug)!;
            const subcategoryId = assessment.slug
              ? subcategoryIds.get(assessment.slug)!
              : null;
            const score = randomScore(
              `${sessionId}:${termId}:${classId}:${student.id}:${subject.id}:${
                assessment.slug || assessment.categorySlug
              }`,
              assessment.obtainable,
            );
            const sheetId = seedId(
              'scoresheet',
              `${sessionId}:${termId}:${classId}:${student.id}:${
                subject.id
              }:${categoryId}:${subcategoryId || ''}`,
            );
            const existingRows = await manager.query(
              `SELECT id FROM academic_score_sheets
               WHERE session_id = $1 AND term_id = $2 AND class_id = $3
                 AND student_id = $4 AND subject_id = $5 AND category_id = $6
                 AND subcategory_id IS NOT DISTINCT FROM $7
               LIMIT 1`,
              [
                sessionId,
                termId,
                classId,
                student.id,
                subject.id,
                categoryId,
                subcategoryId,
              ],
            );
            if (existingRows[0]) {
              await manager.query(
                `UPDATE academic_score_sheets
                 SET student_score = $1, obtainable_score = $2, grade = $3,
                     recorder_user_id = $4, recorded_by = $5, updated_at = NOW()
                 WHERE id = $6`,
                [
                  score,
                  assessment.obtainable,
                  grade(score, assessment.obtainable),
                  recorder.id,
                  recorder.name || recorder.id,
                  existingRows[0].id,
                ],
              );
            } else {
              await manager.query(
                `INSERT INTO academic_score_sheets
                   (id, session_id, term_id, class_id, student_id, subject_id, category_id,
                    subcategory_id, student_score, obtainable_score, grade, status,
                    recorder_user_id, recorded_by)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12, $13)`,
                [
                  sheetId,
                  sessionId,
                  termId,
                  classId,
                  student.id,
                  subject.id,
                  categoryId,
                  subcategoryId,
                  score,
                  assessment.obtainable,
                  grade(score, assessment.obtainable),
                  recorder.id,
                  recorder.name || recorder.id,
                ],
              );
            }
            seeded++;
          }
        }
      }
      return {
        students: students.length,
        subjects: subjects.length,
        rows: seeded,
      };
    });

    console.log(
      JSON.stringify(
        {
          tenant: tenant.slug,
          session: SESSION_NAME,
          term: 'First Term',
          class: classRowsLabel(),
          ...result,
        },
        null,
        2,
      ),
    );
  } finally {
    if (db?.isInitialized) await db.destroy();
    if (control.isInitialized) await control.destroy();
  }
}

function classRowsLabel() {
  return 'SSS 3';
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
