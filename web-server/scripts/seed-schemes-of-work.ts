import 'reflect-metadata';
import { createHash } from 'crypto';
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';
import { DataSource } from 'typeorm';
import { buildControlDataSourceOptions } from '../src/database/control-db.config';
import { buildTenantDataSourceOptions } from '../src/database/typeorm.config';
import { TenantConfigEntity } from '../src/control-plane/entities/tenant-config.entity';
import { CryptoService } from '../src/common/security/crypto.service';

type Topic = {
  title: string;
  objective: string;
  activities: string;
  resources: string;
  assessment: string;
};

type SubjectRow = {
  id: string;
  name: string;
  schoolLevel: string;
  classIds: string | null;
};

type ClassRow = { id: string; name: string; schoolLevel: string };
type TermRow = { id: string; name: string; sessionId: string; code: string };

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

const TOPICS: Record<string, Topic[]> = {
  'English Language': [
    [
      'Listening and speaking',
      'Develop attentive listening and clear oral expression.',
      'Guided conversations, role play, and pronunciation drills.',
      'Approved English textbook and audio passages.',
      'Oral questions and speaking observation.',
    ],
    [
      'Grammar in context',
      'Use parts of speech and sentence patterns accurately.',
      'Sentence sorting, correction exercises, and pair work.',
      'Grammar charts and learner workbooks.',
      'Short written exercise.',
    ],
    [
      'Reading comprehension',
      'Identify main ideas, details, sequence, and inference in a passage.',
      'Shared reading, vocabulary preview, and comprehension questions.',
      'Age-appropriate passages and dictionary.',
      'Comprehension worksheet.',
    ],
    [
      'Vocabulary development',
      'Build and apply subject-appropriate vocabulary.',
      'Word maps, context clues, and vocabulary games.',
      'Word cards and dictionary.',
      'Vocabulary quiz.',
    ],
    [
      'Writing skills',
      'Plan, draft, revise, and present coherent writing.',
      'Model writing, guided drafting, peer review, and editing.',
      'Writing notebook and sample texts.',
      'Marked writing task.',
    ],
    [
      'Literature and oral traditions',
      'Respond thoughtfully to stories, poems, or drama.',
      'Read-aloud, performance, discussion, and creative response.',
      'Storybooks, poems, and local texts.',
      'Literary response or performance.',
    ],
  ].map(([title, objective, activities, resources, assessment]) => ({
    title,
    objective,
    activities,
    resources,
    assessment,
  })),
  Mathematics: [
    [
      'Number sense and place value',
      'Read, represent, compare, and order numbers appropriate to the class.',
      'Counters, number lines, place-value activities, and drills.',
      'Mathematics textbook and place-value cards.',
      'Class exercise and oral questions.',
    ],
    [
      'Operations',
      'Apply the four operations to increasingly complex problems.',
      'Mental mathematics, worked examples, and collaborative practice.',
      'Counters, charts, and workbook.',
      'Written exercise.',
    ],
    [
      'Fractions and decimals',
      'Represent, compare, and calculate with fractions or decimals as appropriate.',
      'Paper folding, number lines, and practical problem solving.',
      'Fraction strips and decimal grids.',
      'Problem-solving task.',
    ],
    [
      'Measurement',
      'Measure and estimate using appropriate units.',
      'Hands-on measurement of classroom objects and recording results.',
      'Rulers, scales, clocks, and measuring containers.',
      'Practical measurement activity.',
    ],
    [
      'Geometry',
      'Identify, describe, and construct common shapes and angles.',
      'Shape sorting, drawing, folding, and angle investigation.',
      'Geometric instruments and shape cards.',
      'Diagram and identification exercise.',
    ],
    [
      'Data and problem solving',
      'Collect, represent, interpret data, and explain solution strategies.',
      'Class survey, tables, charts, and multi-step problems.',
      'Graph paper and problem cards.',
      'Data interpretation quiz.',
    ],
  ].map(([title, objective, activities, resources, assessment]) => ({
    title,
    objective,
    activities,
    resources,
    assessment,
  })),
  'Basic Science': [
    [
      'Living and non-living things',
      'Distinguish living things and describe their basic characteristics.',
      'Observation walk, sorting, and class discussion.',
      'Pictures, specimens, and chart paper.',
      'Classification activity.',
    ],
    [
      'Matter and materials',
      'Describe common materials and changes in their states or properties.',
      'Material testing, sorting, and simple demonstrations.',
      'Water, containers, solids, and classroom materials.',
      'Observation record.',
    ],
    [
      'Plants and animals',
      'Identify structures, needs, and adaptations of familiar organisms.',
      'Label specimens, compare habitats, and grow a seed.',
      'Plant samples, diagrams, and seed-growing materials.',
      'Labelled diagram.',
    ],
    [
      'Energy and motion',
      'Recognise common forms of energy and explain simple motion.',
      'Push-and-pull investigations and energy-source discussion.',
      'Toys, magnets, torches, and batteries.',
      'Investigation questions.',
    ],
    [
      'Earth and environment',
      'Explain basic weather, resources, and environmental care.',
      'Weather chart, waste audit, and conservation project.',
      'Weather chart and recycling materials.',
      'Short project report.',
    ],
    [
      'Health and safety',
      'Apply personal, community, and laboratory safety practices.',
      'Safety scenarios, demonstrations, and hygiene routine.',
      'Safety posters and first-aid images.',
      'Safety checklist.',
    ],
  ].map(([title, objective, activities, resources, assessment]) => ({
    title,
    objective,
    activities,
    resources,
    assessment,
  })),
};

const FALLBACK_TOPICS = [
  'Introduction and prior knowledge',
  'Key concepts and vocabulary',
  'Guided examples and practice',
  'Application to everyday life',
  'Practical or collaborative activity',
  'Review and assessment',
];

function topicsFor(subject: SubjectRow): Topic[] {
  const known = TOPICS[subject.name.trim()];
  if (known) return known;
  return FALLBACK_TOPICS.map((title, index) => ({
    title: `${subject.name}: ${title}`,
    objective: `Explain and apply the key ${subject.name.toLowerCase()} ideas covered in this unit.`,
    activities:
      index === 4
        ? 'Learner-centred practical, group, or project activity.'
        : 'Teacher explanation, guided discussion, examples, and learner practice.',
    resources: `${subject.name} textbook, board, learner notebooks, and relevant local materials.`,
    assessment:
      index === 5
        ? 'Revision exercise and end-of-unit assessment.'
        : 'Questions, observation, and class exercise.',
  }));
}

const DEFAULT_PASSWORD = 'Password@123';
const CONTROL_DB_URL = 'postgresql://neondb_owner:npg_4NI6KspBHRbv@ep-cold-math-amz06dq7-pooler.c-5.us-east-1.aws.neon.tech/tenant_control_db?sslmode=require'//process.env.CONTROL_DB_URL;
const SCHOOL_TENANT_ID = 'tnt_1786280134145_bjmq7w'//process.env.SCHOOL_TENANT_ID;
const SCHOOL_TENANT_SLUG = 'kristbethel-college'//process.env.SCHOOL_TENANT_SLUG;

async function main() {
  loadLocalEnv();
  const controlUrl = CONTROL_DB_URL;
  if (!controlUrl) throw new Error('CONTROL_DB_URL is required');

  const control = new DataSource(buildControlDataSourceOptions(controlUrl));
  await control.initialize();
  let db: DataSource | undefined;
  try {
    const tenantRepo = control.getRepository(TenantConfigEntity);
    const tenant = SCHOOL_TENANT_ID
      ? await tenantRepo.findOneBy({ id: SCHOOL_TENANT_ID })
      : SCHOOL_TENANT_SLUG
      ? await tenantRepo.findOneBy({ slug: SCHOOL_TENANT_SLUG })
      : null;
    if (!tenant)
      throw new Error(
        'Set SCHOOL_TENANT_ID or SCHOOL_TENANT_SLUG for a ready tenant',
      );
    if (tenant.provisioningStatus !== 'ready')
      throw new Error(`Tenant is not ready: ${tenant.provisioningStatus}`);

    const dbUrl =
      tenant.dbUri.startsWith('postgres://') ||
      tenant.dbUri.startsWith('postgresql://')
        ? tenant.dbUri
        : new CryptoService().decrypt(tenant.dbUri);
    db = new DataSource(buildTenantDataSourceOptions(dbUrl));
    await db.initialize();

    await db.query(`CREATE TABLE IF NOT EXISTS academic_schemes_of_work (
      id varchar(64) PRIMARY KEY, class_id varchar(64) NOT NULL, subject_id varchar(64) NOT NULL,
      session_id varchar(64) NOT NULL, term_id varchar(64) NOT NULL, title varchar(255) NULL,
      created_by varchar(64) NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE (class_id, subject_id, term_id)
    )`);
    await db.query(`CREATE TABLE IF NOT EXISTS academic_scheme_topics (
      id varchar(64) PRIMARY KEY, scheme_id varchar(64) NOT NULL, title varchar(255) NOT NULL,
      timeframe varchar(128) NULL, objective text NULL, activities text NULL, resources text NULL,
      assessment text NULL, order_index int NOT NULL DEFAULT 0, status varchar(32) NOT NULL DEFAULT 'planned',
      covered_before_assessment boolean NOT NULL DEFAULT false, covered_before_examination boolean NOT NULL DEFAULT false,
      carry_over_reason text NULL, is_next boolean NOT NULL DEFAULT false, updated_by varchar(64) NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);

    const sessionRows = await db.query(
      process.env.SEED_SESSION_ID
        ? `SELECT id, name FROM academic_sessions WHERE id = $1 LIMIT 1`
        : `SELECT id, name FROM academic_sessions WHERE is_current = true ORDER BY created_at DESC LIMIT 1`,
      process.env.SEED_SESSION_ID ? [process.env.SEED_SESSION_ID] : [],
    );
    if (!sessionRows[0])
      throw new Error(
        'No current academic session found; create one or set SEED_SESSION_ID',
      );
    const sessionId = sessionRows[0].id as string;
    const terms: TermRow[] = await db.query(
      `SELECT id, name, session_id as "sessionId", code FROM academic_terms WHERE session_id = $1 ORDER BY
       CASE code WHEN 'first' THEN 1 WHEN 'second' THEN 2 WHEN 'third' THEN 3 ELSE 4 END, created_at`,
      [sessionId],
    );
    const classes: ClassRow[] = await db.query(
      `SELECT id, name, school_level as "schoolLevel" FROM academic_classes WHERE is_active = true ORDER BY name`,
    );
    const subjects: SubjectRow[] = await db.query(
      `SELECT id, name, school_level as "schoolLevel", class_ids as "classIds"
       FROM academic_subjects WHERE is_active = true ORDER BY school_level, name`,
    );
    if (!terms.length || !classes.length || !subjects.length) {
      throw new Error(
        'Seed requires at least one term, active class, and active subject',
      );
    }

    let createdSchemes = 0;
    let createdTopics = 0;
    for (const subject of subjects) {
      const configuredClassIds = subject.classIds
        ? (JSON.parse(subject.classIds) as string[])
        : [];
      const eligibleClasses = classes.filter((item) => {
        const sameLevel =
          subject.schoolLevel === 'secondary'
            ? ['jss', 'sss'].includes(item.schoolLevel)
            : item.schoolLevel === subject.schoolLevel;
        return (
          sameLevel &&
          (!configuredClassIds.length || configuredClassIds.includes(item.id))
        );
      });
      for (const classRow of eligibleClasses) {
        for (const term of terms) {
          const existing = await db.query(
            `SELECT id FROM academic_schemes_of_work WHERE class_id = $1 AND subject_id = $2 AND term_id = $3`,
            [classRow.id, subject.id, term.id],
          );
          if (existing[0]) continue;
          const schemeId = seedId(
            'scheme',
            `${classRow.id}:${subject.id}:${term.id}`,
          );
          await db.query(
            `INSERT INTO academic_schemes_of_work
             (id, class_id, subject_id, session_id, term_id, title)
             VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (class_id, subject_id, term_id) DO NOTHING`,
            [
              schemeId,
              classRow.id,
              subject.id,
              sessionId,
              term.id,
              `${subject.name} Scheme of Work - ${term.name}`,
            ],
          );
          const topics = topicsFor(subject);
          for (const [index, topic] of topics.entries()) {
            await db.query(
              `INSERT INTO academic_scheme_topics
               (id, scheme_id, title, timeframe, objective, activities, resources, assessment, order_index)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) ON CONFLICT (id) DO NOTHING`,
              [
                seedId('topic', `${schemeId}:${index}`),
                schemeId,
                topic.title,
                `Weeks ${index * 2 + 1}-${index * 2 + 2}`,
                topic.objective,
                topic.activities,
                topic.resources,
                topic.assessment,
                index,
              ],
            );
            createdTopics++;
          }
          createdSchemes++;
        }
      }
    }
    console.log(
      JSON.stringify(
        {
          tenant: tenant.slug,
          session: sessionRows[0].name,
          terms: terms.length,
          activeSubjects: subjects.length,
          activeClasses: classes.length,
          createdSchemes,
          createdTopics,
        },
        null,
        2,
      ),
    );
  } finally {
    if (db?.isInitialized) await db.destroy();
    await control.destroy();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
