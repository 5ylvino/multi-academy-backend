import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../../control-plane/control-plane.service';
import { TenantConnectionService } from '../../database/tenant-connection.service';
import { randomToken } from '../../common/utils/id.util';
import { runDbQuery } from '../../database/db-driver.util';
import { FeatureFlagService } from '../../platform-config/feature-flag.service';

@Injectable()
export class TransportService {
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
      CREATE TABLE IF NOT EXISTS transport_routes (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        stops text NULL,
        driver_id varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS transport_vehicles (
        id varchar(64) PRIMARY KEY,
        plate varchar(32) NOT NULL,
        capacity int NOT NULL DEFAULT 30,
        route_id varchar(64) NULL,
        status varchar(32) NOT NULL DEFAULT 'active',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS transport_assignments (
        id varchar(64) PRIMARY KEY,
        route_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        pickup_stop varchar(128) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async listRoutes(tenantId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'ops.transport');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    let routes: any[] = await runDbQuery(
      ds,
      `SELECT id, name, stops, driver_id as "driverId", created_at as "createdAt"
       FROM transport_routes ORDER BY created_at DESC LIMIT 200`,
      [],
    );
    const roles = await this.getUserRoles(ds, actorId);
    if (roles.includes('driver')) {
      routes = routes.filter((route) => route.driverId === actorId);
    } else if (roles.includes('student') || roles.includes('parent')) {
      const assignments: any[] = await runDbQuery(
        ds,
        roles.includes('student')
          ? `SELECT route_id as "routeId" FROM transport_assignments WHERE student_id = ?`
          : `SELECT DISTINCT a.route_id as "routeId"
             FROM transport_assignments a
             JOIN parent_student_links p ON p.student_id = a.student_id
             WHERE p.parent_id = ?`,
        [actorId],
      );
      const allowed = new Set(assignments.map((row) => row.routeId));
      routes = routes.filter((route) => allowed.has(route.id));
    }
    const routeIds = routes.map((route: { id: string }) => route.id);
    if (!routeIds.length) return [];
    const placeholders = routeIds.map(() => '?').join(',');
    const [vehicles, assignments] = await Promise.all([
      runDbQuery(
        ds,
        `SELECT id, plate, capacity, route_id as "routeId", status
         FROM transport_vehicles WHERE route_id IN (${placeholders})`,
        routeIds,
      ),
      runDbQuery(
        ds,
        `SELECT id, route_id as "routeId", student_id as "studentId", pickup_stop as "pickupStop"
         FROM transport_assignments WHERE route_id IN (${placeholders})`,
        routeIds,
      ),
    ]);
    const vehiclesByRoute = new Map<string, any[]>();
    const assignmentsByRoute = new Map<string, any[]>();
    for (const vehicle of vehicles) {
      const list = vehiclesByRoute.get(vehicle.routeId) || [];
      list.push(vehicle);
      vehiclesByRoute.set(vehicle.routeId, list);
    }
    for (const assignment of assignments) {
      const list = assignmentsByRoute.get(assignment.routeId) || [];
      list.push(assignment);
      assignmentsByRoute.set(assignment.routeId, list);
    }
    return routes.map((route: { id: string }) => ({
      ...route,
      vehicles: vehiclesByRoute.get(route.id) || [],
      assignments: assignmentsByRoute.get(route.id) || [],
    }));
  }

  async createRoute(tenantId: string, actorId: string, body: { name: string; stops?: string[]; driverId?: string }) {
    await this.flags.assertEnabled(tenantId, 'ops.transport');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);
    const id = randomToken('trt');
    await runDbQuery(
      ds,
      `INSERT INTO transport_routes (id, name, stops, driver_id, created_at) VALUES (?, ?, ?, ?, NOW())`,
      [id, body.name, JSON.stringify(body.stops || []), body.driverId || null],
    );
    return { id };
  }

  async addVehicle(tenantId: string, actorId: string, body: { plate: string; capacity?: number; routeId?: string }) {
    await this.flags.assertEnabled(tenantId, 'ops.transport');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);
    const id = randomToken('veh');
    await runDbQuery(
      ds,
      `INSERT INTO transport_vehicles (id, plate, capacity, route_id, status, created_at)
       VALUES (?, ?, ?, ?, 'active', NOW())`,
      [id, body.plate, body.capacity ?? 30, body.routeId || null],
    );
    return { id };
  }

  async assignStudent(tenantId: string, actorId: string, body: { routeId: string; studentId: string; pickupStop?: string }) {
    await this.flags.assertEnabled(tenantId, 'ops.transport');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertManager(ds, actorId);
    const student = await runDbQuery(
      ds,
      `SELECT 1 FROM users WHERE id = ? AND roles LIKE '%student%' LIMIT 1`,
      [body.studentId],
    );
    if (!student.length) throw new NotFoundException('Student not found');
    const id = randomToken('tas');
    await runDbQuery(
      ds,
      `INSERT INTO transport_assignments (id, route_id, student_id, pickup_stop, created_at)
       VALUES (?, ?, ?, ?, NOW())`,
      [id, body.routeId, body.studentId, body.pickupStop || null],
    );
    return { id };
  }

  private async assertManager(ds: any, actorId: string) {
    const roles = await this.getUserRoles(ds, actorId);
    if (!roles.some(isTransportManagerRole)) {
      throw new NotFoundException('Transport management is restricted to school administrators');
    }
  }

  private async getUserRoles(ds: any, userId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const row = rows[0];
    if (!row) return [];
    if (typeof row.roles === 'string') {
      try {
        const parsed = JSON.parse(row.roles);
        if (Array.isArray(parsed)) return parsed.map((role: string) => String(role).toLowerCase());
      } catch {
        // Legacy role fallback.
      }
    }
    return row.role ? [String(row.role).toLowerCase()] : [];
  }
}

export function isTransportManagerRole(role: string): boolean {
  return ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff'].includes(role);
}
