import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../../control-plane/control-plane.service';
import { TenantConnectionService } from '../../database/tenant-connection.service';
import { randomToken } from '../../common/utils/id.util';
import { runDbQuery } from '../../database/db-driver.util';
import { FeatureFlagService } from '../../platform-config/feature-flag.service';

@Injectable()
export class HostelService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS hostel_houses (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        gender varchar(16) NULL,
        capacity int NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS hostel_beds (
        id varchar(64) PRIMARY KEY,
        house_id varchar(64) NOT NULL,
        label varchar(64) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'available',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS hostel_allocations (
        id varchar(64) PRIMARY KEY,
        bed_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        from_date varchar(32) NULL,
        to_date varchar(32) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async listHouses(tenantId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'ops.hostel');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.getRoles(ds, actorId);
    if (!canViewHostelRole(roles)) throw new NotFoundException('Hostel access is restricted');
    const houses = await runDbQuery(
      ds,
      `SELECT id, name, gender, capacity, created_at as "createdAt" FROM hostel_houses ORDER BY name`,
      [],
    );
    const houseIds = houses.map((house: { id: string }) => house.id);
    if (!houseIds.length) return [];
    const placeholders = houseIds.map(() => '?').join(',');
    const beds = await runDbQuery(
      ds,
      `SELECT id, house_id as "houseId", label, status
       FROM hostel_beds WHERE house_id IN (${placeholders})
       ${roles.some((role) => ['parent', 'student'].includes(role)) ? `AND id IN (
         SELECT bed_id FROM hostel_allocations WHERE ${roles.includes('parent')
           ? 'student_id IN (SELECT student_id FROM parent_student_links WHERE parent_id = ?)'
           : 'student_id = ?'}
       )` : ''}
       ORDER BY label`,
      roles.includes('parent') ? [...houseIds, actorId] : roles.includes('student') ? [...houseIds, actorId] : houseIds,
    );
    const bedsByHouse = new Map<string, any[]>();
    for (const bed of beds) {
      const list = bedsByHouse.get(bed.houseId) || [];
      list.push(bed);
      bedsByHouse.set(bed.houseId, list);
    }
    return houses.map((house: { id: string }) => ({
      ...house,
      beds: bedsByHouse.get(house.id) || [],
    }));
  }

  async createHouse(tenantId: string, actorId: string, body: { name: string; gender?: string; capacity?: number }) {
    await this.flags.assertEnabled(tenantId, 'ops.hostel');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);
    if (!body.name?.trim() || Number(body.capacity ?? 0) < 0) throw new BadRequestException('House values are invalid');
    const id = randomToken('hse');
    await runDbQuery(
      ds,
      `INSERT INTO hostel_houses (id, name, gender, capacity, created_at) VALUES (?, ?, ?, ?, NOW())`,
      [id, body.name, body.gender || null, body.capacity ?? 0],
    );
    return { id };
  }

  async addBed(tenantId: string, actorId: string, body: { houseId: string; label: string }) {
    await this.flags.assertEnabled(tenantId, 'ops.hostel');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);
    if (!body.houseId || !body.label?.trim()) throw new BadRequestException('Bed values are invalid');
    const houses: any[] = await runDbQuery(ds, `SELECT id FROM hostel_houses WHERE id = ? LIMIT 1`, [body.houseId]);
    if (!houses.length) throw new NotFoundException('House not found');
    const id = randomToken('bed');
    await runDbQuery(
      ds,
      `INSERT INTO hostel_beds (id, house_id, label, status, created_at) VALUES (?, ?, ?, 'available', NOW())`,
      [id, body.houseId, body.label],
    );
    return { id };
  }

  async allocate(tenantId: string, actorId: string, body: { bedId: string; studentId: string; fromDate?: string; toDate?: string }) {
    await this.flags.assertEnabled(tenantId, 'ops.hostel');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);
    if (!body.bedId || !body.studentId || (body.toDate && body.fromDate && body.toDate < body.fromDate)) {
      throw new BadRequestException('Allocation values are invalid');
    }
    const beds: any[] = await runDbQuery(
      ds,
      `SELECT status FROM hostel_beds WHERE id = ? LIMIT 1`,
      [body.bedId],
    );
    if (!beds.length) throw new NotFoundException('Bed not found');
    if (beds[0].status !== 'available') {
      throw new BadRequestException('Bed is not available');
    }
    const id = randomToken('hal');
    await runDbQuery(
      ds,
      `INSERT INTO hostel_allocations (id, bed_id, student_id, from_date, to_date, created_at)
       VALUES (?, ?, ?, ?, ?, NOW())`,
      [id, body.bedId, body.studentId, body.fromDate || null, body.toDate || null],
    );
    await runDbQuery(ds, `UPDATE hostel_beds SET status = 'occupied' WHERE id = ?`, [body.bedId]);
    return { id };
  }

  private async getRoles(ds: any, actorId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [actorId]);
    const row = rows[0];
    let roles: unknown[] = row?.role ? [row.role] : [];
    if (row?.roles) {
      try { roles = typeof row.roles === 'string' ? JSON.parse(row.roles) : row.roles; } catch { /* fallback */ }
    }
    return Array.isArray(roles) ? roles.map((role) => String(role).toLowerCase()) : [];
  }

  private async assertManager(ds: any, actorId: string) {
    const roles = await this.getRoles(ds, actorId);
    if (!roles.some((role) => ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff'].includes(role))) {
      throw new NotFoundException('Hostel management is restricted');
    }
  }
}

export function canViewHostelRole(roles: string[]) {
  return roles.some((role) => [
    'director', 'school_admin', 'it_admin', 'principal', 'head_teacher',
    'administrative_staff', 'parent', 'student',
  ].includes(role));
}
