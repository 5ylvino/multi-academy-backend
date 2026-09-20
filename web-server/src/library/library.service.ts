import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

@Injectable()
export class LibraryService {
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
      CREATE TABLE IF NOT EXISTS library_items (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        author varchar(255) NULL,
        isbn varchar(64) NULL,
        copies int NOT NULL DEFAULT 1,
        available int NOT NULL DEFAULT 1,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS library_loans (
        id varchar(64) PRIMARY KEY,
        item_id varchar(64) NOT NULL,
        borrower_id varchar(64) NOT NULL,
        borrowed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        due_at TIMESTAMP NULL,
        returned_at TIMESTAMP NULL
      );
    `);
  }

  async listItems(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'ops.library');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    return runDbQuery(
      ds,
      `SELECT id, title, author, isbn, copies, available, created_at as "createdAt"
       FROM library_items ORDER BY title LIMIT 200`,
      [],
    );
  }

  async listLoans(
    tenantId: string,
    actorId: string,
    status?: 'open' | 'returned',
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.library');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    let where = '';
    if (status === 'open') where = 'WHERE l.returned_at IS NULL';
    else if (status === 'returned') where = 'WHERE l.returned_at IS NOT NULL';
    const roles = await this.getUserRoles(ds, actorId);
    const escapedActor = actorId.replace(/'/g, "''");
    const scope = roles.includes('student')
      ? `l.borrower_id = '${escapedActor}'`
      : roles.includes('parent')
        ? `l.borrower_id IN (SELECT student_id FROM parent_student_links WHERE parent_id = '${escapedActor}')`
        : '';
    if (scope) where += where ? ` AND ${scope}` : `WHERE ${scope}`;
    return runDbQuery(
      ds,
      `SELECT l.id, l.item_id as "itemId", i.title as "itemTitle", l.borrower_id as "borrowerId",
              l.borrowed_at as "borrowedAt", l.due_at as "dueAt", l.returned_at as "returnedAt"
       FROM library_loans l
       LEFT JOIN library_items i ON i.id = l.item_id
       ${where}
       ORDER BY l.borrowed_at DESC LIMIT 200`,
      [],
    );
  }

  async createItem(
    tenantId: string,
    body: { title: string; author?: string; isbn?: string; copies?: number },
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.library');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const id = randomToken('lib');
    const copies = Math.max(1, body.copies || 1);
    await runDbQuery(
      ds,
      `INSERT INTO library_items (id, title, author, isbn, copies, available)
       VALUES (?, ?, ?, ?, ?, ?)`,
      [id, body.title, body.author || null, body.isbn || null, copies, copies],
    );
    return { id, title: body.title, copies, available: copies };
  }

  async loan(
    tenantId: string,
    actorId: string,
    body: { itemId: string; borrowerId?: string; dueAt?: string },
  ) {
    await this.flags.assertEnabled(tenantId, 'ops.library');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const roles = await this.getUserRoles(ds, actorId);
    const borrowerId = resolveBorrowerId(roles, actorId, body.borrowerId);
    if (!borrowerId) throw new NotFoundException('Borrower is required');
    if (roles.includes('parent')) {
      const linked = await runDbQuery(
        ds,
        `SELECT 1 FROM parent_student_links WHERE parent_id = ? AND student_id = ? LIMIT 1`,
        [actorId, borrowerId],
      );
      if (!linked.length) {
        throw new NotFoundException('Borrower is not linked to this parent');
      }
    }
    const items: any[] = await runDbQuery(
      ds,
      `SELECT id, available FROM library_items WHERE id = ?`,
      [body.itemId],
    );
    if (!items.length) throw new NotFoundException('Item not found');
    if ((items[0].available ?? 0) < 1) {
      throw new NotFoundException('No copies available');
    }
    const id = randomToken('loan');
    await runDbQuery(
      ds,
      `INSERT INTO library_loans (id, item_id, borrower_id, due_at) VALUES (?, ?, ?, ?)`,
      [id, body.itemId, borrowerId, body.dueAt || null],
    );
    await runDbQuery(
      ds,
      `UPDATE library_items SET available = available - 1 WHERE id = ?`,
      [body.itemId],
    );
    return { id, itemId: body.itemId, borrowerId };
  }

  async returnLoan(tenantId: string, loanId: string) {
    await this.flags.assertEnabled(tenantId, 'ops.library');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const loans: any[] = await runDbQuery(
      ds,
      `SELECT id, item_id as "itemId", returned_at as "returnedAt" FROM library_loans WHERE id = ?`,
      [loanId],
    );
    if (!loans.length) throw new NotFoundException('Loan not found');
    if (loans[0].returnedAt) return { id: loanId, alreadyReturned: true };
    await runDbQuery(
      ds,
      `UPDATE library_loans SET returned_at = CURRENT_TIMESTAMP WHERE id = ?`,
      [loanId],
    );
    await runDbQuery(
      ds,
      `UPDATE library_items SET available = available + 1 WHERE id = ?`,
      [loans[0].itemId],
    );
    return { id: loanId, returned: true };
  }

  private async getUserRoles(ds: any, userId: string): Promise<string[]> {
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT roles FROM users WHERE id = ? LIMIT 1`,
      [userId],
    );
    const row = rows[0];
    if (!row) return [];
    if (Array.isArray(row.roles)) {
      return row.roles.map((role: string) => String(role).toLowerCase());
    }
    if (typeof row.roles === 'string') {
      try {
        const parsed = JSON.parse(row.roles);
        if (Array.isArray(parsed)) {
          return parsed.map((role: string) => String(role).toLowerCase());
        }
      } catch {
        // Fall through to the legacy role.
      }
    }
    return row.role ? [String(row.role).toLowerCase()] : [];
  }
}

export function resolveBorrowerId(
  roles: string[],
  actorId: string,
  requestedId?: string,
): string | undefined {
  if (roles.includes('student')) return actorId;
  return requestedId;
}
