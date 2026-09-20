import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { ControlDbService } from '../database/control-db.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { ControlApiClient } from '../platform-config/control-api.client';
import type {
  AttendanceConfigDto,
  LoginPageConfigDto,
} from './dto/organization.dto';
import { isValidSchoolSlug } from '../common/utils/id.util';
import { ListCacheService } from '../common/cache/list-cache.service';

export type CreateOrganizationInput = {
  name: string;
  email?: string;
  phone?: string;
  address?: string;
  country?: string;
  state?: string;
  city?: string;
  schoolLevels?: string[];
  motto?: string | null;
  website?: string | null;
  logo?: string | null;
  brandColor?: string | null;
  loginPageConfig?: LoginPageConfigDto | null;
  attendanceConfig?: AttendanceConfigDto | null;
};

function toOrgSlug(name: string): string {
  return (
    name
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'school'
  );
}

@Injectable()
export class OrganizationsService {
  private readonly orgSchemaReady = new WeakSet<object>();
  private readonly orgSchemaSetup = new WeakMap<object, Promise<void>>();

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly controlDb: ControlDbService,
    private readonly controlApi: ControlApiClient,
    private readonly listCache: ListCacheService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    return this.tenantConnections.getOrCreate(tenant!.id, tenant!.dbUri);
  }

  private async ensurePublicBrandingTable() {
    const ds = await this.controlDb.getDataSource();
    await ds.query(`
      CREATE TABLE IF NOT EXISTS public_org_branding (
        slug varchar(128) PRIMARY KEY,
        tenant_id varchar(64) NOT NULL,
        org_id varchar(64) NOT NULL,
        payload TEXT NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  private parseAttendanceConfig(raw: any): AttendanceConfigDto | null {
    if (!raw) return null;
    try {
      const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (!parsed || typeof parsed !== 'object') return null;
      const locations = Array.isArray(parsed.locations)
        ? parsed.locations
            .map((loc: any) => ({
              id: String(loc?.id || '').trim(),
              name: String(loc?.name || '').trim(),
              latitude: Number(loc?.latitude),
              longitude: Number(loc?.longitude),
              radiusMeters: Number(
                loc?.radiusMeters ?? loc?.radius_meters ?? 100,
              ),
            }))
            .filter(
              (loc: any) =>
                loc.id &&
                loc.name &&
                Number.isFinite(loc.latitude) &&
                Number.isFinite(loc.longitude) &&
                Number.isFinite(loc.radiusMeters) &&
                loc.radiusMeters > 0,
            )
        : [];
      return {
        requireLocationVerification:
          parsed.requireLocationVerification === true,
        locations,
        sessionStartTime:
          parsed.sessionStartTime || parsed.session_start_time || undefined,
        sessionEndTime:
          parsed.sessionEndTime || parsed.session_end_time || undefined,
        biometricWindowStart:
          parsed.biometricWindowStart ||
          parsed.biometric_window_start ||
          undefined,
        biometricWindowEnd:
          parsed.biometricWindowEnd || parsed.biometric_window_end || undefined,
        businessDays: Array.isArray(parsed.businessDays)
          ? parsed.businessDays
              .map((d: any) => Number(d))
              .filter((d: number) => d >= 0 && d <= 6)
          : undefined,
        manualVerificationCode: parsed.manualVerificationCode || null,
        manualCodeCreatedAt: parsed.manualCodeCreatedAt || null,
        verificationRequiredRoles: Array.isArray(
          parsed.verificationRequiredRoles,
        )
          ? parsed.verificationRequiredRoles
              .map((r: any) =>
                String(r)
                  .trim()
                  .toLowerCase()
                  .replace(/[\s-]+/g, '_'),
              )
              .filter(Boolean)
          : undefined,
        lastStaffFinalizeDate: parsed.lastStaffFinalizeDate || null,
      };
    } catch {
      return null;
    }
  }

  async getAttendanceConfig(tenantId: string): Promise<AttendanceConfigDto> {
    const org = await this.getCurrentOrganization(tenantId);
    const parsed = this.parseAttendanceConfig(
      org?.attendanceConfig ?? (org as any)?.attendance_config,
    );
    return parsed || { requireLocationVerification: false, locations: [] };
  }

  private parseLoginPageConfig(raw: any): LoginPageConfigDto | null {
    if (!raw) return null;
    try {
      const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (!parsed || typeof parsed !== 'object') return null;
      return {
        enabled: !!parsed.enabled,
        welcomeMessage: parsed.welcomeMessage || 'Welcome Back',
        subtitle: parsed.subtitle || 'Sign in to access your school portal',
        bgType: parsed.bgType === 'solid' ? 'solid' : 'gradient',
        bgColor: parsed.bgColor || '#e0f2fe',
        bgGradientTo: parsed.bgGradientTo || '#bfdbfe',
        showMotto: parsed.showMotto !== false,
        footerText: parsed.footerText || '',
      };
    } catch {
      return null;
    }
  }

  private async syncPublicBranding(params: { tenantId: string; org: any }) {
    await this.ensurePublicBrandingTable();
    const slug = toOrgSlug(params.org.name || 'school');
    const payload = JSON.stringify({
      slug,
      name: params.org.name,
      logo: params.org.logo || null,
      motto: params.org.motto || '',
      brandColor: params.org.brandColor || null,
      loginPageConfig: params.org.loginPageConfig || null,
    });
    const ds = await this.controlDb.getDataSource();
    // Remove any previous slug rows for this org (name/slug may have changed).
    await ds.query(`DELETE FROM public_org_branding WHERE org_id = $1`, [
      params.org.id,
    ]);
    await ds.query(
      `INSERT INTO public_org_branding (slug, tenant_id, org_id, payload, updated_at)
       VALUES ($1, $2, $3, $4, NOW())
       ON CONFLICT (slug) DO UPDATE SET
         tenant_id = EXCLUDED.tenant_id,
         org_id = EXCLUDED.org_id,
         payload = EXCLUDED.payload,
         updated_at = NOW()`,
      [slug, params.tenantId, params.org.id, payload],
    );
    return slug;
  }

  private async ensureOrgTables(ds: any): Promise<void> {
    if (this.orgSchemaReady.has(ds)) return;
    const existing = this.orgSchemaSetup.get(ds);
    if (existing) return existing;

    const setup = this.ensureOrgTablesInternal(ds);
    this.orgSchemaSetup.set(ds, setup);
    try {
      await setup;
      this.orgSchemaReady.add(ds);
    } finally {
      this.orgSchemaSetup.delete(ds);
    }
  }

  private async ensureOrgTablesInternal(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS business_org (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        email varchar(255),
        phone varchar(64),
        address varchar(255),
        country varchar(128),
        state varchar(128),
        city varchar(128),
        school_levels TEXT NULL,
        created_by varchar(64) NOT NULL,
        updated_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    try {
      await ds.query(`
        ALTER TABLE business_org
          ADD COLUMN IF NOT EXISTS email varchar(255) NULL,
          ADD COLUMN IF NOT EXISTS phone varchar(64) NULL,
          ADD COLUMN IF NOT EXISTS address varchar(255) NULL,
          ADD COLUMN IF NOT EXISTS country varchar(128) NULL,
          ADD COLUMN IF NOT EXISTS state varchar(128) NULL,
          ADD COLUMN IF NOT EXISTS city varchar(128) NULL,
          ADD COLUMN IF NOT EXISTS school_levels TEXT NULL,
          ADD COLUMN IF NOT EXISTS created_by varchar(64) NULL,
          ADD COLUMN IF NOT EXISTS updated_by varchar(64) NULL,
          ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ADD COLUMN IF NOT EXISTS motto varchar(255) NULL,
          ADD COLUMN IF NOT EXISTS website varchar(512) NULL,
          ADD COLUMN IF NOT EXISTS logo TEXT NULL,
          ADD COLUMN IF NOT EXISTS brand_color varchar(32) NULL,
          ADD COLUMN IF NOT EXISTS login_page_config TEXT NULL,
          ADD COLUMN IF NOT EXISTS attendance_config TEXT NULL
      `);
    } catch {
      // Existing deployments may already have the columns.
    }
    await ds.query(`
      CREATE TABLE IF NOT EXISTS org_subscription (
        id varchar(64) PRIMARY KEY,
        plan_id varchar(64) NOT NULL,
        billing_cycle varchar(16) NOT NULL,
        status varchar(32) NOT NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        next_billing_at TIMESTAMP NULL,
        created_by varchar(64) NULL,
        updated_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    try {
      await ds.query(`
        ALTER TABLE org_subscription
          ADD COLUMN IF NOT EXISTS plan_id varchar(64) NULL,
          ADD COLUMN IF NOT EXISTS billing_cycle varchar(16) NOT NULL DEFAULT 'monthly',
          ADD COLUMN IF NOT EXISTS status varchar(32) NOT NULL DEFAULT 'active',
          ADD COLUMN IF NOT EXISTS amount decimal(15,2) NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS next_billing_at TIMESTAMP NULL,
          ADD COLUMN IF NOT EXISTS created_by varchar(64) NULL,
          ADD COLUMN IF NOT EXISTS updated_by varchar(64) NULL,
          ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      `);
    } catch {
      // Existing deployments may already have the columns.
    }
    await ds.query(`
      CREATE TABLE IF NOT EXISTS org_invoices (
        id varchar(64) PRIMARY KEY,
        invoice_no varchar(64) NOT NULL,
        period varchar(128) NULL,
        plan_id varchar(64) NOT NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        status varchar(32) NOT NULL,
        due_date TIMESTAMP NULL,
        paid_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    try {
      await ds.query(`
        ALTER TABLE org_invoices
          ADD COLUMN IF NOT EXISTS invoice_no varchar(64) NULL,
          ADD COLUMN IF NOT EXISTS period varchar(128) NULL,
          ADD COLUMN IF NOT EXISTS plan_id varchar(64) NULL,
          ADD COLUMN IF NOT EXISTS amount decimal(15,2) NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS status varchar(32) NOT NULL DEFAULT 'pending',
          ADD COLUMN IF NOT EXISTS due_date TIMESTAMP NULL,
          ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP NULL,
          ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      `);
    } catch {
      // Existing deployments may already have the columns.
    }
  }

  private assertLogo(value: string | null | undefined) {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value !== 'string') {
      throw new Error('Invalid school logo');
    }
    if (value.length > 400_000) {
      throw new Error(
        'School logo is too large. Use a square PNG/JPEG under ~300KB.',
      );
    }
    if (!/^data:image\/(jpeg|jpg|png|webp|svg\+xml);base64,/i.test(value)) {
      throw new Error('School logo must be a JPEG, PNG, WebP, or SVG data URL');
    }
    return value;
  }

  private mapOrganizationRow(row: any) {
    let schoolLevels: string[] = [];
    const rawLevels =
      row?.schoolLevels ?? row?.schoollevels ?? row?.school_levels;
    try {
      if (rawLevels && typeof rawLevels === 'string') {
        schoolLevels = JSON.parse(rawLevels);
      } else if (Array.isArray(rawLevels)) {
        schoolLevels = rawLevels;
      }
    } catch {
      schoolLevels = [];
    }
    return {
      id: row.id,
      name: row.name,
      email: row.email,
      phone: row.phone,
      address: row.address,
      country: row.country,
      state: row.state,
      city: row.city,
      schoolLevels,
      motto: row.motto ?? null,
      website: row.website ?? null,
      logo: row.logo ?? null,
      brandColor: row.brandColor ?? row.brand_color ?? row.brandcolor ?? null,
      loginPageConfig: this.parseLoginPageConfig(
        row.loginPageConfig ?? row.login_page_config ?? row.loginpageconfig,
      ),
      attendanceConfig: this.parseAttendanceConfig(
        row.attendanceConfig ?? row.attendance_config ?? row.attendanceconfig,
      ),
      slug: toOrgSlug(row.name || 'school'),
      createdBy: row.createdBy ?? row.createdby,
      updatedBy: row.updatedBy ?? row.updatedby,
      createdAt: row.createdAt ?? row.createdat,
      updatedAt: row.updatedAt ?? row.updatedat,
    };
  }

  async createBusinessOrganization(params: {
    tenantId: string;
    userId: string;
    body: CreateOrganizationInput;
  }) {
    const { tenantId, userId, body } = params;
    const ds = await this.getTenantDs(tenantId);

    await this.ensureOrgTables(ds);

    const orgId = randomToken('org');
    await runDbQuery(
      ds,
      `
      INSERT INTO business_org (id, name, email, phone, address, country, state, city, school_levels, created_by, updated_by, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW());
      `,
      [
        orgId,
        body.name,
        body.email || null,
        body.phone || null,
        body.address || null,
        body.country || null,
        body.state || null,
        body.city || null,
        JSON.stringify(body.schoolLevels || []),
        userId,
        userId,
      ],
    );

    return {
      id: orgId,
      name: body.name,
      createdBy: userId,
    };
  }

  async listBusinessOrganizations(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);
    const rows = await ds.query(
      `SELECT 
         id, 
         name, 
         email, 
         phone, 
         address, 
         country, 
         state, 
         city, 
         school_levels as schoolLevels,
         motto,
         website,
         logo,
         brand_color as brandColor,
         login_page_config as loginPageConfig,
         attendance_config as attendanceConfig,
         created_by as createdBy, 
         updated_by as updatedBy, 
         created_at as createdAt, 
         updated_at as updatedAt 
       FROM business_org 
       ORDER BY created_at DESC`,
    );
    return rows.map((r: any) => this.mapOrganizationRow(r));
  }

  async getBusinessOrganizationById(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);
    const rows = await runDbQuery(
      ds,
      `SELECT 
         id, 
         name, 
         email, 
         phone, 
         address, 
         country, 
         state, 
         city, 
         school_levels as schoolLevels,
         motto,
         website,
         logo,
         brand_color as brandColor,
         login_page_config as loginPageConfig,
         attendance_config as attendanceConfig,
         created_by as createdBy, 
         updated_by as updatedBy, 
         created_at as createdAt, 
         updated_at as updatedAt 
       FROM business_org 
       WHERE id = ? 
       LIMIT 1`,
      [id],
    );
    const row = rows[0] || null;
    return row ? this.mapOrganizationRow(row) : null;
  }

  async updateBusinessOrganization(params: {
    tenantId: string;
    userId: string;
    id: string;
    body: Partial<CreateOrganizationInput>;
  }) {
    const { tenantId, userId, id, body } = params;
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);
    const updates: string[] = [];
    const values: any[] = [];
    const upsert = (col: string, val: any) => {
      if (val !== undefined) {
        updates.push(`${col} = ?`);
        values.push(val);
      }
    };
    upsert('name', body.name);
    upsert('email', body.email);
    upsert('phone', body.phone);
    upsert('address', body.address);
    upsert('country', body.country);
    upsert('state', body.state);
    upsert('city', body.city);
    upsert('motto', body.motto);
    upsert('website', body.website);
    upsert('brand_color', body.brandColor);
    if (body.logo !== undefined) {
      try {
        upsert('logo', this.assertLogo(body.logo));
      } catch (err) {
        throw new BadRequestException(
          err instanceof Error ? err.message : 'Invalid school logo',
        );
      }
    }
    if (body.schoolLevels !== undefined) {
      updates.push('school_levels = ?');
      values.push(JSON.stringify(body.schoolLevels || []));
    }
    if (body.loginPageConfig !== undefined) {
      updates.push('login_page_config = ?');
      values.push(
        body.loginPageConfig === null
          ? null
          : JSON.stringify(body.loginPageConfig),
      );
    }
    if (body.attendanceConfig !== undefined) {
      const currentOrg = await this.getBusinessOrganizationById(tenantId, id);
      const existingConfig = this.parseAttendanceConfig(
        currentOrg?.attendanceConfig,
      );
      const incoming =
        body.attendanceConfig === null
          ? {}
          : (body.attendanceConfig as Record<string, unknown>);
      const merged = {
        ...(existingConfig || {
          requireLocationVerification: false,
          locations: [],
        }),
        ...incoming,
      };
      // Preserve server-managed manual code unless admin explicitly regenerates via attendance API.
      if (
        existingConfig?.manualVerificationCode &&
        incoming.manualVerificationCode === undefined
      ) {
        merged.manualVerificationCode = existingConfig.manualVerificationCode;
        merged.manualCodeCreatedAt = existingConfig.manualCodeCreatedAt;
      }
      if (
        existingConfig?.lastStaffFinalizeDate &&
        incoming.lastStaffFinalizeDate === undefined
      ) {
        merged.lastStaffFinalizeDate = existingConfig.lastStaffFinalizeDate;
      }
      updates.push('attendance_config = ?');
      values.push(JSON.stringify(merged));
    }
    if (updates.length === 0) {
      return this.getBusinessOrganizationById(tenantId, id);
    }
    updates.push('updated_by = ?');
    values.push(userId);
    const sql = `UPDATE business_org SET ${updates.join(
      ', ',
    )}, updated_at = NOW() WHERE id = ?`;
    values.push(id);
    await runDbQuery(ds, sql, values);
    const updated = await this.getBusinessOrganizationById(tenantId, id);
    if (updated) {
      await this.syncPublicBranding({ tenantId, org: updated });
    }
    return updated;
  }

  /**
   * Public (unauthenticated) branding + login-page config for /{slug}/login.
   */
  async getPublicLoginBrandingBySlug(slug: string) {
    if (!isValidSchoolSlug(slug)) {
      throw new NotFoundException(
        'Custom login page not found for this school',
      );
    }
    const cached = await this.listCache.getOrLoad(
      `public-org-branding:${slug}`,
      async () => {
        try {
          return await this.loadPublicLoginBrandingBySlug(slug);
        } catch (error) {
          if (error instanceof NotFoundException) {
            return { __notFound: true as const };
          }
          throw error;
        }
      },
      false,
      300_000,
    );
    if (
      cached &&
      typeof cached === 'object' &&
      '__notFound' in (cached as object)
    ) {
      throw new NotFoundException(
        'Custom login page not found for this school',
      );
    }
    return cached;
  }

  private async loadPublicLoginBrandingBySlug(slug: string) {
    await this.ensurePublicBrandingTable();
    const normalized = slug;
    const ds = await this.controlDb.getDataSource();
    const rows = await ds.query(
      `SELECT payload FROM public_org_branding WHERE slug = $1 LIMIT 1`,
      [normalized],
    );
    if (!rows?.[0]?.payload) {
      throw new NotFoundException(
        'Custom login page not found for this school',
      );
    }
    let payload: any;
    try {
      payload =
        typeof rows[0].payload === 'string'
          ? JSON.parse(rows[0].payload)
          : rows[0].payload;
    } catch {
      throw new NotFoundException(
        'Custom login page not found for this school',
      );
    }
    const loginPageConfig = this.parseLoginPageConfig(payload.loginPageConfig);
    if (!loginPageConfig?.enabled) {
      throw new NotFoundException(
        'Custom login page is not enabled for this school',
      );
    }
    return {
      slug: normalized,
      name: payload.name,
      logo: payload.logo || null,
      motto: payload.motto || '',
      brandColor: payload.brandColor || '#4f46e5',
      loginPageConfig,
    };
  }

  async deleteBusinessOrganization(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);
    await runDbQuery(ds, `DELETE FROM business_org WHERE id = ?`, [id]);
    return { id };
  }

  async getCurrentOrganization(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);
    const rows = await ds.query(`
      SELECT
        id, name, email, phone, address, country, state, city,
        school_levels as "schoolLevels",
        motto, website, logo,
        brand_color as "brandColor",
        login_page_config as "loginPageConfig",
        attendance_config as "attendanceConfig",
        created_by as "createdBy",
        updated_by as "updatedBy",
        created_at as "createdAt",
        updated_at as "updatedAt"
      FROM business_org
      ORDER BY created_at DESC
      LIMIT 1
    `);
    return rows[0] ? this.mapOrganizationRow(rows[0]) : null;
  }

  /**
   * Live dashboard metrics for the organization overview page.
   * Aggregated server-side from tenant tables (no client-side demo numbers).
   */
  async getOrganizationOverview(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);

    const [org, subscription] = await Promise.all([
      this.getCurrentOrganization(tenantId),
      this.getCurrentSubscription(tenantId),
    ]);

    const countRole = async (role: string) => {
      try {
        const rows: any[] = await runDbQuery(
          ds,
          `SELECT COUNT(*)::int as count FROM users WHERE roles LIKE ? AND is_active = true`,
          [`%"${role}"%`],
        );
        return Number(rows[0]?.count || 0);
      } catch {
        return 0;
      }
    };

    const [studentCount, parentCount] = await Promise.all([
      countRole('student'),
      countRole('parent'),
    ]);
    const staffRoles = [
      'director',
      'school_admin',
      'it_admin',
      'head_teacher',
      'principal',
      'assistant_head_teacher',
      'bursar',
      'class_teacher',
      'subject_teacher',
      'administrative_staff',
    ];
    let staffCount = 0;
    try {
      const staffPredicates = staffRoles.map(() => `roles LIKE ?`).join(' OR ');
      const staffRows: any[] = await runDbQuery(
        ds,
        `SELECT COUNT(*)::int as count
         FROM users
         WHERE is_active = true AND (${staffPredicates})`,
        staffRoles.map((role) => `%"${role}"%`),
      );
      staffCount = Number(staffRows[0]?.count || 0);
    } catch {
      staffCount = 0;
    }

    let classCount = 0;
    let enrollmentByLevel: Array<{ level: string; students: number }> = [];
    try {
      const [classRows, enrollmentRows] = await Promise.all([
        runDbQuery(
          ds,
          `SELECT COUNT(*)::int as count FROM academic_classes WHERE is_active = true`,
        ),
        runDbQuery(
          ds,
          `SELECT COALESCE(c.school_level, 'unknown') as level, COUNT(e.student_id)::int as students
           FROM academic_classes c
           LEFT JOIN student_class_enrollments e ON e.class_id = c.id
           WHERE c.is_active = true
           GROUP BY COALESCE(c.school_level, 'unknown')
           ORDER BY level`,
        ),
      ]);
      classCount = Number((classRows as any[])[0]?.count || 0);
      enrollmentByLevel = enrollmentRows;
    } catch {
      // tables may not exist yet on fresh tenants
    }

    let financial = {
      totalCollected: 0,
      paymentCount: 0,
      feeStructureCount: 0,
    };
    try {
      const [payRows, feeRows] = await Promise.all([
        runDbQuery(
          ds,
          `SELECT COALESCE(SUM(amount), 0)::float as total, COUNT(*)::int as count
           FROM financial_payments WHERE status = 'completed'`,
        ),
        runDbQuery(
          ds,
          `SELECT COUNT(*)::int as count FROM financial_fee_structures`,
        ),
      ]);
      financial = {
        totalCollected: Number((payRows as any[])[0]?.total || 0),
        paymentCount: Number((payRows as any[])[0]?.count || 0),
        feeStructureCount: Number((feeRows as any[])[0]?.count || 0),
      };
    } catch {
      // ignore
    }

    const attendanceToday = { present: 0, absent: 0, late: 0, total: 0 };
    try {
      const today = new Date().toISOString().split('T')[0];
      const attRows: any[] = await runDbQuery(
        ds,
        `SELECT status, COUNT(*)::int as count FROM attendance_students WHERE date = ? GROUP BY status`,
        [today],
      );
      for (const row of attRows) {
        const n = Number(row.count || 0);
        attendanceToday.total += n;
        if (row.status === 'present') attendanceToday.present = n;
        if (row.status === 'absent') attendanceToday.absent = n;
        if (row.status === 'late') attendanceToday.late = n;
      }
    } catch {
      // ignore
    }

    return {
      organization: org,
      subscription,
      counts: {
        students: studentCount,
        parents: parentCount,
        staff: staffCount,
        classes: classCount,
      },
      enrollmentByLevel,
      financial,
      attendanceToday,
    };
  }

  async listCatalogPlans() {
    if (this.controlApi.isConfigured()) {
      const plans = await this.controlApi.getCatalogPlans();
      return plans.map((plan) => ({
        id: plan.key,
        controlId: plan.id,
        name: plan.name,
        description: plan.description,
        monthlyPrice: Number(plan.monthly_price_minor || 0) / 100,
        yearlyPrice: (Number(plan.monthly_price_minor || 0) / 100) * 12,
        currency: plan.currency,
        rank: plan.rank,
        entitlements: plan.entitlements.map((entitlement) => ({
          ...entitlement,
          name: entitlement.name || entitlement.feature_key,
        })),
      }));
    }
    return [
      {
        id: 'starter',
        controlId: null,
        name: 'Starter',
        description: 'Starter plan',
        monthlyPrice: 25000,
        yearlyPrice: 250000,
        currency: 'NGN',
        rank: 1,
        entitlements: [],
      },
      {
        id: 'professional',
        controlId: null,
        name: 'Professional',
        description: 'Professional plan',
        monthlyPrice: 75000,
        yearlyPrice: 750000,
        currency: 'NGN',
        rank: 2,
        entitlements: [],
      },
      {
        id: 'enterprise',
        controlId: null,
        name: 'Enterprise',
        description: 'Enterprise plan',
        monthlyPrice: 150000,
        yearlyPrice: 1500000,
        currency: 'NGN',
        rank: 3,
        entitlements: [],
      },
    ];
  }

  private async reconcileControlTenant(tenantId: string): Promise<void> {
    const organization = await this.getCurrentOrganization(tenantId);
    await this.controlApi.registerTenant(tenantId, {
      slug: organization?.slug || toOrgSlug(organization?.name || tenantId),
      name: organization?.name || tenantId,
      adminEmail: organization?.email || undefined,
      adminPhone: organization?.phone || undefined,
    });
  }

  async getCurrentSubscription(tenantId: string) {
    if (this.controlApi.isConfigured()) {
      let subscription: any;
      try {
        subscription = await this.controlApi.getTenantSubscription(tenantId);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!/Tenant not registered|subscription 404/i.test(message))
          throw error;
        await this.reconcileControlTenant(tenantId);
        subscription = await this.controlApi.getTenantSubscription(tenantId);
      }
      const plans = await this.listCatalogPlans();
      const plan = plans.find(
        (item) => item.controlId === Number(subscription.plan_id),
      );
      return {
        id: subscription.id,
        type: subscription.type,
        planId: plan?.id || null,
        dealName: subscription.deal_name || null,
        billingCycle: subscription.billing_cycle,
        status: subscription.status,
        amount: Number(subscription.price_minor || 0) / 100,
        nextBillingAt: subscription.next_invoice_at || null,
      };
    }
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);
    const rows = await ds.query(
      `SELECT id, plan_id as planId, billing_cycle as billingCycle, status, amount, next_billing_at as nextBillingAt, created_at as createdAt, updated_at as updatedAt
       FROM org_subscription ORDER BY updated_at DESC LIMIT 1`,
    );
    if (rows[0]) return rows[0];
    return {
      id: null,
      planId: 'professional',
      billingCycle: 'yearly',
      status: 'active',
      amount: 750000,
      nextBillingAt: null,
    };
  }

  async changeSubscription(params: {
    tenantId: string;
    userId: string;
    planId: string;
    billingCycle: 'monthly' | 'yearly';
  }) {
    const { tenantId, userId, planId, billingCycle } = params;
    if (this.controlApi.isConfigured()) {
      let subscription: any;
      try {
        subscription = await this.controlApi.changeTenantSubscription(
          tenantId,
          {
            planKey: planId,
            billingCycle,
          },
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!/Tenant not registered|subscription change 404/i.test(message))
          throw error;
        await this.reconcileControlTenant(tenantId);
        subscription = await this.controlApi.changeTenantSubscription(
          tenantId,
          {
            planKey: planId,
            billingCycle,
          },
        );
      }
      const plans = await this.listCatalogPlans();
      const plan = plans.find((item) => item.id === planId);
      return {
        id: subscription.id,
        planId,
        billingCycle: subscription.billing_cycle,
        status: subscription.status,
        amount: Number(subscription.price_minor || 0) / 100,
        nextBillingAt: subscription.next_invoice_at || null,
        planName: plan?.name || planId,
        pendingInvoiceId: subscription.pending_invoice_id || null,
      };
    }
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);
    const amountMap: Record<string, { monthly: number; yearly: number }> = {
      starter: { monthly: 25000, yearly: 250000 },
      professional: { monthly: 75000, yearly: 750000 },
      enterprise: { monthly: 150000, yearly: 1500000 },
    };
    const plan = amountMap[planId];
    if (!plan) {
      throw new BadRequestException('Unknown subscription plan');
    }
    const amount = plan[billingCycle];
    if (!Number.isSafeInteger(amount) || amount <= 0) {
      throw new BadRequestException('Subscription price is not configured');
    }
    await runDbQuery(
      ds,
      `UPDATE org_subscription SET status = 'replaced', updated_at = NOW() WHERE status IN ('active', 'pending_payment')`,
    );
    const subId = randomToken('sub');
    await runDbQuery(
      ds,
      `INSERT INTO org_subscription (id, plan_id, billing_cycle, status, amount, created_by, updated_by, created_at, updated_at)
       VALUES (?, ?, ?, 'pending_payment', ?, ?, ?, NOW(), NOW())`,
      [subId, planId, billingCycle, amount, userId, userId],
    );
    const invId = randomToken('inv');
    await runDbQuery(
      ds,
      `INSERT INTO org_invoices (id, invoice_no, period, plan_id, amount, status, due_date, created_at)
       VALUES (?, ?, ?, ?, ?, 'pending', NOW() + INTERVAL '30 days', NOW())`,
      [
        invId,
        `INV-${Date.now()}`,
        `${new Date().getFullYear()}`,
        planId,
        amount,
      ],
    );
    const current = await this.getCurrentSubscription(tenantId);
    return { ...current, pendingInvoiceId: invId };
  }

  async listInvoices(tenantId: string) {
    if (this.controlApi.isConfigured()) {
      return this.controlApi.getTenantInvoices(tenantId);
    }
    const ds = await this.getTenantDs(tenantId);
    await this.ensureOrgTables(ds);
    const rows = await ds.query(
      `SELECT id, invoice_no as invoiceNo, period, plan_id as planId, amount, status, due_date as dueDate, paid_at as paidAt, created_at as createdAt
       FROM org_invoices ORDER BY created_at DESC LIMIT 200`,
    );
    return rows;
  }

  async downloadInvoicePdf(tenantId: string, invoiceId: number) {
    if (!this.controlApi.isConfigured()) {
      throw new NotFoundException('Subscription invoice PDF unavailable');
    }
    return this.controlApi.getTenantInvoicePdf(tenantId, invoiceId);
  }
}
