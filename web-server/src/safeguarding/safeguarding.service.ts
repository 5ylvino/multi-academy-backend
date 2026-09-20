import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { createHash, randomInt, timingSafeEqual } from 'crypto';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { NotificationsService } from '../notifications/notifications.service';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { parseRoleList } from '../common/auth/teacher-scope.util';

function hashValue(value: string): string {
  return createHash('sha256').update(value.trim().toUpperCase()).digest('hex');
}

function digits(length: number): string {
  let out = '';
  for (let i = 0; i < length; i++) out += String(randomInt(10));
  return out;
}

function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  if (left.length !== right.length) return false;
  return timingSafeEqual(new Uint8Array(left), new Uint8Array(right));
}

@Injectable()
export class SafeguardingService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly notifications: NotificationsService,
    private readonly flags: FeatureFlagService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS authorized_pickup_adults (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        name varchar(255) NOT NULL,
        phone varchar(64) NULL,
        photo_url text NULL,
        relationship varchar(64) NULL,
        id_number varchar(128) NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS pickup_delegations (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        adult_name varchar(255) NOT NULL,
        phone varchar(64) NULL,
        photo_url text NULL,
        id_capture text NULL,
        valid_on varchar(32) NOT NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS safeguarding_exeats (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        kind varchar(32) NOT NULL,
        reason text NOT NULL,
        pickup_at TIMESTAMP NOT NULL,
        pickup_adult_name varchar(255) NOT NULL,
        pickup_adult_phone varchar(64) NULL,
        pickup_adult_id varchar(64) NULL,
        status varchar(32) NOT NULL,
        form_teacher_id varchar(64) NULL,
        form_teacher_at TIMESTAMP NULL,
        principal_id varchar(64) NULL,
        principal_at TIMESTAMP NULL,
        parent_otp_hash varchar(128) NULL,
        parent_otp_expires TIMESTAMP NULL,
        parent_confirmed_at TIMESTAMP NULL,
        pass_code varchar(16) NULL,
        pass_expires_at TIMESTAMP NULL,
        gate_used_at TIMESTAMP NULL,
        gate_used_by varchar(64) NULL,
        decision_note text NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  private async rolesOf(ds: any, userId: string): Promise<string[]> {
    const rows = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [userId]);
    return parseRoleList(rows?.[0]?.roles);
  }

  private async assertWard(ds: any, parentId: string, studentId: string) {
    const links = await runDbQuery(
      ds,
      `SELECT 1 FROM parent_student_links WHERE parent_id = ? AND student_id = ? LIMIT 1`,
      [parentId, studentId],
    );
    if (!links.length) throw new ForbiddenException('Student is not linked to this parent');
  }

  private async formTeacherId(ds: any, studentId: string): Promise<string | null> {
    const rows = await runDbQuery(
      ds,
      `SELECT c.class_teacher_id as "teacherId"
       FROM student_class_enrollments e
       JOIN academic_classes c ON c.id = e.class_id
       WHERE e.student_id = ?
       ORDER BY e.created_at DESC NULLS LAST
       LIMIT 1`,
      [studentId],
    );
    return rows[0]?.teacherId || rows[0]?.teacherid || null;
  }

  private async leaderIds(ds: any): Promise<string[]> {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, roles FROM users WHERE is_active = true`,
      [],
    );
    return rows
      .filter((row) => {
        const roles = parseRoleList(row.roles);
        return roles.includes('principal') || roles.includes('head_teacher');
      })
      .map((row) => String(row.id));
  }

  private async notifyMany(
    tenantId: string,
    userIds: string[],
    title: string,
    message: string,
    href: string,
  ) {
    for (const userId of [...new Set(userIds.filter(Boolean))]) {
      await this.notifications.create({
        tenantId,
        userId,
        title,
        message,
        type: 'warning',
        href,
      });
    }
  }

  async listAdults(tenantId: string, studentId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.rolesOf(ds, actorId);
    if (roles.includes('parent')) await this.assertWard(ds, actorId, studentId);
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", name, phone, photo_url as "photoUrl",
              relationship, id_number as "idNumber", created_at as "createdAt"
       FROM authorized_pickup_adults WHERE student_id = ? ORDER BY created_at DESC`,
      [studentId],
    );
  }

  async addAdult(
    tenantId: string,
    actorId: string,
    body: {
      studentId: string;
      name: string;
      phone?: string;
      photoUrl?: string;
      relationship?: string;
      idNumber?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.rolesOf(ds, actorId);
    if (roles.includes('parent')) await this.assertWard(ds, actorId, body.studentId);
    const id = randomToken('adu');
    await runDbQuery(
      ds,
      `INSERT INTO authorized_pickup_adults
        (id, student_id, name, phone, photo_url, relationship, id_number, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW())`,
      [
        id,
        body.studentId,
        body.name,
        body.phone || null,
        body.photoUrl || null,
        body.relationship || null,
        body.idNumber || null,
        actorId,
      ],
    );
    return { id };
  }

  async removeAdult(tenantId: string, actorId: string, adultId: string) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.rolesOf(ds, actorId);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, student_id as "studentId"
       FROM authorized_pickup_adults WHERE id = ? LIMIT 1`,
      [adultId],
    );
    const adult = rows[0];
    if (!adult) throw new NotFoundException('Authorized adult not found');
    if (roles.includes('parent')) {
      await this.assertWard(ds, actorId, adult.studentId);
    } else if (
      !roles.some((role) =>
        ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher'].includes(role),
      )
    ) {
      throw new ForbiddenException('You cannot remove authorized adults');
    }
    await runDbQuery(ds, `DELETE FROM authorized_pickup_adults WHERE id = ?`, [adultId]);
    return { id: adultId };
  }

  async addDelegation(
    tenantId: string,
    actorId: string,
    body: {
      studentId: string;
      adultName: string;
      phone?: string;
      photoUrl?: string;
      idCapture?: string;
      validOn: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.rolesOf(ds, actorId);
    if (roles.includes('parent')) await this.assertWard(ds, actorId, body.studentId);
    const id = randomToken('del');
    await runDbQuery(
      ds,
      `INSERT INTO pickup_delegations
        (id, student_id, adult_name, phone, photo_url, id_capture, valid_on, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW())`,
      [
        id,
        body.studentId,
        body.adultName,
        body.phone || null,
        body.photoUrl || null,
        body.idCapture || null,
        body.validOn,
        actorId,
      ],
    );
    return { id };
  }

  async listExeats(tenantId: string, actorId: string, studentId?: string) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.rolesOf(ds, actorId);
    let sql = `SELECT e.id, e.student_id as "studentId", u.name as "studentName",
                      e.kind, e.reason, e.pickup_at as "pickupAt",
                      e.pickup_adult_name as "pickupAdultName",
                      e.pickup_adult_phone as "pickupAdultPhone",
                      e.status, e.pass_code as "passCode",
                      e.pass_expires_at as "passExpiresAt",
                      e.gate_used_at as "gateUsedAt",
                      e.created_at as "createdAt"
               FROM safeguarding_exeats e
               LEFT JOIN users u ON u.id = e.student_id`;
    const params: any[] = [];
    if (roles.includes('parent')) {
      sql += ` JOIN parent_student_links l ON l.student_id = e.student_id AND l.parent_id = ?`;
      params.push(actorId);
    } else if (roles.includes('class_teacher') && !roles.includes('principal') && !roles.includes('head_teacher')) {
      sql += ` JOIN student_class_enrollments en ON en.student_id = e.student_id
               JOIN academic_classes c ON c.id = en.class_id AND (c.class_teacher_id = ? OR EXISTS (
                 SELECT 1 FROM academic_class_teacher_links t
                 WHERE t.class_id = c.id AND t.teacher_id = ?
               ))`;
      params.push(actorId, actorId);
    }
    if (studentId) {
      sql += params.length ? ` WHERE e.student_id = ?` : ` WHERE e.student_id = ?`;
      params.push(studentId);
    }
    sql += ` ORDER BY e.created_at DESC LIMIT 200`;
    return runDbQuery(ds, sql, params);
  }

  async createExeat(
    tenantId: string,
    actorId: string,
    body: {
      studentId: string;
      kind?: 'parent_initiated' | 'school_initiated';
      reason: string;
      pickupAt: string;
      pickupAdultName: string;
      pickupAdultPhone?: string;
      pickupAdultId?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.rolesOf(ds, actorId);
    const kind = body.kind || (roles.includes('parent') ? 'parent_initiated' : 'school_initiated');
    if (roles.includes('parent')) {
      await this.assertWard(ds, actorId, body.studentId);
      if (kind !== 'parent_initiated') {
        throw new ForbiddenException('Parents can only create parent-initiated exeats');
      }
    }

    const formTeacherId = await this.formTeacherId(ds, body.studentId);
    const status =
      kind === 'school_initiated'
        ? 'pending_parent'
        : formTeacherId
          ? 'pending_form_teacher'
          : 'pending_principal';
    const id = randomToken('exe');
    let otp: string | null = null;
    let otpHash: string | null = null;
    let otpExpires: string | null = null;
    if (kind === 'school_initiated') {
      otp = digits(6);
      otpHash = hashValue(otp);
      otpExpires = new Date(Date.now() + 15 * 60_000).toISOString();
    }

    await runDbQuery(
      ds,
      `INSERT INTO safeguarding_exeats
        (id, student_id, kind, reason, pickup_at, pickup_adult_name, pickup_adult_phone,
         pickup_adult_id, status, parent_otp_hash, parent_otp_expires, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        body.studentId,
        kind,
        body.reason,
        body.pickupAt,
        body.pickupAdultName,
        body.pickupAdultPhone || null,
        body.pickupAdultId || null,
        status,
        otpHash,
        otpExpires,
        actorId,
      ],
    );

    const href = '/dashboard/safeguarding';
    if (kind === 'parent_initiated' && formTeacherId) {
      await this.notifyMany(
        tenantId,
        [formTeacherId],
        'Exeat request pending review',
        body.reason,
        href,
      );
    } else if (kind === 'parent_initiated') {
      await this.notifyMany(tenantId, await this.leaderIds(ds), 'Exeat request pending approval', body.reason, href);
    } else {
      const parents = await runDbQuery(
        ds,
        `SELECT parent_id as "parentId" FROM parent_student_links WHERE student_id = ?`,
        [body.studentId],
      );
      await this.notifyMany(
        tenantId,
        parents.map((row: any) => String(row.parentId || row.parentid)),
        'School exit consent required',
        `Approve with in-app OTP to release the student. ${otp ? `OTP: ${otp}` : ''}`,
        '/dashboard/parent',
      );
    }

    return { id, status, parentOtp: otp };
  }

  async review(
    tenantId: string,
    actorId: string,
    id: string,
    decision: 'approve' | 'decline',
    note?: string,
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT * FROM safeguarding_exeats WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!rows.length) throw new NotFoundException('Exeat not found');
    const row = rows[0];
    if (row.status !== 'pending_form_teacher') {
      throw new BadRequestException('Exeat is not waiting for form-teacher review');
    }
    const formTeacherId = await this.formTeacherId(ds, row.student_id);
    const roles = await this.rolesOf(ds, actorId);
    const canReview =
      actorId === formTeacherId ||
      roles.includes('principal') ||
      roles.includes('head_teacher') ||
      roles.includes('school_admin');
    if (!canReview) throw new ForbiddenException('Only the form teacher can review this request');

    if (decision === 'decline') {
      await runDbQuery(
        ds,
        `UPDATE safeguarding_exeats
         SET status = 'declined', form_teacher_id = ?, form_teacher_at = NOW(),
             decision_note = ?, updated_at = NOW()
         WHERE id = ?`,
        [actorId, note || null, id],
      );
      await this.notifyParents(tenantId, ds, row.student_id, 'Exeat declined', note || 'The school declined this exit request.');
      return { id, status: 'declined' };
    }

    await runDbQuery(
      ds,
      `UPDATE safeguarding_exeats
       SET status = 'pending_principal', form_teacher_id = ?, form_teacher_at = NOW(), updated_at = NOW()
       WHERE id = ?`,
      [actorId, id],
    );
    await this.notifyMany(tenantId, await this.leaderIds(ds), 'Exeat pending principal approval', row.reason, '/dashboard/safeguarding');
    return { id, status: 'pending_principal' };
  }

  async decide(
    tenantId: string,
    actorId: string,
    id: string,
    decision: 'approve' | 'decline',
    note?: string,
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.rolesOf(ds, actorId);
    if (!roles.some((role) => ['principal', 'head_teacher', 'school_admin', 'director'].includes(role))) {
      throw new ForbiddenException('Only a school leader can approve exeats');
    }
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT * FROM safeguarding_exeats WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!rows.length) throw new NotFoundException('Exeat not found');
    const row = rows[0];
    if (!['pending_principal', 'pending_form_teacher'].includes(row.status)) {
      throw new BadRequestException('Exeat is not waiting for approval');
    }
    if (decision === 'decline') {
      await runDbQuery(
        ds,
        `UPDATE safeguarding_exeats
         SET status = 'declined', principal_id = ?, principal_at = NOW(),
             decision_note = ?, updated_at = NOW()
         WHERE id = ?`,
        [actorId, note || null, id],
      );
      await this.notifyParents(tenantId, ds, row.student_id, 'Exeat declined', note || 'The school declined this exit request.');
      return { id, status: 'declined' };
    }
    const pass = await this.issuePass(ds, id, actorId);
    await this.notifyParents(
      tenantId,
      ds,
      row.student_id,
      'Exeat approved',
      `Digital exit pass ${pass.passCode} is valid until ${pass.passExpiresAt}.`,
    );
    return { id, status: 'approved', ...pass };
  }

  async confirmParentOtp(tenantId: string, parentId: string, id: string, otp: string) {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT * FROM safeguarding_exeats WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!rows.length) throw new NotFoundException('Exeat not found');
    const row = rows[0];
    await this.assertWard(ds, parentId, row.student_id);
    if (row.status !== 'pending_parent') {
      throw new BadRequestException('This request is not waiting for parent confirmation');
    }
    if (!row.parent_otp_hash || !row.parent_otp_expires) {
      throw new BadRequestException('OTP is not available');
    }
    if (new Date(row.parent_otp_expires) < new Date()) {
      throw new BadRequestException('OTP expired');
    }
    if (!safeEqual(String(row.parent_otp_hash), hashValue(otp))) {
      throw new ForbiddenException('Invalid OTP');
    }
    const pass = await this.issuePass(ds, id, parentId);
    await runDbQuery(
      ds,
      `UPDATE safeguarding_exeats SET parent_confirmed_at = NOW(), updated_at = NOW() WHERE id = ?`,
      [id],
    );
    return { id, status: 'approved', ...pass };
  }

  private async issuePass(ds: any, id: string, actorId: string) {
    const passCode = digits(6);
    const passExpiresAt = new Date(Date.now() + 6 * 3600000).toISOString();
    await runDbQuery(
      ds,
      `UPDATE safeguarding_exeats
       SET status = 'approved', principal_id = COALESCE(principal_id, ?),
           principal_at = COALESCE(principal_at, NOW()),
           pass_code = ?, pass_expires_at = ?, updated_at = NOW()
       WHERE id = ?`,
      [actorId, passCode, passExpiresAt, id],
    );
    return { passCode, passExpiresAt };
  }

  async redeemPass(
    tenantId: string,
    actorId: string,
    passCode: string,
  ): Promise<{
    id: string;
    studentId: string;
    status: string;
    pickupAdultName?: string;
  } | null> {
    await this.flags.assertEnabled(tenantId, 'ops.safeguarding');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", pickup_adult_name as "pickupAdultName",
              status, pass_expires_at as "passExpiresAt", gate_used_at as "gateUsedAt"
       FROM safeguarding_exeats WHERE pass_code = ? LIMIT 1`,
      [passCode],
    );
    if (!rows.length) return null;
    const row = rows[0];
    if (row.gateUsedAt) return { id: row.id, studentId: row.studentId, status: 'already_used' };
    if (row.status !== 'approved' || new Date(row.passExpiresAt) < new Date()) {
      await this.notifyMany(
        tenantId,
        await this.leaderIds(ds),
        'Invalid exit pass attempt',
        `Pass ${passCode} was presented but is not valid.`,
        '/dashboard/safeguarding',
      );
      return { id: row.id, studentId: row.studentId, status: 'expired' };
    }
    await runDbQuery(
      ds,
      `UPDATE safeguarding_exeats
       SET status = 'used', gate_used_at = NOW(), gate_used_by = ?, updated_at = NOW()
       WHERE id = ?`,
      [actorId, row.id],
    );
    await this.notifyParents(
      tenantId,
      ds,
      row.studentId,
      'Student left school',
      `${row.pickupAdultName || 'An authorized adult'} collected the student.`,
    );
    return {
      id: row.id,
      studentId: row.studentId,
      status: 'redeemed',
      pickupAdultName: row.pickupAdultName,
    };
  }

  private async notifyParents(
    tenantId: string,
    ds: any,
    studentId: string,
    title: string,
    message: string,
  ) {
    const parents = await runDbQuery(
      ds,
      `SELECT parent_id as "parentId" FROM parent_student_links WHERE student_id = ?`,
      [studentId],
    );
    await this.notifyMany(
      tenantId,
      parents.map((row: any) => String(row.parentId || row.parentid)),
      title,
      message,
      '/dashboard/parent',
    );
  }
}
