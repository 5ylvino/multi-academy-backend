import { BadRequestException, ForbiddenException, Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { UserAuthorityService } from '../control-plane/user-authority.service';
import { PasswordService } from '../common/security/password.service';
import { generateUserId } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { ControlDbService } from '../database/control-db.service';
import { UserTenantMappingEntity } from '../control-plane/entities/user-tenant-mapping.entity';
import {
  actorHasPermission,
  actorHasWildcard,
  arraysEqualAsSets,
  derivePermissionsForRoles,
  findInvalidRoles,
  findRoleOnlyCapabilities,
  findUnknownCapabilities,
} from '../common/auth/role-permissions';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { AuditLogService } from '../common/audit/audit-log.service';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { ListCacheService, stableStringify } from '../common/cache/list-cache.service';

const OPS_STAFF_ROLES = new Set(['driver', 'guard', 'nurse']);
const SCHOOL_LEVELS = new Set(['all', 'nursery', 'primary', 'secondary', 'jss', 'sss']);
const GENDERS = new Set(['male', 'female']);
const PRIMARY_ROLES = new Set(['head_teacher', 'assistant_head_teacher']);
const SECONDARY_ROLES = new Set(['principal', 'vice_principal']);
const LEVEL_REQUIRED_ROLES = new Set([
  'head_teacher',
  'assistant_head_teacher',
  'principal',
  'vice_principal',
  'class_teacher',
  'subject_teacher',
  'bursar',
  'student',
]);

export type CreateTenantUserInput = {
  fullName: string;
  email: string;
  password: string;
  roles: string[];
  phone?: string;
  major?: string | null;
  schoolLevel?: string | null;
  gender?: string | null;
  privilegeChangeReason?: string;
};

@Injectable()
export class UsersService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly passwordService: PasswordService,
    private readonly controlDb: ControlDbService,
    private readonly userAuthority: UserAuthorityService,
    private readonly flags: FeatureFlagService,
    private readonly audit: AuditLogService,
    private readonly listCache: ListCacheService,
  ) {}

  private assertCanAssignPrivileges(actor: AuthUserClaims | undefined): asserts actor is AuthUserClaims {
    if (!actor || !actorHasPermission(actor, 'roles:assign')) {
      throw new ForbiddenException(
        'roles:assign permission is required to change roles or custom capabilities',
      );
    }
  }

  private assertNotSelfPrivilegeChange(actor: AuthUserClaims, targetUserId: string) {
    if (actorHasWildcard(actor)) return;
    const actorId = actor.user_id || actor.sub;
    if (actorId && actorId === targetUserId) {
      throw new ForbiddenException(
        'You cannot change your own roles or custom capabilities. Ask another authorized administrator.',
      );
    }
  }

  private assertCapabilitiesAllowed(capabilities: string[]) {
    const unknown = findUnknownCapabilities(capabilities);
    if (unknown.length > 0) {
      throw new BadRequestException(
        `Unknown or disallowed capabilities: ${unknown.join(', ')}`,
      );
    }
    const roleOnly = findRoleOnlyCapabilities(capabilities);
    if (roleOnly.length > 0) {
      throw new BadRequestException(
        `These capabilities require assigning the matching role (cannot be custom overrides): ${roleOnly.join(', ')}`,
      );
    }
  }

  private requirePrivilegeReason(reason: string | undefined, context: string) {
    const trimmed = (reason || '').trim();
    if (trimmed.length < 3) {
      throw new BadRequestException(
        `privilegeChangeReason is required (min 3 chars) when ${context}`,
      );
    }
    return trimmed;
  }

  private async assertOpsRolesAllowed(tenantId: string, roles: string[]) {
    if (!roles.some((r) => OPS_STAFF_ROLES.has(r))) return;
    await this.flags.assertEnabled(tenantId, 'roles.ops_staff');
  }

  private normalizeSchoolLevel(value: string | null | undefined, roles: string[]) {
    const hasPrimaryRole = roles.some((role) => PRIMARY_ROLES.has(role));
    const hasSecondaryRole = roles.some((role) => SECONDARY_ROLES.has(role));
    const hasRequiredSelectionRole = roles.some((role) => LEVEL_REQUIRED_ROLES.has(role));
    if (value === undefined || value === null || value === '') {
      if (hasPrimaryRole && !hasSecondaryRole) return 'primary';
      if (hasSecondaryRole && !hasPrimaryRole) return 'secondary';
      if (!hasRequiredSelectionRole) return 'all';
      return null;
    }
    const level = String(value).trim().toLowerCase();
    if (!SCHOOL_LEVELS.has(level)) throw new BadRequestException(`Unknown school level: ${value}`);
    if (hasPrimaryRole && !['nursery', 'primary'].includes(level)) {
      throw new BadRequestException('Primary academic roles require a primary or nursery school level');
    }
    if (hasSecondaryRole && !['secondary', 'jss', 'sss'].includes(level)) {
      throw new BadRequestException('Secondary academic roles require a secondary school level');
    }
    return level;
  }

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    return this.tenantConnections.getOrCreate(tenant!.id, tenant!.dbUri);
  }

  /** Ensure optional columns exist without a full migration for every tenant. */
  private async ensureUserColumns(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS users (
        id varchar(64) PRIMARY KEY,
        email varchar(255) NOT NULL UNIQUE,
        name varchar(255) NOT NULL,
        phone varchar(32) NULL,
        password_hash varchar(255) NOT NULL,
        roles TEXT NOT NULL,
        permissions TEXT NOT NULL,
        capabilities TEXT NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    try {
      await ds.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS passport_photo TEXT NULL`);
    } catch {
      // Older Postgres without IF NOT EXISTS — ignore if column already exists
      try {
        await ds.query(`ALTER TABLE users ADD COLUMN passport_photo TEXT NULL`);
      } catch {
        // column exists
      }
    }
    await ds.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`);
    await ds.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS archive_reason text NULL`);
    await ds.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS major varchar(128) NULL`);
    await ds.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS school_level varchar(32) NULL`);
    await ds.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS gender varchar(16) NULL`);
    await ds.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS admission_no varchar(12) NULL`);
    await ds.query(`CREATE UNIQUE INDEX IF NOT EXISTS users_admission_no_unique ON users (admission_no)`);
  }

  private async generateAdmissionNo(ds: any): Promise<string> {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      const admissionNo = String(Math.floor(100000000000 + Math.random() * 900000000000));
      const existing = await runDbQuery(
        ds,
        `SELECT id FROM users WHERE admission_no = ? LIMIT 1`,
        [admissionNo],
      );
      if (existing.length === 0) return admissionNo;
    }
    throw new BadRequestException('Unable to generate a unique admission number');
  }

  private assertPassportPhoto(value: string | null | undefined) {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value !== 'string') {
      throw new BadRequestException('Invalid passport photo');
    }
    if (value.length > 280_000) {
      throw new BadRequestException('Passport photo is too large. Use a passport-size JPEG under ~200KB.');
    }
    if (!/^data:image\/(jpeg|jpg|png|webp);base64,/i.test(value)) {
      throw new BadRequestException('Passport photo must be a JPEG, PNG, or WebP data URL');
    }
    return value;
  }

  private mapUserRow(row: any) {
    let roles: string[] = [];
    try {
      if (typeof row?.roles === 'string') {
        roles = JSON.parse(row.roles);
      } else if (Array.isArray(row?.roles)) {
        roles = row.roles;
      }
    } catch {
      roles = [];
    }

    let capabilities: string[] = [];
    const rawCaps = row?.capabilities ?? row?.Capabilities;
    try {
      if (typeof rawCaps === 'string') {
        capabilities = JSON.parse(rawCaps);
      } else if (Array.isArray(rawCaps)) {
        capabilities = rawCaps;
      }
    } catch {
      capabilities = [];
    }

    const { isactive, passport_photo, passportphoto, ...rest } = row;
    const storedSchoolLevel = row?.schoolLevel || row?.schoollevel || row?.school_level;
    const schoolLevel = storedSchoolLevel
      || (roles.includes('head_teacher') || roles.includes('assistant_head_teacher')
        ? 'primary'
        : roles.includes('principal') || roles.includes('vice_principal')
          ? 'secondary'
          : 'all');
    return {
      ...rest,
      roles,
      capabilities,
      role: roles[0] || null,
      isActive: !!(row?.isActive || isactive),
      passportPhoto: row?.passportPhoto || passport_photo || passportphoto || null,
      major: row?.major || null,
      schoolLevel,
      gender: row?.gender || null,
      admissionNo: row?.admissionNo || row?.admission_no || null,
    };
  }

  private async enrichUsersWithLinks(ds: any, users: any[]) {
    let enriched = users;
    // Enrich student/parent links when available.
    try {
      const links: Array<{ parent_id: string; student_id: string }> = await runDbQuery(
        ds,
        `SELECT parent_id, student_id FROM parent_student_links`,
      );
      const studentIdToParentId = new Map(links.map(l => [l.student_id, l.parent_id]));
      const parentIdToStudentId = new Map(links.map(l => [l.parent_id, l.student_id]));
      enriched = enriched.map((u: any) => {
        const rolePrimary = u.role || (Array.isArray(u.roles) ? u.roles[0] : undefined);
        if (rolePrimary === 'student') {
          return { ...u, parentId: studentIdToParentId.get(u.id) || null };
        }
        if (rolePrimary === 'parent') {
          return { ...u, studentId: parentIdToStudentId.get(u.id) || null };
        }
        return u;
      });
    } catch {
      // ignore when link table isn't available yet
    }

    // Enrich students with the classes they are currently enrolled in.
    try {
      const enrollments: Array<{ student_id: string; class_id: string; class_name: string }> = await runDbQuery(
        ds,
        `SELECT e.student_id, e.class_id, c.name as class_name
         FROM student_class_enrollments e
         JOIN academic_classes c ON c.id = e.class_id
         ORDER BY c.name`,
      );
      const classNamesByStudentId = new Map<string, string[]>();
      const classIdsByStudentId = new Map<string, string[]>();
      for (const row of enrollments) {
        const names = classNamesByStudentId.get(row.student_id) || [];
        if (row.class_name && !names.includes(row.class_name)) names.push(row.class_name);
        classNamesByStudentId.set(row.student_id, names);
        const ids = classIdsByStudentId.get(row.student_id) || [];
        if (row.class_id && !ids.includes(row.class_id)) ids.push(row.class_id);
        classIdsByStudentId.set(row.student_id, ids);
      }
      enriched = enriched.map((u: any) =>
        (u.role || u.roles?.[0]) === 'student'
          ? {
              ...u,
              classNames: classNamesByStudentId.get(u.id) || [],
              classIds: classIdsByStudentId.get(u.id) || [],
            }
          : u,
      );
    } catch {
      // Class enrollment tables may not exist for a newly provisioned school.
    }

    // Enrich teacher class/subject assignment counts when available.
    try {
      const classLinks: Array<{ teacher_id: string; class_count: string | number; class_names: string[] }> = await runDbQuery(
        ds,
        `SELECT ctl.teacher_id, COUNT(*) as class_count,
                ARRAY_AGG(c.name ORDER BY c.name) as class_names
         FROM academic_class_teacher_links ctl
         JOIN academic_classes c ON c.id = ctl.class_id
         GROUP BY ctl.teacher_id`,
      );
      const subjectLinks: Array<{ teacher_id: string; subject_count: string | number; subject_names: string[] }> = await runDbQuery(
        ds,
        `SELECT stl.teacher_id, COUNT(*) as subject_count,
                ARRAY_AGG(s.name ORDER BY s.name) as subject_names
         FROM academic_subject_teacher_links stl
         JOIN academic_subjects s ON s.id = stl.subject_id
         GROUP BY stl.teacher_id`,
      );
      const classCountByTeacherId = new Map(
        classLinks.map(row => [row.teacher_id, Number(row.class_count) || 0]),
      );
      const subjectCountByTeacherId = new Map(
        subjectLinks.map(row => [row.teacher_id, Number(row.subject_count) || 0]),
      );
      const classNamesByTeacherId = new Map(
        classLinks.map(row => [row.teacher_id, row.class_names || []]),
      );
      const subjectNamesByTeacherId = new Map(
        subjectLinks.map(row => [row.teacher_id, row.subject_names || []]),
      );
      enriched = enriched.map((u: any) => {
        const roles: string[] = Array.isArray(u.roles) ? u.roles : [];
        const isClassTeacher = roles.includes('class_teacher');
        const isSubjectTeacher = roles.includes('subject_teacher');
        if (!isClassTeacher && !isSubjectTeacher) return u;
        return {
          ...u,
          classAssignedCount: classCountByTeacherId.get(u.id) || 0,
          subjectAssignedCount: subjectCountByTeacherId.get(u.id) || 0,
          assignedClassNames: classNamesByTeacherId.get(u.id) || [],
          assignedSubjectNames: subjectNamesByTeacherId.get(u.id) || [],
        };
      });
    } catch {
      // ignore when link tables aren't available yet
    }

    return enriched;
  }

  async listTenantUsers(params: {
    tenantId: string;
    role?: string;
    category?: 'academic' | 'administrative_financial' | 'student_parent';
    page?: number;
    limit?: number;
    forceRefresh?: boolean;
  }) {
    const { forceRefresh, ...cacheParams } = params;
    const key = `tenant:${params.tenantId}:users:${stableStringify(cacheParams)}`;
    return this.listCache.getOrLoad(key, () => this.listTenantUsersUncached(cacheParams), forceRefresh);
  }

  private async listTenantUsersUncached(params: {
    tenantId: string;
    role?: string;
    category?: 'academic' | 'administrative_financial' | 'student_parent';
    page?: number;
    limit?: number;
  }) {
    const { tenantId, role, category } = params;
    const ds = await this.getTenantDs(tenantId);
    await this.ensureUserColumns(ds);
    // List payloads omit passport_photo (often a large base64 blob); detail
    // endpoints still return it. Cap rows so a large school cannot dump the
    // whole users table into one response.
    const roleLike = (value: string) => `%"${value}"%`;
    const where: string[] = [];
    const whereParams: string[] = [];
    if (role) {
      where.push('roles LIKE ?');
      whereParams.push(roleLike(role));
    }
    if (category === 'academic') {
      where.push(
        '(roles LIKE ? OR roles LIKE ? OR roles LIKE ? OR roles LIKE ? OR roles LIKE ? OR roles LIKE ?)',
      );
      whereParams.push(
        roleLike('head_teacher'),
        roleLike('principal'),
        roleLike('assistant_head_teacher'),
        roleLike('vice_principal'),
        roleLike('class_teacher'),
        roleLike('subject_teacher'),
      );
    } else if (category === 'student_parent') {
      where.push('(roles LIKE ? OR roles LIKE ?)');
      whereParams.push(roleLike('student'), roleLike('parent'));
    } else if (category === 'administrative_financial') {
      where.push(
        '(roles NOT LIKE ? AND roles NOT LIKE ? AND roles NOT LIKE ? AND roles NOT LIKE ? AND roles NOT LIKE ? AND roles NOT LIKE ? AND roles NOT LIKE ? AND roles NOT LIKE ?)',
      );
      whereParams.push(
        roleLike('student'),
        roleLike('parent'),
        roleLike('head_teacher'),
        roleLike('principal'),
        roleLike('assistant_head_teacher'),
        roleLike('vice_principal'),
        roleLike('class_teacher'),
        roleLike('subject_teacher'),
      );
    }
    const whereSql = where.length > 0 ? `WHERE ${where.join(' AND ')}` : '';
    const isPaginated = category !== undefined || params.page !== undefined || params.limit !== undefined;
    const page = Math.max(1, Number.isFinite(params.page) ? Math.floor(params.page as number) : 1);
    const pageSize = Math.min(
      50,
      Math.max(1, Number.isFinite(params.limit) ? Math.floor(params.limit as number) : 50),
    );
    const countRows = isPaginated
      ? await runDbQuery(ds, `SELECT COUNT(*)::int AS total FROM users ${whereSql}`, whereParams)
      : [];
    const total = Number(countRows[0]?.total || 0);
    const paginationSql = isPaginated ? 'LIMIT ? OFFSET ?' : 'LIMIT 1000';
    const paginationParams = isPaginated ? [pageSize, (page - 1) * pageSize] : [];
    const rows = await runDbQuery(
      ds,
      `SELECT id, email, name, phone, major, school_level as schoolLevel, gender, admission_no as admissionNo, roles, capabilities, is_active as isActive,
              created_at as createdAt, updated_at as updatedAt
       FROM users
       ${whereSql}
       ORDER BY created_at DESC, id DESC
       ${paginationSql}`,
      [...whereParams, ...paginationParams],
    );
    const mapped = rows.map((r: any) => this.mapUserRow(r));
    const enriched = await this.enrichUsersWithLinks(ds, mapped);
    if (!isPaginated) return enriched;
    return {
      rows: enriched,
      meta: {
        page,
        pageSize,
        total,
        totalPages: Math.ceil(total / pageSize),
      },
    };
  }

  async getTenantUserById(params: { tenantId: string; id: string }) {
    const { tenantId, id } = params;
    const ds = await this.getTenantDs(tenantId);
    await this.ensureUserColumns(ds);
    const rows = await runDbQuery(
      ds,
      `SELECT id, email, name, phone, major, school_level as schoolLevel, gender, admission_no as admissionNo, roles, capabilities, is_active as isActive, passport_photo as passportPhoto,
              created_at as createdAt, updated_at as updatedAt
       FROM users WHERE id = ? LIMIT 1`,
      [id],
    );
    const row = rows[0] || null;
    return row ? this.mapUserRow(row) : null;
  }

  async createTenantUser(params: {
    tenantId: string;
    body: CreateTenantUserInput;
    actor: AuthUserClaims;
    requestMeta?: { ip?: string | null; userAgent?: string | null };
  }) {
    const { tenantId, body, actor, requestMeta } = params;
    this.assertCanAssignPrivileges(actor);
    const reason = this.requirePrivilegeReason(
      body.privilegeChangeReason,
      'creating a user with roles',
    );

    const ds = await this.getTenantDs(tenantId);
    const email = body.email.toLowerCase();
    const roles = Array.isArray(body.roles) ? body.roles : [];
    if (roles.length === 0) {
      throw new BadRequestException('At least one role is required');
    }
    const invalidRoles = findInvalidRoles(roles);
    if (invalidRoles.length > 0) {
      throw new BadRequestException(`Unknown roles: ${invalidRoles.join(', ')}`);
    }
    await this.assertOpsRolesAllowed(tenantId, roles);
    const schoolLevel = this.normalizeSchoolLevel(body.schoolLevel, roles);
    const gender = body.gender == null || body.gender === ''
      ? null
      : String(body.gender).trim().toLowerCase();
    if (gender && !GENDERS.has(gender)) {
      throw new BadRequestException('Gender must be male or female');
    }
    const admissionNo = roles.includes('student') ? await this.generateAdmissionNo(ds) : null;
    if (roles.some((role) => LEVEL_REQUIRED_ROLES.has(role)) && !schoolLevel) {
      throw new BadRequestException('A school level is required for this role');
    }
    const existingByEmail = await runDbQuery(
      ds,
      `SELECT id FROM users WHERE email = ? LIMIT 1`,
      [email],
    );
    if (existingByEmail.length > 0) {
      throw new BadRequestException('A user with this email already exists');
    }

    await this.ensureUserColumns(ds);

    const userId = generateUserId();
    const passwordHash = this.passwordService.hashPassword(body.password);
    const permissions: string[] = derivePermissionsForRoles(roles);
    const capabilities: string[] = [];
    await runDbQuery(
      ds,
      `
      INSERT INTO users (id, email, name, phone, major, school_level, gender, admission_no, password_hash, roles, permissions, capabilities, is_active, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, true, NOW(), NOW())
      `,
      [
        userId,
        email,
        body.fullName,
        body.phone || null,
        body.major || null,
        schoolLevel,
        gender,
        admissionNo,
        passwordHash,
        JSON.stringify(roles),
        JSON.stringify(permissions),
        JSON.stringify(capabilities),
      ],
    );

    const controlDs = await this.controlDb.getDataSource();
    const mappingRepo = controlDs.getRepository(UserTenantMappingEntity);
    const existingTenantMapping = await mappingRepo.findOne({
      where: { tenantId, email, isActive: true },
    });
    if (!existingTenantMapping) {
      const existingPrimaryForEmail = await mappingRepo.findOne({
        where: { email, isPrimaryTenant: true, isActive: true },
      });
      await mappingRepo.save(
        mappingRepo.create({
          id: `map_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          userId,
          tenantId,
          email,
          isPrimaryTenant: !existingPrimaryForEmail,
          isActive: true,
        }),
      );
    }

    await this.audit.logPrivilegeChange({
      actorUserId: actor.user_id || actor.sub,
      tenantId,
      targetUserId: userId,
      reason,
      before: { roles: [], capabilities: [] },
      after: { roles, capabilities },
      ip: requestMeta?.ip,
      userAgent: requestMeta?.userAgent,
    });

    this.listCache.invalidate(`tenant:${tenantId}:users:`);
    return {
      id: userId,
      email,
      name: body.fullName,
      roles,
      isActive: true,
      createdAt: new Date().toISOString(),
    };
  }

  async updateTenantUser(params: {
    tenantId: string;
    id: string;
    body: {
      name?: string;
      email?: string;
      roles?: string[];
      capabilities?: string[];
      privilegeChangeReason?: string;
      isActive?: boolean;
      password?: string;
      phone?: string;
      major?: string | null;
      schoolLevel?: string | null;
      gender?: string | null;
      passportPhoto?: string | null;
    };
    actor?: AuthUserClaims;
    requestMeta?: { ip?: string | null; userAgent?: string | null };
  }) {
    const { tenantId, id, body, actor, requestMeta } = params;
    const ds = await this.getTenantDs(tenantId);
    await this.ensureUserColumns(ds);
    const existing = await this.getTenantUserById({ tenantId, id });
    if (!existing) {
      throw new NotFoundException('User not found');
    }

    const beforeRoles: string[] = Array.isArray(existing.roles) ? [...existing.roles] : [];
    const beforeCapabilities: string[] = Array.isArray(existing.capabilities)
      ? [...existing.capabilities]
      : [];

    let nextRoles = beforeRoles;
    let nextCapabilities = beforeCapabilities;
    let nextSchoolLevel = existing.schoolLevel ?? null;
    let nextAdmissionNo = existing.admissionNo ?? null;
    let rolesChanging = false;
    let capabilitiesChanging = false;

    if (body.roles !== undefined) {
      const roles = Array.isArray(body.roles) ? body.roles : [];
      if (roles.length === 0) {
        throw new BadRequestException('At least one role is required');
      }
      const invalidRoles = findInvalidRoles(roles);
      if (invalidRoles.length > 0) {
        throw new BadRequestException(`Unknown roles: ${invalidRoles.join(', ')}`);
      }
      await this.assertOpsRolesAllowed(tenantId, roles);
      nextRoles = roles;
      nextSchoolLevel = this.normalizeSchoolLevel(body.schoolLevel ?? nextSchoolLevel, nextRoles);
      if (nextRoles.some((role) => LEVEL_REQUIRED_ROLES.has(role)) && !nextSchoolLevel) {
        throw new BadRequestException('A school level is required for this role');
      }
      if (nextRoles.includes('student') && !nextAdmissionNo) {
        nextAdmissionNo = await this.generateAdmissionNo(ds);
      }
      rolesChanging = !arraysEqualAsSets(beforeRoles, nextRoles);
    } else if (body.schoolLevel !== undefined) {
      nextSchoolLevel = this.normalizeSchoolLevel(body.schoolLevel, nextRoles);
    }

    if (body.capabilities !== undefined) {
      const capabilities = Array.isArray(body.capabilities)
        ? body.capabilities
            .filter((c) => typeof c === 'string' && c.trim().length > 0)
            .map((c) => c.trim())
        : [];
      nextCapabilities = capabilities;
      capabilitiesChanging = !arraysEqualAsSets(beforeCapabilities, nextCapabilities);
      // Only enforce allowlist when the override set is actually changing.
      // (Lets profile-only updates succeed even if legacy bad overrides exist.)
      if (capabilitiesChanging) {
        this.assertCapabilitiesAllowed(capabilities);
      }
    }

    if (rolesChanging || capabilitiesChanging) {
      this.assertCanAssignPrivileges(actor);
      this.assertNotSelfPrivilegeChange(actor, id);
      const reason = this.requirePrivilegeReason(
        body.privilegeChangeReason,
        'roles or custom capabilities change',
      );

      const updates: string[] = [];
      const values: any[] = [];
      const set = (col: string, val: any) => {
        updates.push(`${col} = ?`);
        values.push(val);
      };
      if (body.name !== undefined) set('name', body.name);
      if (body.email !== undefined) set('email', body.email.toLowerCase());
      if (rolesChanging) {
        set('roles', JSON.stringify(nextRoles));
        set('permissions', JSON.stringify(derivePermissionsForRoles(nextRoles)));
      }
      if (body.schoolLevel !== undefined || rolesChanging) set('school_level', nextSchoolLevel);
      if (rolesChanging && nextRoles.includes('student')) set('admission_no', nextAdmissionNo);
      if (capabilitiesChanging) {
        set('capabilities', JSON.stringify(nextCapabilities));
      }
      if (body.isActive !== undefined) set('is_active', !!body.isActive);
      if (body.phone !== undefined) set('phone', body.phone || null);
      if (body.major !== undefined) set('major', body.major || null);
      if (body.gender !== undefined) {
        const gender = body.gender == null || body.gender === ''
          ? null
          : String(body.gender).trim().toLowerCase();
        if (gender && !GENDERS.has(gender)) {
          throw new BadRequestException('Gender must be male or female');
        }
        set('gender', gender);
      }
      if (body.passportPhoto !== undefined) {
        set('passport_photo', this.assertPassportPhoto(body.passportPhoto));
      }
      if (body.password) {
        set('password_hash', this.passwordService.hashPassword(body.password));
      }
      if (updates.length > 0) {
        values.push(id);
        await runDbQuery(
          ds,
          `UPDATE users SET ${updates.join(', ')}, updated_at = NOW() WHERE id = ?`,
          values,
        );
        this.userAuthority.invalidate(tenantId, id);
      }

      await this.audit.logPrivilegeChange({
        actorUserId: actor.user_id || actor.sub,
        tenantId,
        targetUserId: id,
        reason,
        before: { roles: beforeRoles, capabilities: beforeCapabilities },
        after: { roles: nextRoles, capabilities: nextCapabilities },
        ip: requestMeta?.ip,
        userAgent: requestMeta?.userAgent,
      });

      return this.getTenantUserById({ tenantId, id });
    }

    // Non-privilege fields only (roles/capabilities unchanged or omitted).
    const updates: string[] = [];
    const values: any[] = [];
    const set = (col: string, val: any) => {
      updates.push(`${col} = ?`);
      values.push(val);
    };
    if (body.name !== undefined) set('name', body.name);
    if (body.email !== undefined) set('email', body.email.toLowerCase());
    if (body.isActive !== undefined) set('is_active', !!body.isActive);
    if (body.phone !== undefined) set('phone', body.phone || null);
    if (body.major !== undefined) set('major', body.major || null);
    if (body.gender !== undefined) {
      const gender = body.gender == null || body.gender === ''
        ? null
        : String(body.gender).trim().toLowerCase();
      if (gender && !GENDERS.has(gender)) {
        throw new BadRequestException('Gender must be male or female');
      }
      set('gender', gender);
    }
    if (body.schoolLevel !== undefined) {
      set('school_level', this.normalizeSchoolLevel(body.schoolLevel, nextRoles));
    }
    if (body.passportPhoto !== undefined) {
      set('passport_photo', this.assertPassportPhoto(body.passportPhoto));
    }
    if (body.password) {
      set('password_hash', this.passwordService.hashPassword(body.password));
    }
    if (updates.length > 0) {
      values.push(id);
      await runDbQuery(
        ds,
        `UPDATE users SET ${updates.join(', ')}, updated_at = NOW() WHERE id = ?`,
        values,
      );
      this.userAuthority.invalidate(tenantId, id);
    }

    this.listCache.invalidate(`tenant:${tenantId}:users:`);
    return this.getTenantUserById({ tenantId, id });
  }

  async updateOwnPassportPhoto(params: {
    tenantId: string;
    userId: string;
    passportPhoto: string | null;
  }) {
    return this.updateTenantUser({
      tenantId: params.tenantId,
      id: params.userId,
      body: { passportPhoto: params.passportPhoto },
    });
  }

  async deleteTenantUser(params: { tenantId: string; id: string }) {
    const { tenantId, id } = params;
    const ds = await this.getTenantDs(tenantId);
    await this.ensureUserColumns(ds);
    const existing = await this.getTenantUserById({ tenantId, id });
    if (!existing) {
      throw new NotFoundException('User not found');
    }
    const roles = Array.isArray(existing.roles) ? existing.roles : [];
    await runDbQuery(
      ds,
      `UPDATE users SET is_active = false, archived_at = NOW(), archive_reason = 'left_school', updated_at = NOW() WHERE id = ?`,
      [id],
    );
    // Keep academic and financial records intact. A parent is only retained
    // while at least one linked child remains active in this tenant.
    if (roles.includes('student')) {
      const parents: Array<{ parent_id: string }> = await runDbQuery(
        ds,
        `SELECT DISTINCT parent_id FROM parent_student_links WHERE student_id = ?`,
        [id],
      );
      for (const parent of parents) {
        const activeChildren = await runDbQuery(
          ds,
          `SELECT 1 FROM parent_student_links p
           JOIN users child ON child.id = p.student_id
           WHERE p.parent_id = ? AND child.is_active = true LIMIT 1`,
          [parent.parent_id],
        );
        if (!activeChildren.length) {
          await runDbQuery(
            ds,
            `UPDATE users SET is_active = false, archived_at = NOW(),
             archive_reason = 'no_active_children', updated_at = NOW()
             WHERE id = ? AND roles LIKE '%"parent"%'`,
            [parent.parent_id],
          );
          const controlDs = await this.controlDb.getDataSource();
          const mappingRepo = controlDs.getRepository(UserTenantMappingEntity);
          await mappingRepo.update(
            { tenantId, userId: parent.parent_id },
            { isActive: false },
          );
        }
      }
    }
    // Deactivate control-plane mappings so the deleted user can no longer resolve a tenant at login.
    const controlDs = await this.controlDb.getDataSource();
    const mappingRepo = controlDs.getRepository(UserTenantMappingEntity);
    await mappingRepo.update({ tenantId, userId: id }, { isActive: false });
    this.userAuthority.invalidate(tenantId, id);
    this.listCache.invalidate(`tenant:${tenantId}:users:`);
    return { id };
  }

  async adminResetPassword(params: {
    tenantId: string;
    id: string;
    password?: string;
  }) {
    const { tenantId, id, password } = params;
    const existing = await this.getTenantUserById({ tenantId, id });
    if (!existing) {
      throw new NotFoundException('User not found');
    }

    const generated =
      !password || !password.trim()
        ? Array.from({ length: 10 }, () =>
            'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'[
              Math.floor(Math.random() * 56)
            ],
          ).join('')
        : null;
    const nextPassword = generated || password!.trim();
    if (nextPassword.length < 8) {
      throw new BadRequestException('Password must be at least 8 characters');
    }

    const ds = await this.getTenantDs(tenantId);
    await this.ensureUserColumns(ds);
    const hash = this.passwordService.hashPassword(nextPassword);
    await runDbQuery(
      ds,
      `UPDATE users SET password_hash = ?, updated_at = NOW() WHERE id = ?`,
      [hash, id],
    );
    this.userAuthority.invalidate(tenantId, id);

    return generated
      ? { id, temporaryPassword: generated }
      : { id };
  }
}

