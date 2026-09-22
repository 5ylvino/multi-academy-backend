import {
  Injectable,
  NotFoundException,
  ConflictException,
  BadRequestException,
} from '@nestjs/common';
import {
  generateSboId,
  generateSlugFromName,
  generateTenantId,
  generateUserId,
  isValidSchoolSlug,
} from '../common/utils/id.util';
import {
  TenantConfig,
  TenantUser,
  UserTenantMapping,
} from './control-plane.types';
import { TenantProvisioningService } from './tenant-provisioning.service';
import { TenantConfigCacheService } from './tenant-config-cache.service';
import { ControlDbService } from '../database/control-db.service';
import { TenantConfigEntity } from './entities/tenant-config.entity';
import { UserTenantMappingEntity } from './entities/user-tenant-mapping.entity';
import { RegistrationStagingEntity } from './entities/registration-staging.entity';
import { PasswordService } from '../common/security/password.service';
import { CryptoService } from '../common/security/crypto.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { createHash, randomInt } from 'crypto';
import { matchesSchoolSearch } from '../parent-activation/school-search.util';

const ALL_ROLES = [
  'director',
  'school_admin',
  'it_admin',
  // 'head_teacher',
  // 'principal',
  // 'vice_principal',
  // 'assistant_head_teacher',
  'bursar',
  // 'class_teacher',
  // 'subject_teacher',
  'administrative_staff',
  // 'student',
  // 'parent',
];

@Injectable()
export class ControlPlaneService {
  // Production persistence is handled via MySQL control DB and per-tenant DBs.

  constructor(
    private readonly provisioning: TenantProvisioningService,
    private readonly tenantConfigCache: TenantConfigCacheService,
    private readonly controlDb: ControlDbService,
    private readonly passwordService: PasswordService,
    private readonly cryptoService: CryptoService,
    private readonly tenantConnections: TenantConnectionService,
  ) {}

  async createTenantWithOwnerStaging(input: {
    organizationName?: string;
    schoolLevels?: string[];
    owner: {
      fullName: string;
      email: string;
      password?: string;
      phone?: string;
      [key: string]: any;
    };
  }) {
    const now = new Date();
    const orgName =
      input.organizationName || `${input.owner.fullName}'s Organization`;
    const stagingId = `reg_${Date.now()}_${Math.random()
      .toString(36)
      .slice(2, 8)}`;
    const verificationToken = randomInt(0, 1_000_000)
      .toString()
      .padStart(6, '0');
    const verificationTokenHash = createHash('sha256')
      .update(verificationToken)
      .digest('hex');
    const verificationExpiresAt = new Date(Date.now() + 15 * 60_000);
    const ds = await this.controlDb.getDataSource();
    const stagingRepo = ds.getRepository(RegistrationStagingEntity);
    const email = input.owner.email.toLowerCase().trim();
    const previousRegistrations = await stagingRepo.find({
      where: { email },
      order: { createdAt: 'DESC' },
    });
    if (previousRegistrations.some((registration) => registration.consumedAt)) {
      throw new ConflictException(
        'This email is already associated with a completed school registration. Please use a different email address.',
      );
    }

    const staging =
      previousRegistrations[0] || stagingRepo.create({ id: stagingId });
    staging.fullName = input.owner.fullName;
    staging.email = email;
    staging.phone = input.owner.phone || null;
    staging.installTokenHash = input.owner.installToken
      ? createHash('sha256').update(input.owner.installToken).digest('hex')
      : null;
    staging.deviceHash = input.owner.deviceHash || null;
    staging.organizationName = orgName;
    staging.schoolLevels = JSON.stringify(input.schoolLevels || []);
    staging.encryptedPassword = input.owner.password
      ? this.cryptoService.encrypt(input.owner.password)
      : '';
    staging.verificationTokenHash = verificationTokenHash;
    staging.verificationExpiresAt = verificationExpiresAt;
    staging.consumedAt = null;
    await stagingRepo.save(staging);
    return { id: staging.id, verificationToken };
  }

  async refreshOwnerVerificationToken(email: string) {
    const normalizedEmail = email.toLowerCase().trim();
    const ds = await this.controlDb.getDataSource();
    const stagingRepo = ds.getRepository(RegistrationStagingEntity);
    const registrations = await stagingRepo.find({
      where: { email: normalizedEmail },
      order: { createdAt: 'DESC' },
    });
    const staging = registrations[0];
    if (!staging || staging.consumedAt || !staging.encryptedPassword) {
      return null;
    }

    const verificationToken = randomInt(0, 1_000_000)
      .toString()
      .padStart(6, '0');
    staging.verificationTokenHash = createHash('sha256')
      .update(verificationToken)
      .digest('hex');
    staging.verificationExpiresAt = new Date(Date.now() + 15 * 60_000);
    await stagingRepo.save(staging);
    return { email: normalizedEmail, verificationToken };
  }

  async verifyOwnerEmail(token: string): Promise<TenantConfig> {
    const tokenHash = createHash('sha256')
      .update(token || '')
      .digest('hex');
    const ds = await this.controlDb.getDataSource();
    const stagingRepo = ds.getRepository(RegistrationStagingEntity);
    const staging = await stagingRepo.findOne({
      where: { verificationTokenHash: tokenHash },
    });
    if (!staging || staging.consumedAt) {
      throw new BadRequestException('Invalid or expired verification code');
    }
    if (staging.verificationExpiresAt.getTime() < Date.now()) {
      throw new BadRequestException('Invalid or expired verification code');
    }
    if (!staging.encryptedPassword) {
      throw new BadRequestException('Registration details are incomplete');
    }

    const tenantId = generateTenantId();
    const superUrl =
      process.env.POSTGRES_SUPER_URL || process.env.MYSQL_SUPER_URL;
    const dbName = `tenant_${tenantId}`;
    const tenant: TenantConfig = {
      id: tenantId,
      slug: this.ensureUniqueSlug(
        generateSlugFromName(staging.organizationName),
      ),
      schoolBusinessOrganisationId: this.ensureUniqueSboId(),
      name: staging.organizationName,
      status: 'pending_verification',
      dbName,
      dbUri: this.deriveTenantDbUrl(superUrl, dbName),
      schoolLevels: staging.schoolLevels
        ? JSON.parse(staging.schoolLevels)
        : [],
      ownerRegistrationPayloadJson: {
        fullName: staging.fullName,
        email: staging.email,
        phone: staging.phone,
        installTokenHash: staging.installTokenHash || undefined,
        deviceHash: staging.deviceHash || undefined,
        stagedAt: staging.createdAt.toISOString(),
      },
      ownerStagedPasswordHash: this.passwordService.hashPassword(
        this.cryptoService.decrypt(staging.encryptedPassword),
      ),
      ownerEmailVerified: true,
      provisioningStatus: 'pending',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    const repo = ds.getRepository(TenantConfigEntity);
    await repo.save(
      repo.create({
        id: tenant.id,
        slug: tenant.slug,
        schoolBusinessOrganisationId: tenant.schoolBusinessOrganisationId,
        name: tenant.name,
        status: tenant.status,
        dbName: tenant.dbName,
        dbUri: this.cryptoService.encrypt(tenant.dbUri),
        schoolLevels: JSON.stringify(tenant.schoolLevels),
        ownerRegistrationPayloadJson: JSON.stringify(
          tenant.ownerRegistrationPayloadJson,
        ),
        ownerStagedPasswordHash: tenant.ownerStagedPasswordHash,
        ownerEmailVerified: true,
        onboardingToken: null,
        onboardingTokenExpiresAt: null,
        provisioningStatus: 'pending',
        provisioningError: null,
      }),
    );

    const now = new Date();
    staging.consumedAt = now;
    await stagingRepo.save(staging);
    tenant.updatedAt = now.toISOString();
    this.tenantConfigCache.set(tenant);
    return tenant;
  }

  async provisionVerifiedTenant(tenant: TenantConfig): Promise<void> {
    const ds = await this.controlDb.getDataSource();
    const repo = ds.getRepository(TenantConfigEntity);

    try {
      await this.provisioning.provision(tenant);
      const now = new Date();
      await repo.update(
        { id: tenant.id },
        {
          status: 'active',
          provisioningStatus: 'ready',
          provisioningError: null,
          updatedAt: now as any,
        },
      );
      tenant.status = 'active';
      tenant.provisioningStatus = 'ready';
      tenant.provisioningError = undefined;
      tenant.updatedAt = now.toISOString();
      this.tenantConfigCache.set(tenant);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await repo.update(
        { id: tenant.id },
        {
          provisioningStatus: 'failed',
          provisioningError: message,
          updatedAt: new Date() as any,
        },
      );
      tenant.provisioningStatus = 'failed';
      tenant.provisioningError = message;
      this.tenantConfigCache.set(tenant);
      throw error;
    }
  }

  private deriveTenantDbUrl(superUrl: string, dbName: string): string {
    const tenantStorageStrategy = (
      process.env.TENANT_STORAGE_STRATEGY || 'database'
    ).toLowerCase();
    try {
      const url = new URL(superUrl);
      if (tenantStorageStrategy === 'schema') {
        // Keep the base database and route tenant queries into an isolated schema.
        // TypeORM/pg honors `options=-c search_path=<schema>`.
        const existingOptions = url.searchParams.get('options');
        const schemaOption = `-c search_path=${dbName}`;
        url.searchParams.set(
          'options',
          existingOptions ? `${existingOptions} ${schemaOption}` : schemaOption,
        );
      } else {
        // DB-per-tenant mode.
        url.pathname = `/${dbName}`;
      }
      return url.toString();
    } catch {
      // Fallback if SUPER_URL is not a valid URL (Postgres-only default).
      return `postgres://postgres:postgres@localhost:5432/${dbName}`;
    }
  }

  async resolveTenantBySboId(
    schoolBusinessOrganisationId: string,
  ): Promise<TenantConfig> {
    const cached = this.tenantConfigCache.getBySboId(
      schoolBusinessOrganisationId,
    );
    let tenant = cached || null;
    if (!tenant) {
      const ds = await this.controlDb.getDataSource();
      const repo = ds.getRepository(TenantConfigEntity);
      const row = await repo.findOne({
        where: { schoolBusinessOrganisationId },
      });
      if (row) {
        tenant = this.mapTenantRow(row);
      }
    }
    if (!tenant) {
      throw new NotFoundException(
        'Organization not found for provided schoolBusinessOrganisationId',
      );
    }
    if (tenant.status !== 'active') {
      throw new BadRequestException('Organization is not active');
    }
    this.tenantConfigCache.set(tenant);
    return tenant;
  }

  async createPrimaryOwnerUser(params: {
    schoolBusinessOrganisationId: string;
    email: string;
  }): Promise<{ tenant: TenantConfig; user: TenantUser }> {
    const tenant = await this.resolveTenantBySboId(
      params.schoolBusinessOrganisationId,
    );
    const stagedEmail = (
      tenant.ownerRegistrationPayloadJson?.email || ''
    ).toLowerCase();
    if (stagedEmail !== params.email.toLowerCase()) {
      throw new BadRequestException('Email does not match staged owner email');
    }
    if (!tenant.ownerEmailVerified) {
      throw new BadRequestException('Owner email is not verified');
    }
    if (!tenant.ownerStagedPasswordHash) {
      throw new BadRequestException(
        'No staged owner password found for tenant',
      );
    }

    // Ensure no existing primary mapping for this email in this tenant
    const ds = await this.controlDb.getDataSource();
    const mappingRepo = ds.getRepository(UserTenantMappingEntity);
    const existing = await mappingRepo.findOne({
      where: {
        tenantId: tenant.id,
        email: params.email.toLowerCase(),
        isActive: true,
      },
    });
    if (existing) {
      throw new ConflictException('User already mapped to this tenant');
    }

    const now = new Date().toISOString();
    const allCapabilities = ['*'];
    const allPermissions = ['*'];
    const user: TenantUser = {
      id: generateUserId(),
      tenantId: tenant.id,
      email: params.email.toLowerCase(),
      name: tenant.ownerRegistrationPayloadJson?.fullName || 'Primary Owner',
      passwordHash: tenant.ownerStagedPasswordHash,
      roles: [...ALL_ROLES],
      permissions: allPermissions,
      capabilities: allCapabilities,
      isPrimaryOwner: true,
      isActive: true,
      createdAt: now,
      updatedAt: now,
    };

    // Persist user-to-tenant mapping in control DB
    const previousMappings = await mappingRepo.find({
      where: { email: user.email, isPrimaryTenant: true, isActive: true },
    });
    for (const previousMapping of previousMappings) {
      if (previousMapping.tenantId !== tenant.id) {
        previousMapping.isActive = false;
        await mappingRepo.save(previousMapping);
      }
    }

    const mapping = mappingRepo.create({
      id: `map_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      userId: user.id,
      tenantId: tenant.id,
      email: user.email,
      isPrimaryTenant: true,
      isActive: true,
    });
    await mappingRepo.save(mapping);

    // Insert the primary owner into the tenant's users table
    const tenantDs = await this.tenantConnections.getOrCreate(
      tenant.id,
      tenant.dbUri,
    );
    await tenantDs.query(
      `ALTER TABLE users ADD COLUMN IF NOT EXISTS school_level varchar(32) NULL`,
    );
    await runDbQuery(
      tenantDs,
      `
      INSERT INTO users (id, email, name, phone, school_level, password_hash, roles, permissions, capabilities, is_active, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
      `,
      [
        user.id,
        user.email,
        user.name,
        tenant.ownerRegistrationPayloadJson?.phone || null,
        null,
        user.passwordHash,
        JSON.stringify(user.roles),
        JSON.stringify(user.permissions),
        JSON.stringify(user.capabilities),
        true,
      ],
    );

    // Clear staged password in control DB to prevent reuse
    const tenantRepo = ds.getRepository(TenantConfigEntity);
    await tenantRepo.update(
      { id: tenant.id },
      { ownerStagedPasswordHash: null, updatedAt: new Date() as any },
    );
    tenant.ownerStagedPasswordHash = undefined;
    tenant.updatedAt = now;
    this.tenantConfigCache.set(tenant);
    return { tenant, user };
  }

  async resolveTenantForUserEmail(
    email: string,
    schoolBusinessOrganisationId?: string,
  ): Promise<TenantConfig> {
    const ds = await this.controlDb.getDataSource();
    const mappingRepo = ds.getRepository(UserTenantMappingEntity);
    if (schoolBusinessOrganisationId) {
      const tenant = await this.resolveTenantBySboId(
        schoolBusinessOrganisationId,
      );
      const mapping = await mappingRepo.findOne({
        where: {
          tenantId: tenant.id,
          email: email.toLowerCase(),
          isActive: true,
        },
      });
      if (!mapping) {
        throw new NotFoundException('User is not mapped to provided tenant');
      }
      return tenant;
    }
    const mappings = await mappingRepo.find({
      where: {
        email: email.toLowerCase(),
        isPrimaryTenant: true,
        isActive: true,
      },
      order: { createdAt: 'DESC' },
    });
    if (mappings.length === 0) {
      throw new NotFoundException('No active tenant mapping found for user');
    }

    for (const mapping of mappings) {
      const tenant = await this.getTenantById(mapping.tenantId);
      if (
        tenant?.status === 'active' &&
        tenant.provisioningStatus === 'ready'
      ) {
        return tenant;
      }
    }

    throw new NotFoundException('No ready tenant configuration found for user');
  }

  async resolveTenantBySlug(slug: string): Promise<TenantConfig> {
    if (!isValidSchoolSlug(slug)) {
      throw new NotFoundException(
        'No ready tenant configuration found for school',
      );
    }
    const ds = await this.controlDb.getDataSource();
    const repo = ds.getRepository(TenantConfigEntity);
    const normalizedSlug = slug;
    const tenant = await repo.findOne({
      where: {
        slug: normalizedSlug,
        status: 'active',
        provisioningStatus: 'ready',
      },
    });
    if (tenant) {
      return this.mapTenantRow(tenant);
    }

    // Custom login slugs are also persisted in public_org_branding. This
    // fallback keeps login working when an organization name was changed
    // after the control-plane tenant slug was first created.
    try {
      const rows = await ds.query(
        `SELECT tenant_id FROM public_org_branding WHERE slug = $1 LIMIT 1`,
        [normalizedSlug],
      );
      const brandingTenantId = rows?.[0]?.tenant_id;
      if (brandingTenantId) {
        const brandedTenant = await this.getTenantById(brandingTenantId);
        if (
          brandedTenant?.status === 'active' &&
          brandedTenant.provisioningStatus === 'ready'
        ) {
          return brandedTenant;
        }
      }
    } catch {
      // The branding table is created lazily by the public branding endpoint.
    }
    throw new NotFoundException(
      'No ready tenant configuration found for school',
    );
  }

  async searchReadySchools(query: string): Promise<Array<{ name: string; slug: string }>> {
    const normalizedQuery = String(query || '').trim().replace(/\s+/g, ' ');
    if (normalizedQuery.length < 1 || normalizedQuery.length > 100) {
      return [];
    }

    const ds = await this.controlDb.getDataSource();
    try {
      const rows = await ds.query(
        `SELECT slug, payload
         FROM public_org_branding
         ORDER BY updated_at DESC
         LIMIT 500`,
      );
      const schools = rows
        .map((row: any) => {
          let payload: any;
          try {
            payload =
              typeof row.payload === 'string' ? JSON.parse(row.payload) : row.payload;
          } catch {
            return null;
          }
          const name = String(
            payload?.name ||
              payload?.schoolName ||
              payload?.organizationName ||
              '',
          ).trim();
          const slug = String(payload?.slug || row.slug || '').trim().toLowerCase();
          return name && isValidSchoolSlug(slug) ? { name, slug } : null;
        })
        .filter((school): school is { name: string; slug: string } => Boolean(school))
        .filter((school) => matchesSchoolSearch(school.name, normalizedQuery))
        .slice(0, 10);
      return schools;
    } catch {
      // The branding table is created lazily by organization settings.
      return [];
    }
  }

  async validateUserByTenant(
    tenantId: string,
    email: string,
    password: string,
  ): Promise<TenantUser> {
    const tenant = await this.getTenantById(tenantId);
    if (!tenant) throw new BadRequestException('Unknown tenant');
    const ds = await this.tenantConnections.getOrCreate(tenantId, tenant.dbUri);
    const rows: any[] = await runDbQuery(
      ds,
      'SELECT id, email, name, phone, school_level as schoolLevel, password_hash, roles, permissions, capabilities, is_active, created_at, updated_at FROM users WHERE email = ? LIMIT 1',
      [email.toLowerCase()],
    );
    const row = rows[0];
    if (!row || !(row.is_active ?? row.isActive)) {
      throw new BadRequestException('Invalid credentials');
    }
    const passwordHash =
      row.password_hash ?? row.passwordHash ?? row.passwordhash;
    if (!passwordHash) {
      throw new BadRequestException('Invalid credentials');
    }
    const ok = this.passwordService.verifyPassword(password, passwordHash);
    if (!ok) {
      throw new BadRequestException('Invalid credentials');
    }
    const user: TenantUser = {
      id: row.id,
      tenantId,
      email: row.email,
      name: row.name,
      phone: row.phone ?? null,
      schoolLevel: row.schoolLevel ?? row.school_level ?? null,
      passwordHash,
      roles: JSON.parse(row.roles || '[]'),
      permissions: JSON.parse(row.permissions || '[]'),
      capabilities: JSON.parse(row.capabilities || '[]'),
      isPrimaryOwner: false,
      isActive: !!(row.is_active ?? row.isActive),
      createdAt: new Date(row.created_at ?? row.createdAt).toISOString(),
      updatedAt: new Date(row.updated_at ?? row.updatedAt).toISOString(),
    };
    return user;
  }

  async findUserByEmail(
    tenantId: string,
    email: string,
  ): Promise<TenantUser | null> {
    const tenant = await this.getTenantById(tenantId);
    if (!tenant) return null;
    const ds = await this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
    const rows: any[] = await runDbQuery(
      ds,
      'SELECT id, email, name, phone, school_level as schoolLevel, roles, permissions, capabilities, is_active FROM users WHERE lower(email) = lower(?) LIMIT 1',
      [email.trim()],
    );
    const row = rows[0];
    if (!row || row.is_active === false) return null;
    return {
      id: row.id,
      tenantId,
      email: row.email,
      name: row.name,
      phone: row.phone ?? null,
      schoolLevel: row.schoolLevel ?? null,
      passwordHash: '',
      roles: this.parseJsonArray(row.roles),
      permissions: this.parseJsonArray(row.permissions),
      capabilities: this.parseJsonArray(row.capabilities),
      isPrimaryOwner: row.roles?.includes?.('director') ?? false,
      isActive: true,
      createdAt: '',
      updatedAt: '',
    };
  }

  async getUserById(
    tenantId: string,
    userId: string,
  ): Promise<TenantUser | null> {
    const tenant = await this.getTenantById(tenantId);
    if (!tenant) return null;
    const ds = await this.tenantConnections.getOrCreate(tenantId, tenant.dbUri);
    const rows: any[] = await runDbQuery(
      ds,
      'SELECT id, email, name, phone, school_level as schoolLevel, password_hash, roles, permissions, capabilities, is_active, created_at, updated_at FROM users WHERE id = ? LIMIT 1',
      [userId],
    );
    const row = rows[0];
    if (!row) return null;
    const passwordHash =
      row.password_hash ?? row.passwordHash ?? row.passwordhash;
    const user: TenantUser = {
      id: row.id,
      tenantId,
      email: row.email,
      name: row.name,
      phone: row.phone ?? null,
      schoolLevel: row.schoolLevel ?? row.school_level ?? null,
      passwordHash,
      roles: JSON.parse(row.roles || '[]'),
      permissions: JSON.parse(row.permissions || '[]'),
      capabilities: JSON.parse(row.capabilities || '[]'),
      isPrimaryOwner: false,
      isActive: !!(row.is_active ?? row.isActive),
      createdAt: new Date(row.created_at ?? row.createdAt).toISOString(),
      updatedAt: new Date(row.updated_at ?? row.updatedAt).toISOString(),
    };
    return user;
  }

  async getTenantById(tenantId: string): Promise<TenantConfig | null> {
    const cached = this.tenantConfigCache.getByTenantId(tenantId);
    if (cached) return cached;
    const ds = await this.controlDb.getDataSource();
    const repo = ds.getRepository(TenantConfigEntity);
    const row = await repo.findOne({ where: { id: tenantId } });
    if (!row) return null;
    const tenant = this.mapTenantRow(row);
    this.tenantConfigCache.set(tenant);
    return tenant;
  }

  private ensureUniqueSboId(): string {
    // Best-effort; DB unique constraint is the source of truth
    return generateSboId();
  }

  private ensureUniqueSlug(slugBase: string): string {
    // Best-effort; DB unique constraint is the source of truth
    return slugBase || 'organization';
  }

  private mapTenantRow(row: TenantConfigEntity): TenantConfig {
    return {
      id: row.id,
      slug: row.slug,
      schoolBusinessOrganisationId: row.schoolBusinessOrganisationId,
      name: row.name,
      status: row.status,
      dbName: row.dbName,
      dbUri: this.cryptoService.decrypt(row.dbUri),
      schoolLevels: row.schoolLevels ? JSON.parse(row.schoolLevels) : [],
      ownerRegistrationPayloadJson: row.ownerRegistrationPayloadJson
        ? JSON.parse(row.ownerRegistrationPayloadJson)
        : {},
      ownerStagedPasswordHash: row.ownerStagedPasswordHash || undefined,
      ownerEmailVerified: !!row.ownerEmailVerified,
      onboardingToken: row.onboardingToken || undefined,
      onboardingTokenExpiresAt: row.onboardingTokenExpiresAt
        ? row.onboardingTokenExpiresAt.toISOString()
        : undefined,
      provisioningStatus: row.provisioningStatus,
      provisioningError: row.provisioningError || undefined,
      createdAt: row.createdAt.toISOString(),
      updatedAt: row.updatedAt.toISOString(),
    };
  }

  private async ensurePasswordResetTable(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS password_reset_tokens (
        token varchar(128) PRIMARY KEY,
        tenant_id varchar(64) NOT NULL,
        user_id varchar(64) NOT NULL,
        email varchar(255) NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  /**
   * Create a password-reset token for the user's primary tenant mapping.
   * Always returns a generic success shape to avoid email enumeration.
   */
  async createPasswordResetToken(email: string, schoolSlug?: string): Promise<{
    created: boolean;
    token?: string;
    tenantId?: string;
    tenantSlug?: string;
    userId?: string;
  }> {
    const normalized = email.toLowerCase().trim();
    let tenant: TenantConfig;
    try {
      tenant = schoolSlug
        ? await this.resolveTenantBySlug(schoolSlug)
        : await this.resolveTenantForUserEmail(normalized);
    } catch {
      return { created: false };
    }

    const user = await this.validateUserExists(tenant.id, normalized);
    if (!user) return { created: false };

    const ds = await this.controlDb.getDataSource();
    await this.ensurePasswordResetTable(ds);
    const token = `pwr_${Date.now().toString(36)}_${Math.random()
      .toString(36)
      .slice(2, 14)}`;
    const expiresAt = new Date(Date.now() + 60 * 60_000); // 1 hour

    await ds.query(
      `INSERT INTO password_reset_tokens (token, tenant_id, user_id, email, expires_at, created_at)
       VALUES ($1, $2, $3, $4, $5, NOW())`,
      [token, tenant.id, user.id, normalized, expiresAt],
    );

    return {
      created: true,
      token,
      tenantId: tenant.id,
      tenantSlug: tenant.slug,
      userId: user.id,
    };
  }

  private async validateUserExists(
    tenantId: string,
    email: string,
  ): Promise<{ id: string } | null> {
    const tenant = await this.getTenantById(tenantId);
    if (!tenant) return null;
    const ds = await this.tenantConnections.getOrCreate(tenantId, tenant.dbUri);
    const rows: any[] = await runDbQuery(
      ds,
      'SELECT id FROM users WHERE email = ? AND is_active = true LIMIT 1',
      [email],
    );
    return rows[0] ? { id: rows[0].id } : null;
  }

  async resetPasswordWithToken(
    token: string,
    newPassword: string,
  ): Promise<void> {
    const ds = await this.controlDb.getDataSource();
    await this.ensurePasswordResetTable(ds);
    const rows: any[] = await ds.query(
      `SELECT token, tenant_id as "tenantId", user_id as "userId", expires_at as "expiresAt", used_at as "usedAt"
       FROM password_reset_tokens WHERE token = $1 LIMIT 1`,
      [token],
    );
    const row = rows[0];
    if (!row || row.usedAt) {
      throw new BadRequestException('Invalid or expired reset token');
    }
    if (new Date(row.expiresAt).getTime() < Date.now()) {
      throw new BadRequestException('Invalid or expired reset token');
    }

    const tenant = await this.getTenantById(row.tenantId);
    if (!tenant)
      throw new BadRequestException('Invalid or expired reset token');

    const tenantDs = await this.tenantConnections.getOrCreate(
      tenant.id,
      tenant.dbUri,
    );
    const hash = this.passwordService.hashPassword(newPassword);
    await runDbQuery(
      tenantDs,
      `UPDATE users SET password_hash = ?, updated_at = NOW() WHERE id = ?`,
      [hash, row.userId],
    );
    await ds.query(
      `UPDATE password_reset_tokens SET used_at = NOW() WHERE token = $1`,
      [token],
    );
  }

  private parseJsonArray(value: unknown): string[] {
    if (Array.isArray(value)) return value.map(String);
    if (typeof value !== 'string') return [];
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  }
}
