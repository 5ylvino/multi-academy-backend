import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../../control-plane/control-plane.service';
import { TenantConnectionService } from '../../database/tenant-connection.service';
import { randomToken } from '../../common/utils/id.util';
import { runDbQuery } from '../../database/db-driver.util';
import { FeatureFlagService } from '../../platform-config/feature-flag.service';

@Injectable()
export class InventoryService {
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
      CREATE TABLE IF NOT EXISTS inventory_items (
        id varchar(64) PRIMARY KEY,
        sku varchar(64) NULL,
        name varchar(255) NOT NULL,
        category varchar(64) NULL,
        qty_on_hand int NOT NULL DEFAULT 0,
        unit_cost decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS inventory_movements (
        id varchar(64) PRIMARY KEY,
        item_id varchar(64) NOT NULL,
        direction varchar(8) NOT NULL,
        qty int NOT NULL DEFAULT 0,
        reason varchar(255) NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async listItems(tenantId: string, actorId: string) {
    await this.flags.assertEnabled(tenantId, 'finance.inventory');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId, false);
    return runDbQuery(
      ds,
      `SELECT id, sku, name, category, qty_on_hand as "qtyOnHand",
              unit_cost as "unitCost", currency, created_at as "createdAt"
       FROM inventory_items ORDER BY created_at DESC LIMIT 200`,
      [],
    );
  }

  async createItem(
    tenantId: string,
    actorId: string,
    body: { sku?: string; name: string; category?: string; qtyOnHand?: number; unitCost?: number; currency?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'finance.inventory');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId, true);
    if (!body.name?.trim() || Number(body.qtyOnHand ?? 0) < 0 || Number(body.unitCost ?? 0) < 0) {
      throw new NotFoundException('Inventory item values are invalid');
    }
    const id = randomToken('inv');
    await runDbQuery(
      ds,
      `INSERT INTO inventory_items (id, sku, name, category, qty_on_hand, unit_cost, currency, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, NOW())`,
      [id, body.sku || null, body.name, body.category || null, body.qtyOnHand ?? 0, body.unitCost ?? 0, body.currency || 'NGN'],
    );
    return { id };
  }

  async recordMovement(
    tenantId: string,
    userId: string,
    body: { itemId: string; direction: 'in' | 'out'; qty: number; reason?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'finance.inventory');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, userId, true);
    const qty = Number(body.qty);
    if (!['in', 'out'].includes(body.direction) || !Number.isInteger(qty) || qty <= 0) {
      throw new NotFoundException('Inventory movement is invalid');
    }
    const itemRows: any[] = await runDbQuery(
      ds,
      `SELECT qty_on_hand as "qtyOnHand" FROM inventory_items WHERE id = ? LIMIT 1`,
      [body.itemId],
    );
    if (!itemRows.length) throw new NotFoundException('Inventory item not found');
    if (body.direction === 'out' && Number(itemRows[0].qtyOnHand) < qty) {
      throw new NotFoundException('Insufficient stock');
    }
    const delta = body.direction === 'in' ? qty : -qty;
    await runDbQuery(
      ds,
      `UPDATE inventory_items SET qty_on_hand = qty_on_hand + ? WHERE id = ?`,
      [delta, body.itemId],
    );
    const id = randomToken('mov');
    await runDbQuery(
      ds,
      `INSERT INTO inventory_movements (id, item_id, direction, qty, reason, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, NOW())`,
      [id, body.itemId, body.direction, qty, body.reason || null, userId],
    );
    return { id };
  }

  async listMovements(tenantId: string, actorId: string, itemId?: string) {
    await this.flags.assertEnabled(tenantId, 'finance.inventory');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    await this.assertAccess(ds, actorId, false);
    if (itemId) {
      return runDbQuery(
        ds,
        `SELECT id, item_id as "itemId", direction, qty, reason, created_by as "createdBy", created_at as "createdAt"
         FROM inventory_movements WHERE item_id = ? ORDER BY created_at DESC LIMIT 100`,
        [itemId],
      );
    }
    return runDbQuery(
      ds,
      `SELECT id, item_id as "itemId", direction, qty, reason, created_by as "createdBy", created_at as "createdAt"
       FROM inventory_movements ORDER BY created_at DESC LIMIT 200`,
      [],
    );
  }

  private async assertAccess(ds: any, actorId: string, write: boolean) {
    const roles = await this.getUserRoles(ds, actorId);
    const allowed = ['director', 'school_admin', 'it_admin', 'principal', 'head_teacher', 'administrative_staff', 'bursar'];
    if (!roles.some((role) => allowed.includes(role))) {
      throw new NotFoundException('Inventory access is restricted to authorized finance staff');
    }
    if (write && roles.includes('principal') === false && roles.includes('head_teacher') === false &&
      roles.includes('bursar') === false && roles.includes('director') === false &&
      roles.includes('school_admin') === false && roles.includes('it_admin') === false &&
      roles.includes('administrative_staff') === false) {
      throw new NotFoundException('Inventory writes are restricted to authorized finance staff');
    }
  }

  private async getUserRoles(ds: any, userId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [userId]);
    const row = rows[0];
    if (!row) return [];
    if (Array.isArray(row.roles)) return row.roles.map((role: string) => String(role).toLowerCase());
    if (typeof row.roles === 'string') {
      try {
        const parsed = JSON.parse(row.roles);
        if (Array.isArray(parsed)) return parsed.map((role: string) => String(role).toLowerCase());
      } catch {}
    }
    return row.role ? [String(row.role).toLowerCase()] : [];
  }
}
