import {
  Injectable,
  NotFoundException,
  BadRequestException,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { BiometricService } from '../biometric/biometric.service';
import { NotificationsService } from '../notifications/notifications.service';
import { OrganizationsService } from '../organizations/organizations.service';
import type { AttendanceConfigDto } from '../organizations/dto/organization.dto';
import { matchVerificationLocation } from '../common/utils/geo.util';
import {
  assertClassTeacherMayAccess,
  assertTeacherMayMarkClass,
} from '../common/auth/teacher-scope.util';
import {
  generateManualVerificationCode,
  isVerificationWindowOpen,
  isBusinessDay,
  isSessionEnded,
  resolveSessionConfig,
  resolveVerificationRequiredRoles,
  schoolIsoDate,
  userRequiresDailyVerification,
} from '../common/utils/school-session.util';
import { ListCacheService } from '../common/cache/list-cache.service';

/** Postgres often returns unquoted aliases lowercased (`signintime`). */
function rowField(row: Record<string, any>, ...keys: string[]): any {
  for (const key of keys) {
    if (row[key] !== undefined && row[key] !== null) return row[key];
    const lower = key.toLowerCase();
    for (const actual of Object.keys(row)) {
      if (
        actual.toLowerCase() === lower &&
        row[actual] !== undefined &&
        row[actual] !== null
      ) {
        return row[actual];
      }
    }
  }
  return undefined;
}

@Injectable()
export class AttendanceService {
  /** Avoid repeating DDL on every attendance read — schema is ensured on writes. */
  private readonly attendanceSchemaReady = new Set<string>();

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly biometricService: BiometricService,
    private readonly notificationsService: NotificationsService,
    private readonly organizationsService: OrganizationsService,
    private readonly listCache: ListCacheService,
  ) {}

  private verificationStatusCacheKey(
    tenantId: string,
    userId: string,
    date: string,
  ) {
    return `tenant:${tenantId}:attendance:verification-status:${userId}:${date}`;
  }

  private invalidateVerificationStatusCache(
    tenantId: string,
    staffId: string,
    date: string,
  ) {
    this.listCache.invalidate(
      this.verificationStatusCacheKey(tenantId, staffId, date.slice(0, 10)),
    );
  }

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureTablesIfNeeded(tenantId: string, ds: any) {
    if (this.attendanceSchemaReady.has(tenantId)) return;
    await this.ensureTables(ds);
    this.attendanceSchemaReady.add(tenantId);
  }

  private async ensureTables(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS attendance_students (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        class_id varchar(64) NULL,
        date varchar(16) NOT NULL,
        status varchar(16) NOT NULL,
        marked_by varchar(64) NULL,
        corrected_by varchar(64) NULL,
        correction_reason text NULL,
        corrected_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(
      `ALTER TABLE attendance_students ADD COLUMN IF NOT EXISTS corrected_by varchar(64) NULL`,
    );
    await ds.query(
      `ALTER TABLE attendance_students ADD COLUMN IF NOT EXISTS correction_reason text NULL`,
    );
    await ds.query(
      `ALTER TABLE attendance_students ADD COLUMN IF NOT EXISTS corrected_at TIMESTAMP NULL`,
    );
    await ds.query(`
      CREATE TABLE IF NOT EXISTS attendance_staff (
        id varchar(64) PRIMARY KEY,
        staff_id varchar(64) NOT NULL,
        date varchar(16) NOT NULL,
        status varchar(16) NOT NULL,
        sign_in_time varchar(32) NULL,
        location varchar(64) NULL,
        biometric_verified boolean NOT NULL DEFAULT false,
        marked_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    // Existing tenants may have the original attendance_staff schema
    // (check_in_at/method) from the first attendance migration. Keep those
    // databases compatible with the current verification flow.
    await ds.query(
      `ALTER TABLE attendance_staff ADD COLUMN IF NOT EXISTS sign_in_time varchar(32) NULL`,
    );
    await ds.query(
      `ALTER TABLE attendance_staff ADD COLUMN IF NOT EXISTS location varchar(64) NULL`,
    );
    await ds.query(
      `ALTER TABLE attendance_staff ADD COLUMN IF NOT EXISTS biometric_verified boolean NOT NULL DEFAULT false`,
    );
    await ds.query(
      `ALTER TABLE attendance_staff ADD COLUMN IF NOT EXISTS marked_by varchar(64) NULL`,
    );
  }

  async listStudentAttendance(
    tenantId: string,
    date: string,
    classId?: string,
    actorUserId?: string,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTablesIfNeeded(tenantId, ds);
    if (actorUserId) {
      await assertClassTeacherMayAccess(ds, actorUserId, classId);
    }

    let students: any[];
    if (classId) {
      students = await runDbQuery(
        ds,
        `SELECT u.id, u.name, u.email
         FROM users u
         INNER JOIN student_class_enrollments e ON e.student_id = u.id
         WHERE e.class_id = ? AND u.roles LIKE ?
         ORDER BY u.name ASC`,
        [classId, `%"student"%`],
      );
    } else {
      students = await runDbQuery(
        ds,
        `SELECT id, name, email FROM users WHERE roles LIKE ? ORDER BY name ASC`,
        [`%"student"%`],
      );
    }

    const rows: any[] = classId
      ? await runDbQuery(
          ds,
          `SELECT student_id as studentId, status, created_at as createdAt FROM attendance_students WHERE date = ? AND class_id = ?`,
          [date, classId],
        )
      : await runDbQuery(
          ds,
          `SELECT student_id as studentId, status, created_at as createdAt FROM attendance_students WHERE date = ?`,
          [date],
        );
    const byStudent = new Map(
      rows.map((r) => [rowField(r, 'studentId', 'student_id'), r]),
    );
    return students.map((s) => {
      const found = byStudent.get(s.id);
      const createdAt = found
        ? rowField(found, 'createdAt', 'created_at')
        : null;
      return {
        id: s.id,
        name: s.name,
        email: s.email,
        status: (found ? rowField(found, 'status') : null) || 'absent',
        time: createdAt ? new Date(createdAt).toLocaleTimeString() : null,
      };
    });
  }

  async markStudentAttendance(params: {
    tenantId: string;
    userId: string;
    studentId: string;
    classId?: string;
    date: string;
    status: 'present' | 'absent' | 'late';
  }) {
    const { tenantId, userId, studentId, classId, date, status } = params;
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTablesIfNeeded(tenantId, ds);
    await assertClassTeacherMayAccess(ds, userId, classId);
    await assertTeacherMayMarkClass(ds, userId, classId);
    const id = `att_std_${Date.now()}_${Math.random()
      .toString(36)
      .slice(2, 7)}`;
    const existing = await runDbQuery(
      ds,
      `SELECT id FROM attendance_students
       WHERE student_id = ? AND date = ? AND COALESCE(class_id, '') = COALESCE(?, '') LIMIT 1`,
      [studentId, date, classId || null],
    );
    if (existing.length) {
      throw new BadRequestException(
        'This register entry is immutable. Use the authorized correction workflow with a reason.',
      );
    }
    await runDbQuery(
      ds,
      `INSERT INTO attendance_students (id, student_id, class_id, date, status, marked_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [id, studentId, classId || null, date, status, userId],
    );
    return { id, studentId, classId: classId || null, date, status };
  }

  async correctStudentAttendance(params: {
    tenantId: string;
    actorUserId: string;
    studentId: string;
    classId?: string;
    date: string;
    status: 'present' | 'absent' | 'late';
    reason: string;
  }) {
    const { tenantId, actorUserId, studentId, classId, date, status, reason } =
      params;
    if (!reason?.trim())
      throw new BadRequestException('A correction reason is required');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTablesIfNeeded(tenantId, ds);
    const rows = await runDbQuery(
      ds,
      `SELECT id FROM attendance_students
       WHERE student_id = ? AND date = ? AND COALESCE(class_id, '') = COALESCE(?, '') LIMIT 1`,
      [studentId, date, classId || null],
    );
    if (!rows.length) throw new NotFoundException('Register entry not found');
    await runDbQuery(
      ds,
      `UPDATE attendance_students
       SET status = ?, corrected_by = ?, correction_reason = ?, corrected_at = NOW(), updated_at = NOW()
       WHERE id = ?`,
      [status, actorUserId, reason.trim(), rows[0].id],
    );
    return {
      id: rows[0].id,
      studentId,
      classId: classId || null,
      date,
      status,
      corrected: true,
    };
  }

  async listStaffAttendance(
    tenantId: string,
    date: string,
    schoolLevel?: string,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTablesIfNeeded(tenantId, ds);

    const config = await this.organizationsService.getAttendanceConfig(
      tenantId,
    );
    const currentTermRows: any[] = await runDbQuery(
      ds,
      `SELECT start_date as startDate, end_date as endDate
       FROM academic_terms WHERE is_current = true ORDER BY created_at DESC LIMIT 1`,
    );
    const currentTerm = currentTermRows[0];
    const normalizedDate = String(date).slice(0, 10);
    if (
      (currentTerm?.startDate && normalizedDate < String(currentTerm.startDate).slice(0, 10)) ||
      (currentTerm?.endDate && normalizedDate > String(currentTerm.endDate).slice(0, 10))
    ) {
      return [];
    }
    const today = schoolIsoDate();
    const isToday = date === today;

    const allStaff: any[] = await runDbQuery(
      ds,
      `SELECT id, name, roles, school_level as schoolLevel
       FROM users WHERE roles NOT LIKE ? AND roles NOT LIKE ? ORDER BY name ASC`,
      [`%"student"%`, `%"parent"%`],
    );
    const requestedLevel = this.normalizeSchoolLevel(schoolLevel);
    const staff = allStaff.filter(
      (s) =>
        (!requestedLevel || this.schoolLevelMatches(requestedLevel, s.schoolLevel)),
    );

    const rows: any[] = await runDbQuery(
      ds,
      `SELECT staff_id as staffId, status, sign_in_time as signInTime, location, biometric_verified as biometricVerified
       FROM attendance_staff WHERE date = ?`,
      [date],
    );
    const byStaff = new Map(
      rows.map((r) => [rowField(r, 'staffId', 'staff_id'), r]),
    );

    const listDate = new Date(`${date}T12:00:00`);
    const businessDay = isBusinessDay(config, listDate);
    const sessionEnded = isToday && isSessionEnded(config);

    return staff.map((s) => {
      const found = byStaff.get(s.id);
      const signIn = found
        ? rowField(found, 'signInTime', 'sign_in_time')
        : null;
      const location = found ? rowField(found, 'location') : null;
      const biometricVerified = found
        ? !!rowField(found, 'biometricVerified', 'biometric_verified')
        : false;

      let status: 'present' | 'absent' | 'late' | 'pending';
      if (found) {
        status =
          (rowField(found, 'status') as 'present' | 'absent' | 'late') ||
          'absent';
      } else if (isToday && businessDay && !sessionEnded) {
        status = 'pending';
      } else {
        status = 'absent';
      }

      return {
        id: s.id,
        name: s.name,
        date,
        status,
        signIn: signIn || null,
        location:
          location ||
          (found
            ? biometricVerified
              ? 'Verified'
              : 'Unverified'
            : isToday && businessDay && !sessionEnded
            ? 'Awaiting verification'
            : 'Unverified'),
        biometricVerified,
        recorded: !!found,
      };
    });
  }

  async listMyStaffAttendance(tenantId: string, staffId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTablesIfNeeded(tenantId, ds);
    const termRows: any[] = await runDbQuery(
      ds,
      `SELECT id, name, start_date as startDate, end_date as endDate
       FROM academic_terms WHERE is_current = true ORDER BY created_at DESC LIMIT 1`,
    );
    const term = termRows[0] || null;
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT date, status, sign_in_time as signInTime, location,
              biometric_verified as biometricVerified
       FROM attendance_staff
       WHERE staff_id = ?
       ORDER BY date DESC`,
      [staffId],
    );
    const records = rows
      .filter((row) => {
        const date = String(row.date || '');
        return (
          (!term?.startDate || date >= String(term.startDate).slice(0, 10)) &&
          (!term?.endDate || date <= String(term.endDate).slice(0, 10))
        );
      })
      .map((row) => ({
        date: row.date,
        status: row.status,
        signIn: rowField(row, 'signInTime', 'sign_in_time') || null,
        location: row.location || null,
        biometricVerified: !!rowField(row, 'biometricVerified', 'biometric_verified'),
      }));
    return {
      term,
      records,
      summary: {
        present: records.filter((row) => row.status === 'present').length,
        late: records.filter((row) => row.status === 'late').length,
        absent: records.filter((row) => row.status === 'absent').length,
      },
    };
  }

  /**
   * After session end, mark unverified staff absent once per day.
   * Must not run on read/list endpoints — only explicit finalize paths.
   */
  async maybeFinalizeStaffDay(
    tenantId: string,
    date?: string,
    actorUserId?: string,
  ) {
    const today = date || schoolIsoDate();
    const config = await this.organizationsService.getAttendanceConfig(
      tenantId,
    );
    if (!isSessionEnded(config)) return { markedAbsent: 0 };

    const org = await this.organizationsService.getCurrentOrganization(
      tenantId,
    );
    const lastFinalize = (config as any).lastStaffFinalizeDate as
      | string
      | undefined;
    if (lastFinalize === today) {
      return { markedAbsent: 0, alreadyFinalized: true };
    }

    const result = await this.processEndOfDayAbsent(tenantId, today);
    if (org?.id && actorUserId) {
      await this.organizationsService.updateBusinessOrganization({
        tenantId,
        userId: actorUserId,
        id: org.id,
        body: {
          attendanceConfig: {
            ...config,
            lastStaffFinalizeDate: today,
          },
        },
      });
    }
    return { ...result, finalized: true };
  }

  async getStaffDailyVerificationStatus(
    tenantId: string,
    userId: string,
    jwtRoles: string[] = [],
  ) {
    const today = schoolIsoDate();
    const cacheKey = this.verificationStatusCacheKey(tenantId, userId, today);
    return this.listCache.getOrLoad(
      cacheKey,
      () => this.loadStaffDailyVerificationStatus(tenantId, userId, jwtRoles, today),
      false,
      60_000,
    );
  }

  /** Read-only status — never run end-of-day finalize side effects here. */
  private async loadStaffDailyVerificationStatus(
    tenantId: string,
    userId: string,
    jwtRoles: string[],
    today: string,
  ) {
    const [config, ds] = await Promise.all([
      this.organizationsService.getAttendanceConfig(tenantId),
      this.getTenantDs(tenantId),
    ]);
    const session = resolveSessionConfig(config);
    const verificationRequiredRoles = resolveVerificationRequiredRoles(config);

    const jwtNormalized = (jwtRoles || [])
      .map((r) => this.normalizeRole(r))
      .filter(Boolean);
    const dbRoles = await this.getStaffRoles(ds, userId);
    const userRoles = [...new Set([...dbRoles, ...jwtNormalized])];
    const required = userRequiresDailyVerification(userRoles, config);

    let verifiedRows: any[] = [];
    let enrolled = false;
    if (required) {
      const [rows, biometricStatus] = await Promise.all([
        runDbQuery(
          ds,
          `SELECT id, status, sign_in_time as signInTime
           FROM attendance_staff
           WHERE staff_id = ? AND date = ? AND biometric_verified = true
           LIMIT 1`,
          [userId, today],
        ),
        this.biometricService.getStatus(tenantId, userId).catch(() => null),
      ]);
      verifiedRows = rows;
      enrolled = !!biometricStatus?.enrolled;
    } else {
      try {
        const status = await this.biometricService.getStatus(tenantId, userId);
        enrolled = !!status?.enrolled;
      } catch {
        enrolled = false;
      }
    }

    return {
      required,
      verifiedToday: verifiedRows.length > 0,
      enrolled,
      verificationRequiredRoles,
      userRoles,
      windowOpen: isVerificationWindowOpen(config),
      sessionStartTime: session.sessionStartTime,
      sessionEndTime: session.sessionEndTime,
      biometricWindowStart: session.biometricWindowStart,
      biometricWindowEnd: session.biometricWindowEnd,
      businessDays: session.businessDays,
      today,
      attendance: verifiedRows[0]
        ? {
            status: verifiedRows[0].status,
            signInTime: rowField(verifiedRows[0], 'signInTime', 'sign_in_time'),
          }
        : null,
    };
  }

  async getStaffVerificationConfig(tenantId: string) {
    const config = await this.organizationsService.getAttendanceConfig(
      tenantId,
    );
    const session = resolveSessionConfig(config);
    return {
      ...config,
      ...session,
    };
  }

  private normalizeRole(role: unknown): string {
    if (role == null) return '';
    if (typeof role === 'object') {
      const obj = role as { id?: unknown; name?: unknown; role?: unknown };
      return this.normalizeRole(obj.id ?? obj.role ?? obj.name);
    }
    return String(role)
      .trim()
      .toLowerCase()
      .replace(/[\s-]+/g, '_');
  }

  private normalizeSchoolLevel(level: unknown): string {
    const value = String(level || '').trim().toLowerCase();
    if (value === 'jss' || value === 'sss') return 'secondary';
    return value;
  }

  private schoolLevelMatches(requested: string, actual: unknown): boolean {
    const value = this.normalizeSchoolLevel(actual);
    return value === requested || (!value && requested === 'all');
  }

  private parseRoles(raw: unknown): string[] {
    if (Array.isArray(raw)) {
      return raw.map((r) => this.normalizeRole(r)).filter(Boolean);
    }
    if (typeof raw === 'string') {
      const trimmed = raw.trim();
      if (!trimmed) return [];
      try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) {
          return parsed.map((r) => this.normalizeRole(r)).filter(Boolean);
        }
        if (typeof parsed === 'string') {
          const one = this.normalizeRole(parsed);
          return one ? [one] : [];
        }
      } catch {
        const one = this.normalizeRole(trimmed);
        return one ? [one] : [];
      }
    }
    return [];
  }

  private isVerificationTarget(
    rolesRaw: unknown,
    config: AttendanceConfigDto,
  ): boolean {
    return userRequiresDailyVerification(this.parseRoles(rolesRaw), config);
  }

  private async getStaffRoles(ds: any, staffId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [staffId],
    );
    return this.parseRoles(rows?.[0]?.roles);
  }

  private assertVerificationWindow(config: AttendanceConfigDto) {
    if (!isVerificationWindowOpen(config)) {
      const session = resolveSessionConfig(config);
      throw new BadRequestException(
        `Staff verification is only available during the configured window (${session.biometricWindowStart}–${session.biometricWindowEnd} on business days). Applies to fingerprint and manual code.`,
      );
    }
  }

  private async assertNotAlreadyVerified(
    ds: any,
    staffId: string,
    date: string,
  ) {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id FROM attendance_staff
       WHERE staff_id = ? AND date = ? AND biometric_verified = true
       LIMIT 1`,
      [staffId, date],
    );
    if (rows?.[0]) {
      throw new BadRequestException(
        'You have already completed biometric verification for today. Only one verification per day is allowed.',
      );
    }
  }

  private async persistStaffAttendance(params: {
    ds: any;
    tenantId: string;
    actorUserId: string;
    staffId: string;
    date: string;
    status: 'present' | 'absent' | 'late';
    resolvedLocation: string;
    biometricVerified: boolean;
    verificationMethod: 'biometric' | 'manual' | 'auto';
  }) {
    const {
      ds,
      tenantId,
      actorUserId,
      staffId,
      date,
      status,
      resolvedLocation,
      biometricVerified,
    } = params;
    const id = `att_stf_${Date.now()}_${Math.random()
      .toString(36)
      .slice(2, 7)}`;
    const signIn = new Date().toLocaleTimeString();
    await runDbQuery(
      ds,
      `DELETE FROM attendance_staff WHERE staff_id = ? AND date = ?`,
      [staffId, date],
    );
    await runDbQuery(
      ds,
      `INSERT INTO attendance_staff
        (id, staff_id, date, status, sign_in_time, location, biometric_verified, marked_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        staffId,
        date,
        status,
        signIn,
        resolvedLocation,
        biometricVerified,
        actorUserId,
      ],
    );

    if (biometricVerified) {
      await this.notificationsService.create({
        tenantId,
        userId: actorUserId,
        title: 'Staff attendance signed in',
        message: `You signed in as ${status} for ${date} at ${resolvedLocation}.`,
        type: 'success',
        href: '/dashboard/attendance',
      });
    }

    this.invalidateVerificationStatusCache(tenantId, staffId, date);

    return {
      id,
      staffId,
      date,
      status,
      signIn,
      location: resolvedLocation,
      biometricVerified,
    };
  }

  async processEndOfDayAbsent(tenantId: string, date: string) {
    const config = await this.organizationsService.getAttendanceConfig(
      tenantId,
    );
    if (!isSessionEnded(config)) return { markedAbsent: 0 };

    const ds = await this.getTenantDs(tenantId);
    await this.ensureTablesIfNeeded(tenantId, ds);

    const staffUsers: any[] = await runDbQuery(
      ds,
      `SELECT id, roles FROM users
       WHERE roles NOT LIKE ? AND roles NOT LIKE ?`,
      [`%"student"%`, `%"parent"%`],
    );

    let markedAbsent = 0;
    for (const teacher of staffUsers) {
      if (!this.isVerificationTarget(teacher.roles, config)) continue;
      const existing: any[] = await runDbQuery(
        ds,
        `SELECT id FROM attendance_staff WHERE staff_id = ? AND date = ? LIMIT 1`,
        [teacher.id, date],
      );
      if (existing?.[0]) continue;
      await this.persistStaffAttendance({
        ds,
        tenantId,
        actorUserId: teacher.id,
        staffId: teacher.id,
        date,
        status: 'absent',
        resolvedLocation: 'Auto-marked (no verification)',
        biometricVerified: false,
        verificationMethod: 'auto',
      });
      markedAbsent += 1;
    }
    return { markedAbsent };
  }

  async getManualVerificationCode(tenantId: string, userId: string) {
    const config = await this.organizationsService.getAttendanceConfig(
      tenantId,
    );
    if (config.manualVerificationCode) {
      return {
        code: config.manualVerificationCode,
        createdAt: config.manualCodeCreatedAt || null,
      };
    }
    return this.regenerateManualVerificationCode(tenantId, userId);
  }

  async regenerateManualVerificationCode(tenantId: string, userId: string) {
    const org = await this.organizationsService.getCurrentOrganization(
      tenantId,
    );
    if (!org?.id) throw new NotFoundException('Organization not found');

    const existing = await this.organizationsService.getAttendanceConfig(
      tenantId,
    );
    const code = generateManualVerificationCode();
    const createdAt = new Date().toISOString();
    await this.organizationsService.updateBusinessOrganization({
      tenantId,
      userId,
      id: org.id,
      body: {
        attendanceConfig: {
          ...existing,
          manualVerificationCode: code,
          manualCodeCreatedAt: createdAt,
        },
      },
    });
    return { code, createdAt };
  }

  private async consumeManualVerificationCode(
    tenantId: string,
    userId: string,
    submittedCode: string,
  ) {
    const config = await this.organizationsService.getAttendanceConfig(
      tenantId,
    );
    const expected = (config.manualVerificationCode || '').trim();
    if (!expected || expected !== submittedCode.trim()) {
      throw new BadRequestException(
        'Invalid verification code. Ask your school admin for the current code.',
      );
    }
    await this.regenerateManualVerificationCode(tenantId, userId);
  }

  private resolveStaffSignInLocation(params: {
    config: AttendanceConfigDto;
    latitude?: number;
    longitude?: number;
    locationHint?: string;
  }): string {
    const { config, latitude, longitude, locationHint } = params;
    const locations = config.locations || [];
    const verificationRequired =
      config.requireLocationVerification === true && locations.length > 0;

    if (verificationRequired) {
      if (
        typeof latitude !== 'number' ||
        !Number.isFinite(latitude) ||
        typeof longitude !== 'number' ||
        !Number.isFinite(longitude)
      ) {
        throw new BadRequestException(
          'Location access is required. Enable location on your device and try again.',
        );
      }
      const match = matchVerificationLocation(locations, latitude, longitude);
      if (!match) {
        throw new BadRequestException(
          'You are not within an approved staff sign-in location. Move closer to a configured campus/site and try again.',
        );
      }
      return match.name;
    }

    if (
      typeof latitude === 'number' &&
      Number.isFinite(latitude) &&
      typeof longitude === 'number' &&
      Number.isFinite(longitude) &&
      locations.length > 0
    ) {
      const match = matchVerificationLocation(locations, latitude, longitude);
      if (match) return match.name;
    }

    return locationHint?.trim() || 'Biometric verified';
  }

  async markStaffAttendance(params: {
    tenantId: string;
    actorUserId: string;
    staffId: string;
    date: string;
    status: 'present' | 'absent' | 'late';
    biometricAssertionId: string;
    location?: string;
    latitude?: number;
    longitude?: number;
  }) {
    const {
      tenantId,
      actorUserId,
      staffId,
      date,
      status,
      biometricAssertionId,
      location,
      latitude,
      longitude,
    } = params;

    if (actorUserId !== staffId) {
      throw new BadRequestException(
        'You can only biometric-sign yourself in. Ask an admin to override manually.',
      );
    }

    const ds = await this.getTenantDs(tenantId);
    await this.ensureTablesIfNeeded(tenantId, ds);
    const attendanceConfig =
      await this.organizationsService.getAttendanceConfig(tenantId);
    const roles = await this.getStaffRoles(ds, staffId);
    if (!this.isVerificationTarget(roles, attendanceConfig)) {
      throw new BadRequestException(
        'Daily staff verification applies only to roles configured by your school admin.',
      );
    }

    this.assertVerificationWindow(attendanceConfig);
    await this.assertNotAlreadyVerified(ds, staffId, date);

    this.biometricService.consumeAssertion({
      assertionId: biometricAssertionId,
      tenantId,
      userId: actorUserId,
      purpose: 'staff_attendance',
    });

    const resolvedLocation = this.resolveStaffSignInLocation({
      config: attendanceConfig,
      latitude,
      longitude,
      locationHint: location,
    });

    return this.persistStaffAttendance({
      ds,
      tenantId,
      actorUserId,
      staffId,
      date,
      status,
      resolvedLocation,
      biometricVerified: true,
      verificationMethod: 'biometric',
    });
  }

  async markStaffAttendanceWithManualCode(params: {
    tenantId: string;
    actorUserId: string;
    staffId: string;
    date: string;
    status: 'present' | 'absent' | 'late';
    verificationCode: string;
    location?: string;
    latitude?: number;
    longitude?: number;
  }) {
    const {
      tenantId,
      actorUserId,
      staffId,
      date,
      status,
      verificationCode,
      location,
      latitude,
      longitude,
    } = params;

    if (actorUserId !== staffId) {
      throw new BadRequestException('You can only verify yourself in.');
    }

    const ds = await this.getTenantDs(tenantId);
    await this.ensureTablesIfNeeded(tenantId, ds);
    const attendanceConfig =
      await this.organizationsService.getAttendanceConfig(tenantId);
    const roles = await this.getStaffRoles(ds, staffId);
    if (!this.isVerificationTarget(roles, attendanceConfig)) {
      throw new BadRequestException(
        'Daily staff verification applies only to roles configured by your school admin.',
      );
    }

    this.assertVerificationWindow(attendanceConfig);
    await this.assertNotAlreadyVerified(ds, staffId, date);
    await this.consumeManualVerificationCode(
      tenantId,
      actorUserId,
      verificationCode,
    );

    const resolvedLocation = this.resolveStaffSignInLocation({
      config: attendanceConfig,
      latitude,
      longitude,
      locationHint: location || 'Manual code verified',
    });

    return this.persistStaffAttendance({
      ds,
      tenantId,
      actorUserId,
      staffId,
      date,
      status,
      resolvedLocation,
      biometricVerified: true,
      verificationMethod: 'manual',
    });
  }
}
