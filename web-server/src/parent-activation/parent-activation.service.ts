import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { createHash, randomInt, timingSafeEqual } from 'crypto';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { PasswordService } from '../common/security/password.service';
import { NotificationsService } from '../notifications/notifications.service';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { parseRoleList } from '../common/auth/teacher-scope.util';

function hashCode(value: string): string {
  return createHash('sha256').update(value.trim().toUpperCase()).digest('hex');
}

function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  if (left.length !== right.length) return false;
  return timingSafeEqual(new Uint8Array(left), new Uint8Array(right));
}

@Injectable()
export class ParentActivationService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly passwords: PasswordService,
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
      CREATE TABLE IF NOT EXISTS parent_activation_codes (
        id varchar(64) PRIMARY KEY,
        parent_id varchar(64) NOT NULL,
        code_hash varchar(128) NOT NULL,
        code_hint varchar(16) NOT NULL,
        student_ids text NOT NULL DEFAULT '[]',
        expires_at TIMESTAMP NOT NULL,
        used_at TIMESTAMP NULL,
        recovered_at TIMESTAMP NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  private generateCode(): string {
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    let out = '';
    for (let i = 0; i < 8; i++) {
      out += alphabet[randomInt(alphabet.length)];
    }
    return out;
  }

  async issue(
    tenantId: string,
    actorId: string,
    body: { parentId: string; studentIds?: string[]; daysValid?: number },
  ) {
    await this.flags.assertEnabled(tenantId, 'users.parent_activation');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const parents = await runDbQuery(
      ds,
      `SELECT id, name, email, roles FROM users WHERE id = ? LIMIT 1`,
      [body.parentId],
    );
    if (!parents.length) throw new NotFoundException('Parent not found');
    const roles = parseRoleList(parents[0].roles);
    if (!roles.includes('parent')) {
      throw new BadRequestException('User is not a parent');
    }

    let studentIds = Array.isArray(body.studentIds) ? body.studentIds : [];
    if (!studentIds.length) {
      const links = await runDbQuery(
        ds,
        `SELECT student_id as "studentId" FROM parent_student_links WHERE parent_id = ?`,
        [body.parentId],
      );
      studentIds = links.map((row: any) => String(row.studentId || row.studentid));
    }

    await runDbQuery(
      ds,
      `UPDATE parent_activation_codes
       SET recovered_at = NOW()
       WHERE parent_id = ? AND used_at IS NULL AND recovered_at IS NULL`,
      [body.parentId],
    );

    const code = this.generateCode();
    const id = randomToken('pac');
    const days = Math.min(Math.max(Number(body.daysValid ?? 14), 1), 30);
    const expiresAt = new Date(Date.now() + days * 86400000).toISOString();
    await runDbQuery(
      ds,
      `INSERT INTO parent_activation_codes
        (id, parent_id, code_hash, code_hint, student_ids, expires_at, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, NOW())`,
      [
        id,
        body.parentId,
        hashCode(code),
        code.slice(-4),
        JSON.stringify(studentIds),
        expiresAt,
        actorId,
      ],
    );

    await this.notifications.create({
      tenantId,
      userId: body.parentId,
      title: 'Parent activation code issued',
      message:
        'Your school issued an activation code. Use it with the school login slug to set your password.',
      type: 'info',
      href: '/activate',
    });

    return {
      id,
      parentId: body.parentId,
      parentName: parents[0].name,
      parentEmail: parents[0].email,
      code,
      codeHint: code.slice(-4),
      expiresAt,
      studentIds,
    };
  }

  async list(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'users.parent_activation');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    return runDbQuery(
      ds,
      `SELECT c.id, c.parent_id as "parentId", u.name as "parentName", u.email as "parentEmail",
              c.code_hint as "codeHint", c.student_ids as "studentIds",
              c.expires_at as "expiresAt", c.used_at as "usedAt",
              c.recovered_at as "recoveredAt", c.created_at as "createdAt"
       FROM parent_activation_codes c
       JOIN users u ON u.id = c.parent_id
       ORDER BY c.created_at DESC
       LIMIT 300`,
      [],
    );
  }

  async stats(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'users.parent_activation');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const rows = await runDbQuery(
      ds,
      `SELECT c.id, c.name,
              COUNT(DISTINCT e.student_id)::int as "studentCount",
              COUNT(DISTINCT l.parent_id)::int as "linkedParents",
              COUNT(DISTINCT CASE
                WHEN a.used_at IS NOT NULL THEN a.parent_id
                ELSE NULL
              END)::int as "activatedParents"
       FROM academic_classes c
       LEFT JOIN student_class_enrollments e ON e.class_id = c.id
       LEFT JOIN parent_student_links l ON l.student_id = e.student_id
       LEFT JOIN parent_activation_codes a ON a.parent_id = l.parent_id AND a.used_at IS NOT NULL
       WHERE c.is_active = true
       GROUP BY c.id, c.name
       ORDER BY c.name`,
      [],
    );
    return (rows as any[]).map((row) => {
      const linked = Number(row.linkedParents || 0);
      const activated = Number(row.activatedParents || 0);
      return {
        classId: row.id,
        className: row.name,
        studentCount: Number(row.studentCount || 0),
        linkedParents: linked,
        activatedParents: activated,
        activationRate: linked ? Math.round((activated / linked) * 100) : 0,
      };
    });
  }

  async searchSchools(query: string) {
    return this.controlPlane.searchReadySchools(query);
  }

  async preview(slug: string, code: string) {
    const tenant = await this.controlPlane.resolveTenantBySlug(slug);
    const ds = await this.getTenantDs(tenant.id);
    await this.ensure(ds);
    const hashed = hashCode(code);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT c.id, c.parent_id as "parentId", c.expires_at as "expiresAt",
              c.used_at as "usedAt", c.recovered_at as "recoveredAt",
              c.code_hash as "codeHash", u.name as "parentName", u.email as "parentEmail"
       FROM parent_activation_codes c
       JOIN users u ON u.id = c.parent_id
       WHERE c.used_at IS NULL AND c.recovered_at IS NULL
       ORDER BY c.created_at DESC
       LIMIT 50`,
      [],
    );
    const match = rows.find((row) => safeEqual(String(row.codeHash), hashed));
    if (!match || new Date(match.expiresAt) < new Date()) {
      throw new BadRequestException('Activation code is invalid or expired');
    }
    return {
      schoolSlug: tenant.slug,
      parentName: match.parentName,
      parentEmail: match.parentEmail,
      expiresAt: match.expiresAt,
    };
  }

  async activate(body: {
    schoolSlug: string;
    code: string;
    password: string;
  }) {
    if (!body.password || body.password.length < 8) {
      throw new BadRequestException('Password must be at least 8 characters');
    }
    const tenant = await this.controlPlane.resolveTenantBySlug(body.schoolSlug);
    await this.flags.assertEnabled(tenant.id, 'users.parent_activation');
    const ds = await this.getTenantDs(tenant.id);
    await this.ensure(ds);
    const hashed = hashCode(body.code);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT id, parent_id as "parentId", expires_at as "expiresAt",
              used_at as "usedAt", recovered_at as "recoveredAt", code_hash as "codeHash"
       FROM parent_activation_codes
       WHERE used_at IS NULL AND recovered_at IS NULL
       ORDER BY created_at DESC
       LIMIT 100`,
      [],
    );
    const match = rows.find((row) => safeEqual(String(row.codeHash), hashed));
    if (!match || new Date(match.expiresAt) < new Date()) {
      throw new BadRequestException('Activation code is invalid or expired');
    }

    const passwordHash = this.passwords.hashPassword(body.password);
    await runDbQuery(
      ds,
      `UPDATE users SET password_hash = ?, is_active = true, updated_at = NOW() WHERE id = ?`,
      [passwordHash, match.parentId],
    );
    await runDbQuery(
      ds,
      `UPDATE parent_activation_codes SET used_at = NOW() WHERE id = ?`,
      [match.id],
    );
    return {
      parentId: match.parentId,
      schoolSlug: tenant.slug,
      activated: true,
    };
  }
}
