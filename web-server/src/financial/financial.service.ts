import { BadRequestException, ForbiddenException, Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { NotificationsService } from '../notifications/notifications.service';
import { mapDataToUpdateKeys } from '../util/mapDataToUpdateKeys';
import { getVisibleStudentIds } from '../common/auth/teacher-scope.util';
import { ListCacheService } from '../common/cache/list-cache.service';

@Injectable()
export class FinancialService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly notificationsService: NotificationsService,
    private readonly listCache: ListCacheService,
  ) {}

  private invalidateFinancialLists(tenantId: string) {
    this.listCache.invalidate(`tenant:${tenantId}:financial:`);
  }

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureTables(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_fee_structures (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        description text NULL,
        school_level varchar(32) NOT NULL,
        class_id varchar(64) NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        term varchar(32) NOT NULL,
        due_date varchar(32) NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_payments (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        method varchar(32) NOT NULL,
        reference varchar(255) NULL,
        date varchar(32) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'completed',
        biometric_verified boolean NOT NULL DEFAULT false,
        biometric_assertion_id varchar(128) NULL,
        recorded_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(
      `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS invoice_id varchar(64) NULL`,
    );
    await ds.query(
      `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS receipt_number varchar(64) NULL`,
    );
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_fee_reminders (
        id varchar(64) PRIMARY KEY,
        invoice_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        due_date varchar(32) NULL,
        status varchar(32) NOT NULL DEFAULT 'pending',
        next_send_at TIMESTAMP NULL,
        last_sent_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_invoices (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        fee_structure_id varchar(64) NULL,
        title varchar(255) NOT NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        term varchar(32) NOT NULL,
        session_label varchar(64) NULL,
        status varchar(32) NOT NULL DEFAULT 'unpaid',
        due_date varchar(32) NULL,
        notes text NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_payment_proofs (
        id varchar(64) PRIMARY KEY,
        invoice_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        parent_id varchar(64) NOT NULL,
        file_name varchar(255) NOT NULL,
        mime_type varchar(128) NOT NULL,
        file_data text NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'pending',
        reviewed_by varchar(64) NULL,
        reviewed_at TIMESTAMP NULL,
        review_note text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(
      `CREATE INDEX IF NOT EXISTS idx_financial_payment_proofs_invoice
       ON financial_payment_proofs (invoice_id, created_at DESC)`,
    );
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_installment_plans (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        invoice_id varchar(64) NULL,
        title varchar(255) NOT NULL,
        total_amount decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        status varchar(32) NOT NULL DEFAULT 'active',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_installments (
        id varchar(64) PRIMARY KEY,
        plan_id varchar(64) NOT NULL,
        sequence int NOT NULL DEFAULT 1,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        due_date varchar(32) NULL,
        status varchar(32) NOT NULL DEFAULT 'pending',
        paid_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_scholarships (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        title varchar(255) NOT NULL,
        amount decimal(15,2) NULL,
        percent decimal(7,2) NULL,
        term varchar(32) NOT NULL,
        session_label varchar(64) NULL,
        status varchar(32) NOT NULL DEFAULT 'active',
        notes text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS financial_refunds (
        id varchar(64) PRIMARY KEY,
        payment_id varchar(64) NULL,
        student_id varchar(64) NOT NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        reason text NULL,
        method varchar(32) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'completed',
        processed_by varchar(64) NULL,
        date varchar(32) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    // Backward-compatible column adds for existing tenant DBs
    await ds.query(`ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS biometric_verified boolean NOT NULL DEFAULT false`);
    await ds.query(`ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS biometric_assertion_id varchar(128) NULL`);
    await ds.query(`ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS recorded_by varchar(64) NULL`);
    await ds.query(`ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS installment_id varchar(64) NULL`);
    await ds.query(`ALTER TABLE financial_fee_structures ADD COLUMN IF NOT EXISTS currency varchar(8) NOT NULL DEFAULT 'NGN'`);
    await ds.query(`ALTER TABLE financial_invoices ADD COLUMN IF NOT EXISTS currency varchar(8) NOT NULL DEFAULT 'NGN'`);
    await ds.query(`ALTER TABLE financial_fee_structures ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`);
    await ds.query(`ALTER TABLE financial_fee_structures ADD COLUMN IF NOT EXISTS created_by varchar(64) NULL`);
    await ds.query(`ALTER TABLE financial_invoices ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`);
    await ds.query(`ALTER TABLE financial_invoices ADD COLUMN IF NOT EXISTS payment_plan_type varchar(32) NOT NULL DEFAULT 'full'`);
    await ds.query(`ALTER TABLE financial_scholarships ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`);
    await ds.query(`ALTER TABLE financial_scholarships ADD COLUMN IF NOT EXISTS created_by varchar(64) NULL`);
  }

  async listFeeStructures(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await ds.query(`
      SELECT f.id, f.name, f.description,
             f.school_level as "schoolLevel",
             f.class_id as "classId",
             c.name as "className",
             f.amount, f.currency, f.term,
             f.due_date as "dueDate",
             f.is_active as "isActive",
             f.created_by as "createdBy",
             f.created_at as "createdAt",
             f.updated_at as "updatedAt",
             f.archived_at as "archivedAt",
             creator.name as "createdByName"
      FROM financial_fee_structures f
      LEFT JOIN academic_classes c ON c.id = f.class_id
      LEFT JOIN users creator ON creator.id = f.created_by
      ORDER BY f.created_at DESC
      LIMIT 200
    `);
    return mapDataToUpdateKeys(rows, {
      isactive: 'isActive',
      schoollevel: 'schoolLevel',
      classid: 'classId',
      classname: 'className',
      duedate: 'dueDate',
      createdat: 'createdAt',
      updatedat: 'updatedAt',
      archivedat: 'archivedAt',
      createdby: 'createdBy',
      createdbyname: 'createdByName',
    });
  }

  async createFeeStructure(tenantId: string, body: any, createdBy?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const id = randomToken('fee');
    await runDbQuery(
      ds,
      `INSERT INTO financial_fee_structures (id, name, description, school_level, class_id, amount, currency, term, due_date, is_active, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [id, body.name, body.description || null, body.schoolLevel, body.classId || null, body.amount || 0, (body.currency || 'NGN').toUpperCase(), body.term, body.dueDate || null, !!body.isActive, createdBy || null],
    );
    return { id };
  }

  async updateFeeStructure(tenantId: string, id: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing = await runDbQuery(ds, `SELECT id FROM financial_fee_structures WHERE id = ? LIMIT 1`, [id]);
    if (!existing.length) throw new NotFoundException('Fee structure not found');

    const updates: string[] = [];
    const values: any[] = [];
    const set = (col: string, val: any) => {
      updates.push(`${col} = ?`);
      values.push(val);
    };
    if (body.name !== undefined) set('name', body.name);
    if (body.description !== undefined) set('description', body.description || null);
    if (body.schoolLevel !== undefined) set('school_level', body.schoolLevel);
    if (body.classId !== undefined) set('class_id', body.classId || null);
    if (body.amount !== undefined) set('amount', body.amount);
    if (body.term !== undefined) set('term', body.term);
    if (body.dueDate !== undefined) set('due_date', body.dueDate || null);
    if (body.isActive !== undefined) set('is_active', !!body.isActive);
    if (body.currency !== undefined) set('currency', String(body.currency).toUpperCase());

    if (updates.length === 0) {
      throw new BadRequestException('No fields to update');
    }
    values.push(id);
    await runDbQuery(
      ds,
      `UPDATE financial_fee_structures SET ${updates.join(', ')}, updated_at = NOW() WHERE id = ?`,
      values,
    );
    return { id };
  }

  async deleteFeeStructure(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(ds, `UPDATE financial_fee_structures SET is_active = false, archived_at = NOW(), updated_at = NOW() WHERE id = ?`, [id]);
    return { id };
  }

  async listPayments(tenantId: string) {
    return this.listCache.getOrLoad(`tenant:${tenantId}:financial:payments`, async () => {
      const ds = await this.getTenantDs(tenantId);
      await this.ensureTables(ds);
      return ds.query(`
      SELECT p.id, p.student_id as "studentId", p.invoice_id as "invoiceId",
             p.amount, p.method, p.reference,
             p.receipt_number as "receiptNumber", p.date, p.status,
             p.installment_id as "installmentId", installment.sequence as "installmentSequence",
             CASE WHEN p.installment_id IS NOT NULL THEN 'installment' ELSE 'full' END as "paymentType",
             p.recorded_by as "recordedBy", p.created_at as "createdAt",
             u.name as "studentName", recorder.name as "recordedByName"
      FROM financial_payments p
      LEFT JOIN users u ON u.id = p.student_id
      LEFT JOIN users recorder ON recorder.id = p.recorded_by
      LEFT JOIN financial_installments installment ON installment.id = p.installment_id
      ORDER BY p.created_at DESC
      LIMIT 200
    `);
    });
  }

  async createPayment(
    tenantId: string,
    body: any,
    recordedBy?: { userId: string },
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);

    if (!recordedBy?.userId) {
      throw new BadRequestException('Authenticated recorder is required');
    }

    let invoiceId = body.invoiceId || null;
    let installment: any = null;
    if (invoiceId) {
      const invoiceRows = await runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", amount, status
         FROM financial_invoices WHERE id = ? LIMIT 1`,
        [invoiceId],
      );
      const invoice = invoiceRows[0];
      if (!invoice || String(invoice.studentId) !== String(body.studentId)) {
        throw new NotFoundException('Invoice not found for student');
      }
      if (String(invoice.status).toLowerCase() === 'cancelled') {
        throw new BadRequestException('Cannot record a payment against a cancelled invoice');
      }
    }
    if (body.installmentId) {
      const installmentRows = await runDbQuery(
        ds,
        `SELECT i.id, i.amount, i.status, p.student_id as "studentId",
                p.invoice_id as "invoiceId"
         FROM financial_installments i
         JOIN financial_installment_plans p ON p.id = i.plan_id
         WHERE i.id = ? LIMIT 1`,
        [body.installmentId],
      );
      installment = installmentRows[0];
      if (
        !installment ||
        String(installment.studentId) !== String(body.studentId) ||
        (invoiceId && String(installment.invoiceId) !== String(invoiceId))
      ) {
        throw new NotFoundException('Installment not found for student and invoice');
      }
      if (String(installment.status).toLowerCase() !== 'pending') {
        throw new BadRequestException('This installment is no longer outstanding');
      }
      invoiceId = invoiceId || installment.invoiceId;
    }

    const id = randomToken('pay');
    const receiptNumber = `RCPT-${Date.now().toString().slice(-8)}`;
    await runDbQuery(
      ds,
      `INSERT INTO financial_payments
        (id, student_id, invoice_id, installment_id, amount, method, reference, date, status, biometric_verified, biometric_assertion_id, recorded_by, receipt_number, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, true, ?, ?, ?, NOW())`,
      [
        id,
        body.studentId,
        invoiceId,
        body.installmentId || null,
        body.amount || 0,
        body.method,
        body.reference || null,
        body.date,
        body.status || 'completed',
        null,
        recordedBy.userId,
        receiptNumber,
      ],
    );

    if (body.installmentId) {
      await runDbQuery(
        ds,
        `UPDATE financial_installments SET status = 'paid', paid_at = NOW() WHERE id = ?`,
        [body.installmentId],
      );
    }

    if (invoiceId) {
      const invoiceRows = await runDbQuery(
        ds,
        `SELECT amount FROM financial_invoices WHERE id = ? LIMIT 1`,
        [invoiceId],
      );
      const paidRows = await runDbQuery(
        ds,
        `SELECT COALESCE(SUM(amount), 0) as "paidAmount"
         FROM financial_payments
         WHERE invoice_id = ? AND status = 'completed'`,
        [invoiceId],
      );
      const pendingInstallmentRows = await runDbQuery(
        ds,
        `SELECT COUNT(*) as "pendingCount"
         FROM financial_installments i
         JOIN financial_installment_plans p ON p.id = i.plan_id
         WHERE p.invoice_id = ? AND i.status = 'pending'`,
        [invoiceId],
      );
      const invoiceAmount = Number(invoiceRows[0]?.amount || 0);
      const paidAmount = Number(paidRows[0]?.paidAmount || 0);
      const pendingCount = Number(pendingInstallmentRows[0]?.pendingCount || 0);
      const status =
        pendingCount > 0 && paidAmount < invoiceAmount
          ? paidAmount > 0
            ? 'partial'
            : 'unpaid'
          : paidAmount >= invoiceAmount
            ? 'paid'
            : 'partial';
      await runDbQuery(
        ds,
        `UPDATE financial_invoices SET status = ?, updated_at = NOW() WHERE id = ?`,
        [status, invoiceId],
      );
      if (status === 'paid') {
        await runDbQuery(
          ds,
          `UPDATE financial_fee_reminders SET status = 'stopped' WHERE invoice_id = ? AND status = 'pending'`,
          [invoiceId],
        );
      }
    }

    const parents = await runDbQuery(
      ds,
      `SELECT parent_id as "parentId" FROM parent_student_links WHERE student_id = ?`,
      [body.studentId],
    );
    for (const parent of parents as any[]) {
      await this.notificationsService.create({
        tenantId,
        userId: parent.parentId || parent.parentid,
        title: 'Fee payment recorded',
        message: `Payment of ${body.amount} received. Receipt ${receiptNumber}.`,
        type: 'success',
        href: '/dashboard/parent',
      });
    }

    // Notify the recording user (in-app)
    await this.notificationsService.create({
      tenantId,
      userId: recordedBy.userId,
      title: 'Payment recorded',
      message: `Payment of ${body.amount} was recorded. Receipt ${receiptNumber}.`,
      type: 'success',
      href: '/dashboard/financial',
    });

    this.invalidateFinancialLists(tenantId);
    return { id, biometricVerified: true, receiptNumber };
  }

  async deletePayment(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(ds, `UPDATE financial_payments SET status = 'archived' WHERE id = ?`, [id]);
    this.invalidateFinancialLists(tenantId);
    return { id };
  }

  // ─── Invoices ─────────────────────────────────────────────────────────────

  async listInvoices(tenantId: string) {
    return this.listCache.getOrLoad(`tenant:${tenantId}:financial:invoices`, async () => {
      const ds = await this.getTenantDs(tenantId);
      await this.ensureTables(ds);
      const rows: any[] = await ds.query(`
      SELECT i.id, i.student_id as "studentId", i.fee_structure_id as "feeStructureId",
             i.title, i.amount, i.currency, i.term, i.session_label as "sessionLabel", i.status,
             i.payment_plan_type as "paymentPlanType",
             i.due_date as "dueDate", i.notes, i.created_by as "createdBy",
             i.created_at as "createdAt", u.name as "studentName",
             creator.name as "createdByName"
      FROM financial_invoices i
      LEFT JOIN users u ON u.id = i.student_id
      LEFT JOIN users creator ON creator.id = i.created_by
      ORDER BY i.created_at DESC
      LIMIT 200
    `);
    if (!rows.length) return rows;
    const ids = rows.map((row) => row.id);
    const placeholders = ids.map(() => '?').join(',');
    const proofs: any[] = await runDbQuery(
      ds,
      `SELECT invoice_id as "invoiceId", file_name as "fileName",
              mime_type as "mimeType", status, created_at as "createdAt"
       FROM financial_payment_proofs
       WHERE invoice_id IN (${placeholders})
       ORDER BY created_at DESC`,
      ids,
    );
    const latestProof = new Map<string, any>();
    for (const proof of proofs) {
      if (!latestProof.has(String(proof.invoiceId))) {
        latestProof.set(String(proof.invoiceId), proof);
      }
    }
    return rows.map((row) => ({
      ...row,
      proof: latestProof.get(String(row.id)) || null,
    }));
    });
  }

  async createInvoice(tenantId: string, body: any, createdBy?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const id = randomToken('inv');
    await runDbQuery(
      ds,
      `INSERT INTO financial_invoices
        (id, student_id, fee_structure_id, title, amount, currency, term, session_label, status, payment_plan_type, due_date, notes, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        body.studentId,
        body.feeStructureId || null,
        body.title,
        body.amount,
        (body.currency || 'NGN').toUpperCase(),
        body.term,
        body.sessionLabel || null,
        body.status || 'unpaid',
        body.paymentPlanType || 'full',
        body.dueDate || null,
        body.notes || null,
        createdBy || null,
      ],
    );
    await runDbQuery(
      ds,
      `INSERT INTO financial_fee_reminders
        (id, invoice_id, student_id, due_date, status, next_send_at, created_at)
       VALUES (?, ?, ?, ?, 'pending', NOW(), NOW())`,
      [randomToken('frm'), id, body.studentId, body.dueDate || null],
    );
    this.invalidateFinancialLists(tenantId);
    return { id };
  }

  async updateInvoice(tenantId: string, id: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing = await runDbQuery(ds, `SELECT id FROM financial_invoices WHERE id = ? LIMIT 1`, [id]);
    if (!existing.length) throw new NotFoundException('Invoice not found');

    const updates: string[] = [];
    const values: any[] = [];
    const set = (col: string, val: any) => {
      updates.push(`${col} = ?`);
      values.push(val);
    };
    if (body.status !== undefined) set('status', body.status);
    if (body.paymentPlanType !== undefined) set('payment_plan_type', body.paymentPlanType);
    if (body.amount !== undefined) set('amount', body.amount);
    if (body.notes !== undefined) set('notes', body.notes || null);

    if (updates.length === 0) {
      throw new BadRequestException('No fields to update');
    }
    values.push(id);
    await runDbQuery(
      ds,
      `UPDATE financial_invoices SET ${updates.join(', ')}, updated_at = NOW() WHERE id = ?`,
      values,
    );
    this.invalidateFinancialLists(tenantId);
    return { id };
  }

  async deleteInvoice(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing = await runDbQuery(
      ds,
      `SELECT status FROM financial_invoices WHERE id = ? LIMIT 1`,
      [id],
    );
    if (!existing.length) throw new NotFoundException('Invoice not found');
    const status = String(existing[0].status || '').toLowerCase();
    if (status === 'paid' || status === 'pending' || status === 'partial') {
      throw new BadRequestException(
        'Paid, partial, or pending invoices cannot be deleted',
      );
    }
    const proofRows = await runDbQuery(
      ds,
      `SELECT status FROM financial_payment_proofs
       WHERE invoice_id = ? ORDER BY created_at DESC LIMIT 1`,
      [id],
    );
    if (String(proofRows[0]?.status || '').toLowerCase() === 'pending') {
      throw new BadRequestException(
        'An invoice with a payment proof pending review cannot be deleted',
      );
    }
    if (status === 'unpaid' && proofRows.length > 0) {
      throw new BadRequestException(
        'An unpaid invoice with payment proof cannot be deleted',
      );
    }
    await runDbQuery(ds, `UPDATE financial_invoices SET status = 'cancelled', archived_at = NOW(), updated_at = NOW() WHERE id = ?`, [id]);
    this.invalidateFinancialLists(tenantId);
    return { id };
  }

  async getInvoicePaymentProof(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const rows = await runDbQuery(
      ds,
      `SELECT id, invoice_id as "invoiceId", file_name as "fileName",
              mime_type as "mimeType", file_data as "fileData",
              status, review_note as "reviewNote", created_at as "createdAt"
       FROM financial_payment_proofs
       WHERE invoice_id = ?
       ORDER BY created_at DESC LIMIT 1`,
      [id],
    );
    if (!rows.length) throw new NotFoundException('No payment proof found for invoice');
    return rows[0];
  }

  // ─── Scholarships ─────────────────────────────────────────────────────────

  async listScholarships(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    return ds.query(`
      SELECT s.id, s.student_id as "studentId", s.title, s.amount, s.percent, s.term,
             s.session_label as sessionLabel, s.status, s.notes,
             s.created_by as "createdBy", s.created_at as "createdAt",
             u.name as "studentName", creator.name as "createdByName"
      FROM financial_scholarships s
      LEFT JOIN users u ON u.id = s.student_id
      LEFT JOIN users creator ON creator.id = s.created_by
      ORDER BY s.created_at DESC
      LIMIT 200
    `);
  }

  async createScholarship(tenantId: string, body: any, createdBy?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    if (body.amount == null && body.percent == null) {
      throw new BadRequestException('Either amount or percent is required');
    }
    const id = randomToken('sch');
    await runDbQuery(
      ds,
      `INSERT INTO financial_scholarships
        (id, student_id, title, amount, percent, term, session_label, status, notes, created_by, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
      [
        id,
        body.studentId,
        body.title,
        body.amount ?? null,
        body.percent ?? null,
        body.term,
        body.sessionLabel || null,
        body.status || 'active',
        body.notes || null,
        createdBy || null,
      ],
    );
    return { id };
  }

  async updateScholarship(tenantId: string, id: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const existing = await runDbQuery(ds, `SELECT id FROM financial_scholarships WHERE id = ? LIMIT 1`, [id]);
    if (!existing.length) throw new NotFoundException('Scholarship not found');

    const updates: string[] = [];
    const values: any[] = [];
    const set = (col: string, val: any) => {
      updates.push(`${col} = ?`);
      values.push(val);
    };
    if (body.title !== undefined) set('title', body.title);
    if (body.amount !== undefined) set('amount', body.amount);
    if (body.percent !== undefined) set('percent', body.percent);
    if (body.term !== undefined) set('term', body.term);
    if (body.sessionLabel !== undefined) set('session_label', body.sessionLabel || null);
    if (body.status !== undefined) set('status', body.status);
    if (body.notes !== undefined) set('notes', body.notes || null);

    if (updates.length === 0) {
      throw new BadRequestException('No fields to update');
    }
    values.push(id);
    await runDbQuery(
      ds,
      `UPDATE financial_scholarships SET ${updates.join(', ')}, updated_at = NOW() WHERE id = ?`,
      values,
    );
    return { id };
  }

  async deleteScholarship(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    await runDbQuery(ds, `UPDATE financial_scholarships SET status = 'ended', archived_at = NOW(), updated_at = NOW() WHERE id = ?`, [id]);
    return { id };
  }

  // ─── Refunds ──────────────────────────────────────────────────────────────

  async listRefunds(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    return ds.query(`
      SELECT r.id, r.payment_id as "paymentId", r.student_id as "studentId", r.amount,
             r.reason, r.method, r.status, r.processed_by as "processedBy", r.date,
             r.created_at as "createdAt", u.name as "studentName",
             processor.name as "processedByName"
      FROM financial_refunds r
      LEFT JOIN users u ON u.id = r.student_id
      LEFT JOIN users processor ON processor.id = r.processed_by
      ORDER BY r.created_at DESC
      LIMIT 200
    `);
  }

  async createRefund(tenantId: string, body: any, processedBy?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    const id = randomToken('ref');
    await runDbQuery(
      ds,
      `INSERT INTO financial_refunds
        (id, payment_id, student_id, amount, reason, method, status, processed_by, date, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())`,
      [
        id,
        body.paymentId || null,
        body.studentId,
        body.amount,
        body.reason || null,
        body.method,
        body.status || 'completed',
        processedBy || null,
        body.date,
      ],
    );
    return { id };
  }

  async getStudentHistory(tenantId: string, studentId: string, actorUserId?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(ds);
    if (actorUserId) {
      const visibleStudentIds = await getVisibleStudentIds(ds, actorUserId);
      if (visibleStudentIds && !visibleStudentIds.includes(studentId)) {
        throw new ForbiddenException('You are not allowed to view this student history');
      }
    }
    const [payments, invoices, scholarships, refunds] = await Promise.all([
      runDbQuery(ds, `SELECT id, 'payment' as "recordType", amount, method, reference,
        date, status, created_at as "recordedAt" FROM financial_payments
        WHERE student_id = ? ORDER BY created_at ASC`, [studentId]),
      runDbQuery(ds, `SELECT id, 'invoice' as "recordType", amount, currency, title,
        term, session_label as "sessionLabel", status, due_date as "dueDate",
        created_at as "recordedAt" FROM financial_invoices
        WHERE student_id = ? ORDER BY created_at ASC`, [studentId]),
      runDbQuery(ds, `SELECT id, 'scholarship' as "recordType", amount, percent,
        title, term, session_label as "sessionLabel", status,
        created_at as "recordedAt" FROM financial_scholarships
        WHERE student_id = ? ORDER BY created_at ASC`, [studentId]),
      runDbQuery(ds, `SELECT id, 'refund' as "recordType", amount, method, reason,
        date, status, created_at as "recordedAt" FROM financial_refunds
        WHERE student_id = ? ORDER BY created_at ASC`, [studentId]),
    ]);
    return {
      studentId,
      records: [...payments, ...invoices, ...scholarships, ...refunds]
        .sort((a: any, b: any) => new Date(a.recordedAt || a.date).getTime() - new Date(b.recordedAt || b.date).getTime()),
    };
  }
}
