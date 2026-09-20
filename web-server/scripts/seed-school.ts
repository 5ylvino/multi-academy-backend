import 'reflect-metadata';
import { createHash, randomBytes, scryptSync } from 'crypto';
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';
import { DataSource } from 'typeorm';
import { buildControlDataSourceOptions } from '../src/database/control-db.config';
import { buildTenantDataSourceOptions } from '../src/database/typeorm.config';
import { TenantConfigEntity } from '../src/control-plane/entities/tenant-config.entity';
import { derivePermissionsForRoles } from '../src/common/auth/role-permissions';
import { CryptoService } from '../src/common/security/crypto.service';

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
    const value = trimmed
      .slice(separator + 1)
      .trim()
      .replace(/^(['"])(.*)\1$/, '$2');
    process.env[key] = value;
  }
}

loadLocalEnv();

const DEFAULT_PASSWORD = 'Password@123';
const CONTROL_DB_URL = 'postgresql://neondb_owner:npg_4NI6KspBHRbv@ep-cold-math-amz06dq7-pooler.c-5.us-east-1.aws.neon.tech/tenant_control_db?sslmode=require'//process.env.CONTROL_DB_URL;
const TENANT_ID = 'tnt_1785597295361_saa3ln'//process.env.SCHOOL_TENANT_ID;
const TENANT_SLUG = 'kristbethel-college'//process.env.SCHOOL_TENANT_SLUG;
const studentFirstNames = [
  'Adebayo', 'Adaeze', 'Aisha', 'Amaka', 'Ayomide', 'Chiamaka', 'Chinedu', 'David',
  'Efe', 'Emeka', 'Fatima', 'Favour', 'Ifeanyi', 'Jide', 'Kelechi', 'Mariam',
  'Michael', 'Nneka', 'Samuel', 'Zainab',
];
const studentLastNames = [
  'Adewale', 'Afolabi', 'Ajayi', 'Anyanwu', 'Balogun', 'Bello', 'Chukwu', 'Eze',
  'Ibrahim', 'Ibe', 'Johnson', 'Lawal', 'Mohammed', 'Nwosu', 'Okafor', 'Okonkwo',
  'Olawale', 'Osei', 'Salami', 'Yusuf',
];
const parentFirstNames = [
  'Abdul', 'Ada', 'Ademola', 'Amina', 'Blessing', 'Chukwudi', 'Damilola', 'Esther',
  'Femi', 'Grace', 'Hauwa', 'Ibrahim', 'Janet', 'Kabiru', 'Ngozi', 'Oluwaseun',
  'Patience', 'Peter', 'Rasheed', 'Rose', 'Sani', 'Taiwo', 'Uche', 'Yemi',
];
const parentLastNames = [
  'Abubakar', 'Adeyemi', 'Akande', 'Aliyu', 'Eze', 'Ibrahim', 'Kalu', 'Lawal',
  'Musa', 'Nwachukwu', 'Ojo', 'Okeke', 'Oladipo', 'Onyeka', 'Suleiman', 'Usman',
  'Williams', 'Yakubu', 'Yusuf', 'Zubairu',
];
const classTeacherNames = [
  'Bisi Adeyemi', 'Chinonso Okafor', 'Grace Eze', 'Halima Bello', 'Ifeoma Nwosu',
  'John Afolabi', 'Kemi Balogun', 'Lilian Chukwu', 'Musa Ibrahim', 'Ngozi Okeke',
  'Obinna Eze', 'Ruth Adewale', 'Sani Yusuf', 'Toluwa Ajayi', 'Yvonne Nwachukwu',
];
const subjectTeacherNames = [
  'Abdul Malik', 'Adaobi Nnamani', 'Aisha Suleiman', 'Chinedu Obi', 'Daniel Ojo',
  'Esther Ibe', 'Femi Lawal', 'Hauwa Mohammed', 'Ikenna Kalu', 'Janet Williams',
  'Morenike Adekunle', 'Peter Nwankwo',
];

const nurseryAndPrimaryClasses = [
  ...Array.from({ length: 3 }, (_, i) => ({ name: `Nursery ${i + 1}`, code: `NUR${i + 1}`, level: 'nursery' })),
  ...Array.from({ length: 6 }, (_, i) => ({ name: `Primary ${i + 1}`, code: `PRI${i + 1}`, level: 'primary' })),
];
const secondaryClasses = [
  ...Array.from({ length: 3 }, (_, i) => ({ name: `JSS ${i + 1}`, code: `JSS${i + 1}`, level: 'jss' })),
  ...Array.from({ length: 3 }, (_, i) => ({ name: `SSS ${i + 1}`, code: `SSS${i + 1}`, level: 'sss' })),
];
const classes = [...nurseryAndPrimaryClasses, ...secondaryClasses];

const jssSubjects = [
  'English Language',
  'Mathematics',
  'Basic Science',
  'Basic Technology',
  'Social Studies',
  'Civic Education',
  'Computer Studies',
  'Business Studies',
  'Agricultural Science',
  'Christian Religious Studies',
  'Islamic Religious Studies',
  'French',
  'Physical & Health Education',
  'Cultural & Creative Arts',
  'Home Economics',
];
const nurserySubjects = [
  'Literacy',
  'Numeracy',
  'Rhymes',
  'Basic Science',
  'Social Habits',
  'Health Habits',
  'Creative Arts',
  'Physical Education',
  'Religious Studies',
  'Moral Instruction',
];
const primarySubjects = [
  'English Language',
  'Mathematics',
  'Basic Science',
  'Basic Technology',
  'Social Studies',
  'Civic Education',
  'Computer Studies',
  'Agricultural Science',
  'Christian Religious Studies',
  'Islamic Religious Studies',
  'Physical & Health Education',
  'Cultural & Creative Arts',
  'Home Economics',
];
const sssSubjects = [
  'English Language',
  'Mathematics',
  'Biology',
  'Chemistry',
  'Physics',
  'Economics',
  'Government',
  'Literature-in-English',
  'Civic Education',
];
const subjectCategories = [
  { name: 'Science', slug: 'science' },
  { name: 'Art', slug: 'art' },
  { name: 'Commercial', slug: 'commercial' },
];

function hashPassword(password: string): string {
  const salt = randomBytes(16).toString('hex');
  const derived = scryptSync(password, salt, 32, {
    N: 1 << 14,
    r: 8,
    p: 1,
    maxmem: 64 * 1024 * 1024,
  }) as Buffer;
  return ['scrypt', '14', '8', '1', salt, derived.toString('base64')].join('$');
}

function id(prefix: string, key: string): string {
  return `seed_${prefix}_${key}`.slice(0, 64);
}

async function findTenant(): Promise<{ tenant: TenantConfigEntity; control: DataSource }> {
  if (!CONTROL_DB_URL) throw new Error('CONTROL_DB_URL is required');
  const control = new DataSource(buildControlDataSourceOptions(CONTROL_DB_URL));
  await control.initialize();
  try {
    const repo = control.getRepository(TenantConfigEntity);
    let tenant = TENANT_ID
      ? await repo.findOneBy({ id: TENANT_ID })
      : TENANT_SLUG
        ? await repo.findOneBy({ slug: TENANT_SLUG })
        : null;
    if (!tenant) {
      const readyTenants = await repo.find({
        where: { provisioningStatus: 'ready' },
        order: { createdAt: 'ASC' },
      });
      if (readyTenants.length !== 1) {
        throw new Error(
          `Set SCHOOL_TENANT_ID or SCHOOL_TENANT_SLUG; found ${readyTenants.length} ready tenants`,
        );
      }
      tenant = readyTenants[0];
    }
    if (!tenant) throw new Error('Tenant was not found');
    if (tenant.provisioningStatus !== 'ready') {
      throw new Error(`Tenant is not ready (status: ${tenant.provisioningStatus})`);
    }
    if (!tenant.dbUri.startsWith('postgres://') && !tenant.dbUri.startsWith('postgresql://')) {
      tenant.dbUri = new CryptoService().decrypt(tenant.dbUri);
    }
    return { tenant, control };
  } catch (error) {
    await control.destroy();
    throw error;
  }
}

async function main() {
  if (!DEFAULT_PASSWORD || DEFAULT_PASSWORD.length < 8) {
    throw new Error('SEED_DEFAULT_PASSWORD is required and must be at least 8 characters');
  }
  const { tenant, control } = await findTenant();
  const db = new DataSource(buildTenantDataSourceOptions(tenant.dbUri));
  await db.initialize();
  await db.query(
    'ALTER TABLE users ADD COLUMN IF NOT EXISTS school_level varchar(32) NULL',
  );

  const users = new Map<string, string>();
  const query = (sql: string, params: unknown[] = []) => db.query(sql, params);
  const controlQuery = (sql: string, params: unknown[] = []) => control.query(sql, params);

  async function ensureMapping(userId: string, email: string) {
    const existing = await controlQuery(
      `SELECT id FROM user_tenant_mappings WHERE tenantid = $1 AND email = $2 AND isactive = true LIMIT 1`,
      [tenant.id, email.toLowerCase()],
    );
    if (existing[0]) return;
    await controlQuery(
      `INSERT INTO user_tenant_mappings
        (id, userid, tenantid, email, isprimarytenant, isactive)
       VALUES ($1, $2, $3, $4, false, true)`,
      [id('mapping', `${tenant.id}_${email}`), userId, tenant.id, email.toLowerCase()],
    );
  }

  async function ensureUser(
    key: string,
    email: string,
    name: string,
    roles: string[],
    schoolLevel?: string,
    major?: string | null,
  ): Promise<string> {
    const effectiveSchoolLevel = schoolLevel
      || (roles.includes('head_teacher') || roles.includes('assistant_head_teacher')
        ? 'primary'
        : roles.includes('principal') || roles.includes('vice_principal')
          ? 'secondary'
          : roles.includes('student') || roles.includes('class_teacher') || roles.includes('subject_teacher')
            ? 'all'
            : 'all');
    const existing = await query('SELECT id FROM users WHERE email = $1 LIMIT 1', [email.toLowerCase()]);
    if (existing[0]) {
      users.set(key, existing[0].id);
      await query(
        'UPDATE users SET name = $1, major = $2, school_level = $3, updated_at = NOW() WHERE id = $4',
        [name, major || null, effectiveSchoolLevel, existing[0].id],
      );
      await ensureMapping(existing[0].id, email);
      return existing[0].id;
    }
    const userId = id('usr', key);
    await query(
      `INSERT INTO users
        (id, email, name, major, school_level, password_hash, roles, permissions, capabilities, is_active)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true)`,
      [
        userId,
        email.toLowerCase(),
        name,
        major || null,
        effectiveSchoolLevel,
        hashPassword(DEFAULT_PASSWORD),
        JSON.stringify(roles),
        JSON.stringify(derivePermissionsForRoles(roles)),
        JSON.stringify([]),
      ],
    );
    users.set(key, userId);
    await ensureMapping(userId, email);
    return userId;
  }

  async function ensureClass(item: (typeof classes)[number]) {
    const existing = await query('SELECT id FROM academic_classes WHERE code = $1 LIMIT 1', [item.code]);
    if (existing[0]) return existing[0].id as string;
    const classId = id('class', item.code.toLowerCase());
    await query(
      `INSERT INTO academic_classes (id, name, code, school_level, capacity)
       VALUES ($1, $2, $3, $4, $5)`,
      [classId, item.name, item.code, item.level, item.level === 'nursery' ? 20 : 30],
    );
    return classId;
  }

  async function ensureSubject(name: string, level: 'nursery' | 'primary' | 'jss' | 'sss') {
    const code = `${level}_${name.toLowerCase().replace(/[^a-z0-9]+/g, '_')}`;
    const existing = await query('SELECT id FROM academic_subjects WHERE code = $1 LIMIT 1', [code]);
    if (existing[0]) return existing[0].id as string;
    const subjectId = id('subject', code);
    const levelClasses = classes.filter((item) => item.level === level).map((item) => id('class', item.code.toLowerCase()));
    const category = level === 'jss' || level === 'sss'
      ? subjectCategories.find((item) =>
          /biology|chemistry|physics|science|technology|agricultural/i.test(name)
            ? item.slug === 'science'
            : /economics|business/i.test(name)
              ? item.slug === 'commercial'
              : item.slug === 'art',
        )
      : null;
    const categoryRows = category
      ? await query('SELECT id FROM academic_subject_categories WHERE slug = $1 LIMIT 1', [category.slug])
      : [];
    await query(
      `INSERT INTO academic_subjects (id, name, code, school_level, category_id, class_ids)
       VALUES ($1, $2, $3, $4, $5, $6)`,
      [subjectId, name, code, level, categoryRows[0]?.id || null, JSON.stringify(levelClasses)],
    );
    return subjectId;
  }

  async function ensureLink(table: string, columns: string[], values: unknown[], conflict: string) {
    const existing = await query(
      `SELECT id FROM ${table} WHERE ${columns.map((column, i) => `${column} = $${i + 1}`).join(' AND ')} LIMIT 1`,
      values,
    );
    if (existing[0]) return;
    const linkKey = createHash('sha256')
      .update(`${table}:${values.map((value) => String(value)).join('|')}`)
      .digest('hex')
      .slice(0, 32);
    await query(
      `INSERT INTO ${table} (id, ${columns.join(', ')}) VALUES ($1, ${values.map((_, i) => `$${i + 2}`).join(', ')})
       ON CONFLICT (${conflict}) DO NOTHING`,
      [`seed_link_${linkKey}`, ...values],
    );
  }

  try {
    for (const category of subjectCategories) {
      await query(
        `INSERT INTO academic_subject_categories (id, name, slug)
         VALUES ($1, $2, $3)
         ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, is_active = true`,
        [id('category', category.slug), category.name, category.slug],
      );
    }
    const fixedStaff = [
      ['school_admin', 'schooladmin@example.com', 'Adewale Ogunleye', ['school_admin']],
      ['it_admin', 'itadmin@example.com', 'Chinedu Nnamani', ['it_admin']],
      ['head_teacher', 'headteacher@example.com', 'Grace Nwankwo', ['head_teacher']],
      ['principal', 'principal@example.com', 'Ibrahim Suleiman', ['principal']],
      ['assistant_head_teacher', 'assistantheadteacher@example.com', 'Adaeze Okonkwo', ['assistant_head_teacher']],
      ['vice_principal', 'viceprincipal@example.com', 'Chukwuemeka Obi', ['vice_principal']],
      ['bursar', 'bursar@example.com', 'Mariam Bello', ['bursar']],
      ['driver', 'driver@example.com', 'Emeka Okafor', ['driver']],
      ['guard1', 'guard1@example.com', 'Sani Abdullahi', ['guard']],
      ['guard2', 'guard2@example.com', 'Peter Nwachukwu', ['guard']],
      ['nurse', 'nurse@example.com', 'Amaka Eze', ['nurse']],
      ['administrative1', 'administrative1@example.com', 'Funmi Afolabi', ['administrative_staff']],
      ['administrative2', 'administrative2@example.com', 'Yusuf Lawal', ['administrative_staff']],
      ['administrative3', 'administrative3@example.com', 'Blessing Chukwu', ['administrative_staff']],
    ] as const;
    for (const [key, email, name, roles] of fixedStaff) await ensureUser(key, email, name, [...roles]);
    for (const [key] of fixedStaff) {
      const staffId = users.get(key);
      await query('DELETE FROM academic_class_teacher_links WHERE teacher_id = $1', [staffId]);
      await query('DELETE FROM academic_subject_teacher_links WHERE teacher_id = $1', [staffId]);
    }

    const classIds = new Map<string, string>();
    for (const item of classes) classIds.set(item.code, await ensureClass(item));

    const subjectIds = new Map<string, string>();
    for (const name of nurserySubjects) subjectIds.set(`nursery:${name}`, await ensureSubject(name, 'nursery'));
    for (const name of primarySubjects) subjectIds.set(`primary:${name}`, await ensureSubject(name, 'primary'));
    for (const name of jssSubjects) subjectIds.set(`jss:${name}`, await ensureSubject(name, 'jss'));
    for (const name of sssSubjects) subjectIds.set(`sss:${name}`, await ensureSubject(name, 'sss'));

    const classTeacherIds: string[] = [];
    for (let i = 0; i < classes.length; i++) {
      const secondary = classes[i].level === 'jss' || classes[i].level === 'sss';
      const roles = secondary ? ['class_teacher', 'subject_teacher'] : ['class_teacher'];
      const teacherId = await ensureUser(
        `class_teacher_${i + 1}`,
        `classteacher${i + 1}@example.com`,
        classTeacherNames[i],
        roles,
        secondary ? 'secondary' : 'primary',
      );
      classTeacherIds.push(teacherId);
      await query(
        'DELETE FROM academic_class_teacher_links WHERE teacher_id = $1 AND class_id <> $2',
        [teacherId, classIds.get(classes[i].code)],
      );
      await query('DELETE FROM academic_subject_teacher_links WHERE teacher_id = $1', [teacherId]);
      await ensureLink('academic_class_teacher_links', ['class_id', 'teacher_id'], [classIds.get(classes[i].code), teacherId], 'class_id, teacher_id');
      if (!secondary) {
        const subjectNames = classes[i].level === 'nursery' ? nurserySubjects : primarySubjects;
        for (const subjectName of subjectNames) {
          await ensureLink(
            'academic_subject_teacher_links',
            ['subject_id', 'teacher_id'],
            [subjectIds.get(`${classes[i].level}:${subjectName}`), teacherId],
            'subject_id, teacher_id',
          );
        }
      }
    }

    const subjectTeacherIds: string[] = [];
    for (let i = 0; i < 12; i++) {
      subjectTeacherIds.push(
        await ensureUser(
          `subject_teacher_${i + 1}`,
          `subjectteacher${i + 1}@example.com`,
          subjectTeacherNames[i],
          ['subject_teacher'],
          'secondary',
        ),
      );
    }
    for (const teacherId of subjectTeacherIds) {
      await query('DELETE FROM academic_class_teacher_links WHERE teacher_id = $1', [teacherId]);
    }
    const allSubjects = [
      ...jssSubjects.map((name) => `jss:${name}`),
      ...sssSubjects.map((name) => `sss:${name}`),
    ];
    for (let i = 0; i < allSubjects.length; i++) {
      await ensureLink(
        'academic_subject_teacher_links',
        ['subject_id', 'teacher_id'],
        [subjectIds.get(allSubjects[i]), subjectTeacherIds[Math.floor(i / 2)]],
        'subject_id, teacher_id',
      );
    }
    for (let i = 0; i < 6; i++) {
      for (const subjectKey of [allSubjects[i], allSubjects[i + 6]]) {
        await ensureLink(
          'academic_subject_teacher_links',
          ['subject_id', 'teacher_id'],
          [subjectIds.get(subjectKey), classTeacherIds[9 + i]],
          'subject_id, teacher_id',
        );
      }
    }

    const classStudentCounts = new Map<string, number>();
    for (const item of classes) classStudentCounts.set(item.code, 5);
    let studentNumber = 0;
    const studentIds: string[] = [];
    for (const item of classes) {
      for (let i = 0; i < (classStudentCounts.get(item.code) || 0); i++) {
        studentNumber++;
        const studentId = await ensureUser(
          `student_${studentNumber}`,
          `student${String(studentNumber).padStart(3, '0')}@example.com`,
          `${studentFirstNames[(studentNumber - 1) % studentFirstNames.length]} ${studentLastNames[Math.floor((studentNumber - 1) / studentFirstNames.length) % studentLastNames.length]}`,
          ['student'],
          item.level === 'nursery' || item.level === 'primary' ? 'primary' : 'secondary',
          item.level === 'sss'
            ? ['Science', 'Art', 'Commercial'][studentNumber % 3]
            : null,
        );
        studentIds.push(studentId);
        await ensureLink('student_class_enrollments', ['student_id', 'class_id'], [studentId, classIds.get(item.code)], 'student_id, class_id');
      }
    }

    for (let i = 0; i < Math.ceil(studentIds.length / 4); i++) {
      const parentId = await ensureUser(
        `parent_${i + 1}`,
        `parent${String(i + 1).padStart(3, '0')}@example.com`,
        `${parentFirstNames[i % parentFirstNames.length]} ${parentLastNames[Math.floor(i / parentFirstNames.length) % parentLastNames.length]}`,
        ['parent'],
      );
      for (const studentId of studentIds.slice(i * 4, i * 4 + 4)) {
        await ensureLink('parent_student_links', ['parent_id', 'student_id'], [parentId, studentId], 'parent_id, student_id');
      }
    }

    console.log(JSON.stringify({
      tenant: tenant.slug,
      classes: classes.length,
      nurserySubjects: nurserySubjects.length,
      primarySubjects: primarySubjects.length,
      jssSubjects: jssSubjects.length,
      sssSubjects: sssSubjects.length,
      students: studentIds.length,
      parents: Math.ceil(studentIds.length / 4),
      password: DEFAULT_PASSWORD,
    }, null, 2));
  } finally {
    await db.destroy();
    await control.destroy();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
