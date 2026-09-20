import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
  StreamableFile,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { FinancialService } from '../financial/financial.service';
import { GatewayPaymentsService } from '../financial/gateway-payments.service';
import { BalanceFreezeService } from '../financial/balance-freeze.service';
import { AcademicService } from '../academic/academic.service';
import { ReportCardsService } from '../academic/report-cards.service';
import { hasPortalRole } from './portal-role.util';
import { StudentPortalService } from './student-portal.service';
import { PortalReadServiceClient } from './portal-read-service.client';

@Injectable()
export class ParentPortalService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly financial: FinancialService,
    private readonly gateway: GatewayPaymentsService,
    private readonly balanceFreeze: BalanceFreezeService,
    private readonly academic: AcademicService,
    private readonly reportCards: ReportCardsService,
    private readonly studentPortal: StudentPortalService,
    private readonly portalRead: PortalReadServiceClient,
  ) {}

  private async getDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async assertParent(tenantId: string, parentId: string) {
    await this.flags.assertEnabled(tenantId, 'portal.parent');
    const ds = await this.getDs(tenantId);
    const rows = await runDbQuery(
      ds,
      `SELECT id, roles FROM users WHERE id = ? LIMIT 1`,
      [parentId],
    );
    if (!rows.length) throw new NotFoundException('User not found');
    // Portal access is deliberately role-owned. Administrative capabilities
    // must not become a substitute for a parent/ward relationship.
    const allowed = hasPortalRole(rows[0].role, rows[0].roles, 'parent');
    if (!allowed) {
      throw new ForbiddenException('Parent portal requires parent role');
    }
  }

  private async assertNotFrozen(tenantId: string, studentId: string) {
    const balance = await this.balanceFreeze.computeStudentBalance(
      tenantId,
      studentId,
    );
    if (balance.frozen) {
      throw new ForbiddenException(
        balance.message || 'Portal frozen due to fee arrears',
      );
    }
    return balance;
  }

  async getBalanceStatus(tenantId: string, parentId: string) {
    await this.assertParent(tenantId, parentId);
    return this.balanceFreeze.computeParentBalance(tenantId, parentId);
  }

  async listWards(tenantId: string, parentId: string) {
    await this.assertParent(tenantId, parentId);
    const ds = await this.getDs(tenantId);
    const wards: any[] = await runDbQuery(
      ds,
      `SELECT u.id, u.name, u.email
       FROM parent_student_links l
       JOIN users u ON u.id = l.student_id
       WHERE l.parent_id = ?
       ORDER BY u.name`,
      [parentId],
    );
    if (wards.length) {
      try {
        const ids = wards.map((ward) => ward.id);
        const placeholders = ids.map(() => '?').join(',');
        const enrollments: any[] = await runDbQuery(
          ds,
          `SELECT student_id as "studentId", class_id as "classId"
           FROM student_class_enrollments
           WHERE student_id IN (${placeholders})`,
          ids,
        );
        const classIdsByStudent = new Map<string, string[]>();
        for (const row of enrollments) {
          if (!row.classId) continue;
          const key = String(row.studentId);
          const list = classIdsByStudent.get(key) || [];
          list.push(String(row.classId));
          classIdsByStudent.set(key, list);
        }
        for (const ward of wards) {
          ward.classIds = classIdsByStudent.get(String(ward.id)) || [];
        }
      } catch {
        for (const ward of wards) {
          ward.classIds = [];
        }
      }
    }
    const balance = await this.balanceFreeze.computeParentBalance(
      tenantId,
      parentId,
    );
    return {
      wards,
      frozen: balance.frozen,
      freezeMessage: balance.message,
      balance: balance.wards,
    };
  }

  private async assertWard(
    tenantId: string,
    parentId: string,
    studentId: string,
  ) {
    await this.assertParent(tenantId, parentId);
    const ds = await this.getDs(tenantId);
    const links = await runDbQuery(
      ds,
      `SELECT 1 FROM parent_student_links WHERE parent_id = ? AND student_id = ? LIMIT 1`,
      [parentId, studentId],
    );
    if (!links.length) {
      throw new ForbiddenException('Student is not linked to this parent');
    }
  }

  private async ensurePaymentProofsTable(ds: any) {
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
  }

  /** Parent portal landing — delegates to portal-read service when configured. */
  async getHome(tenantId: string, parentId: string) {
    if (this.portalRead.isEnabled()) {
      return this.portalRead.get(
        'parent/home',
        tenantId,
        { userId: parentId, roles: ['parent'] },
        ['portal.parent'],
      );
    }
    return this.getHomeLocal(tenantId, parentId);
  }

  /** Parent portal landing — wards list plus optional attendance alerts. */
  async getHomeLocal(tenantId: string, parentId: string) {
    const alertsOn = await this.flags.resolve(
      tenantId,
      'attendance.absence_alerts',
    );
    const [wardsPayload, alertsPayload] = await Promise.all([
      this.listWards(tenantId, parentId),
      alertsOn
        ? this.attendanceAlerts(tenantId, parentId).catch(() => ({ items: [] }))
        : Promise.resolve({ items: [] as any[] }),
    ]);
    return {
      ...wardsPayload,
      attendanceAlerts: alertsPayload.items,
    };
  }

  /** Aggregated ward dashboard — delegates to portal-read when configured. */
  async getWardOverview(tenantId: string, parentId: string, studentId: string) {
    if (this.portalRead.isEnabled()) {
      return this.portalRead.get(
        `parent/wards/${encodeURIComponent(studentId)}/overview`,
        tenantId,
        { userId: parentId, roles: ['parent'] },
        ['portal.parent'],
      );
    }
    return this.getWardOverviewLocal(tenantId, parentId, studentId);
  }

  /** Aggregated ward dashboard — one round trip, one ownership check. */
  async getWardOverviewLocal(
    tenantId: string,
    parentId: string,
    studentId: string,
  ) {
    const access = await this.openWardAccess(tenantId, parentId, studentId);
    const frozen = Boolean(access.balance.frozen);
    const freezeMessage = access.balance.message;

    await this.ensurePaymentProofsTable(access.ds);

    const [invoices, payments, installments, proofs, results, history, attendance, assignments, schedule] =
      await Promise.all([
        this.financial.listInvoices(tenantId),
        this.financial.listPayments(tenantId),
        this.gateway.listInstallmentPlans(tenantId, studentId),
        runDbQuery(
          access.ds,
          `SELECT id, invoice_id as "invoiceId", file_name as "fileName",
                  mime_type as "mimeType", status, review_note as "reviewNote",
                  created_at as "createdAt"
           FROM financial_payment_proofs
           WHERE student_id = ? AND parent_id = ?
           ORDER BY created_at DESC`,
          [studentId, parentId],
        ),
        frozen
          ? Promise.resolve([])
          : this.queryWardResults(access.ds, studentId),
        frozen
          ? Promise.resolve([])
          : this.queryWardHistory(access.ds, studentId),
        frozen
          ? Promise.resolve(null)
          : this.queryWardAttendanceSummary(access.ds, studentId),
        this.studentPortal.getAssignmentsForStudent(tenantId, studentId),
        this.studentPortal.getScheduleForStudent(tenantId, studentId),
      ]);

    const studentInvoices = (invoices as any[]).filter(
      (i) => String(i.studentId) === String(studentId),
    );
    const studentPayments = (payments as any[]).filter(
      (p) => String(p.studentId) === String(studentId),
    );

    const fees = {
      frozen,
      freezeMessage,
      balance: access.balance,
      invoices: studentInvoices,
      payments: studentPayments,
      installments,
      proofs,
    };

    return {
      fees,
      results: { frozen, freezeMessage, results },
      history: { frozen, freezeMessage, studentId, history },
      attendance: frozen
        ? { frozen, freezeMessage, summary: null }
        : { frozen: false, summary: attendance },
      assignments,
      schedule,
      installments,
    };
  }

  private async openWardAccess(
    tenantId: string,
    parentId: string,
    studentId: string,
  ) {
    await this.assertWard(tenantId, parentId, studentId);
    const [ds, balance] = await Promise.all([
      this.getDs(tenantId),
      this.balanceFreeze.computeStudentBalance(tenantId, studentId),
    ]);
    return { ds, balance };
  }

  private async queryWardResults(ds: any, studentId: string) {
    const results = await runDbQuery(
      ds,
      `SELECT agr.id, agr.session_id as "sessionId", agr.term_id as "termId",
          agr.class_id as "classId", agr.student_name as "studentName",
          agr.student_class_name as "className", agr.admission_no as "admissionNo",
          agr.student_subject_records as "studentSubjectRecords",
          agr.release_status as "releaseStatus", agr.generated_at as "generatedAt",
          ses.name as "sessionName", trm.name as "termName"
       FROM academic_generated_results agr
       LEFT JOIN academic_sessions ses ON ses.id = agr.session_id
       LEFT JOIN academic_terms trm ON trm.id = agr.term_id
       WHERE agr.student_id = ? AND agr.release_status = 'approved'
       ORDER BY ses.start_date DESC NULLS LAST, trm.start_date DESC NULLS LAST`,
      [studentId],
    );
    results.forEach((row: any) => {
      row.studentSubjectRecords =
        typeof row.studentSubjectRecords === 'string'
          ? JSON.parse(row.studentSubjectRecords)
          : row.studentSubjectRecords || [];
    });
    return results;
  }

  private async queryWardHistory(ds: any, studentId: string) {
    const rows = await runDbQuery(
      ds,
      `SELECT agr.id, agr.student_id as "studentId", agr.class_id as "classId",
              agr.student_class_name as "className", agr.term_id as "termId",
              trm.name as "termName", ses.name as "sessionName",
              agr.student_subject_records as "studentSubjectRecords",
              agr.release_status as "status", agr.generated_at as "generatedAt"
       FROM academic_generated_results agr
       LEFT JOIN academic_terms trm ON trm.id = agr.term_id
       LEFT JOIN academic_sessions ses ON ses.id = agr.session_id
       WHERE agr.student_id = ? AND agr.release_status = 'approved'
       ORDER BY ses.start_date NULLS LAST, trm.start_date NULLS LAST`,
      [studentId],
    );
    rows.forEach((row: any) => {
      row.studentSubjectRecords =
        typeof row.studentSubjectRecords === 'string'
          ? JSON.parse(row.studentSubjectRecords)
          : row.studentSubjectRecords || [];
    });
    return rows;
  }

  private async queryWardAttendanceSummary(ds: any, studentId: string) {
    const since = new Date(Date.now() - 30 * 86400000)
      .toISOString()
      .slice(0, 10);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT
         SUM(CASE WHEN LOWER(status) = 'present' THEN 1 ELSE 0 END) as "presentDays",
         SUM(CASE WHEN LOWER(status) = 'absent' THEN 1 ELSE 0 END) as "absentDays",
         SUM(CASE WHEN LOWER(status) = 'late' THEN 1 ELSE 0 END) as "lateDays",
         COUNT(*) as "totalMarked"
       FROM attendance_students
       WHERE student_id = ? AND date >= ?`,
      [studentId, since],
    );
    const summary = rows?.[0] || {
      presentDays: 0,
      absentDays: 0,
      lateDays: 0,
      totalMarked: 0,
    };
    return { ...summary, windowDays: 30 };
  }

  async wardFees(tenantId: string, parentId: string, studentId: string) {
    await this.assertWard(tenantId, parentId, studentId);
    const balance = await this.balanceFreeze.computeStudentBalance(
      tenantId,
      studentId,
    );
    const invoices = await this.financial.listInvoices(tenantId);
    const payments = await this.financial.listPayments(tenantId);
    const installments = await this.gateway.listInstallmentPlans(tenantId, studentId);
    const ds = await this.getDs(tenantId);
    await this.ensurePaymentProofsTable(ds);
    const proofs = await runDbQuery(
      ds,
      `SELECT id, invoice_id as "invoiceId", file_name as "fileName",
              mime_type as "mimeType", status, review_note as "reviewNote",
              created_at as "createdAt"
       FROM financial_payment_proofs
       WHERE student_id = ? AND parent_id = ?
       ORDER BY created_at DESC`,
      [studentId, parentId],
    );

    return {
      frozen: balance.frozen,
      freezeMessage: balance.message,
      balance,
      invoices: (invoices as any[]).filter(
        (i) => String(i.studentId) === String(studentId),
      ),
      payments: (payments as any[]).filter(
        (p) => String(p.studentId) === String(studentId),
      ),
      installments,
      proofs,
    };
  }

  async wardResults(tenantId: string, parentId: string, studentId: string) {
    await this.assertWard(tenantId, parentId, studentId);
    const balance = await this.balanceFreeze.computeStudentBalance(
      tenantId,
      studentId,
    );
    if (balance.frozen) {
      return { frozen: true, freezeMessage: balance.message, results: [] };
    }
    const ds = await this.getDs(tenantId);
    const results = await runDbQuery(
      ds,
      `SELECT agr.id, agr.session_id as "sessionId", agr.term_id as "termId",
          agr.class_id as "classId", agr.student_name as "studentName",
          agr.student_class_name as "className", agr.admission_no as "admissionNo",
          agr.student_subject_records as "studentSubjectRecords",
          agr.release_status as "releaseStatus", agr.generated_at as "generatedAt",
          ses.name as "sessionName", trm.name as "termName"
       FROM academic_generated_results agr
       LEFT JOIN academic_sessions ses ON ses.id = agr.session_id
       LEFT JOIN academic_terms trm ON trm.id = agr.term_id
       WHERE agr.student_id = ? AND agr.release_status = 'approved'
       ORDER BY ses.start_date DESC NULLS LAST, trm.start_date DESC NULLS LAST`,
      [studentId],
    );
    results.forEach((row: any) => {
      row.studentSubjectRecords = typeof row.studentSubjectRecords === 'string'
        ? JSON.parse(row.studentSubjectRecords)
        : row.studentSubjectRecords || [];
    });
    return { frozen: false, results };
  }

  async wardHistory(tenantId: string, parentId: string, studentId: string) {
    await this.assertWard(tenantId, parentId, studentId);
    const balance = await this.balanceFreeze.computeStudentBalance(
      tenantId,
      studentId,
    );
    if (balance.frozen) {
      return {
        frozen: true,
        freezeMessage: balance.message,
        studentId,
        history: [],
      };
    }
    const ds = await this.getDs(tenantId);
    const rows = await runDbQuery(
      ds,
      `SELECT agr.id, agr.student_id as "studentId", agr.class_id as "classId",
              agr.student_class_name as "className", agr.term_id as "termId",
              trm.name as "termName", ses.name as "sessionName",
              agr.student_subject_records as "studentSubjectRecords",
              agr.release_status as "status", agr.generated_at as "generatedAt"
       FROM academic_generated_results agr
       LEFT JOIN academic_terms trm ON trm.id = agr.term_id
       LEFT JOIN academic_sessions ses ON ses.id = agr.session_id
       WHERE agr.student_id = ? AND agr.release_status = 'approved'
       ORDER BY ses.start_date NULLS LAST, trm.start_date NULLS LAST`,
      [studentId],
    );
    rows.forEach((row: any) => {
      row.studentSubjectRecords = typeof row.studentSubjectRecords === 'string'
        ? JSON.parse(row.studentSubjectRecords)
        : row.studentSubjectRecords || [];
    });
    return { frozen: false, studentId, history: rows };
  }

  async wardAttendanceSummary(
    tenantId: string,
    parentId: string,
    studentId: string,
  ) {
    await this.assertWard(tenantId, parentId, studentId);
    const balance = await this.balanceFreeze.computeStudentBalance(
      tenantId,
      studentId,
    );
    if (balance.frozen) {
      return { frozen: true, freezeMessage: balance.message, summary: null };
    }
    const ds = await this.getDs(tenantId);
    const since = new Date(Date.now() - 30 * 86400000)
      .toISOString()
      .slice(0, 10);
    const rows: any[] = await runDbQuery(
      ds,
      `SELECT
         SUM(CASE WHEN LOWER(status) = 'present' THEN 1 ELSE 0 END) as "presentDays",
         SUM(CASE WHEN LOWER(status) = 'absent' THEN 1 ELSE 0 END) as "absentDays",
         SUM(CASE WHEN LOWER(status) = 'late' THEN 1 ELSE 0 END) as "lateDays",
         COUNT(*) as "totalMarked"
       FROM attendance_students
       WHERE student_id = ? AND date >= ?`,
      [studentId, since],
    );
    const summary = rows?.[0] || {
      presentDays: 0,
      absentDays: 0,
      lateDays: 0,
      totalMarked: 0,
    };
    return { frozen: false, summary: { ...summary, windowDays: 30 } };
  }

  async wardAssignments(tenantId: string, parentId: string, studentId: string) {
    await this.assertWard(tenantId, parentId, studentId);
    return this.studentPortal.getAssignmentsForStudent(tenantId, studentId);
  }

  async wardSchedule(tenantId: string, parentId: string, studentId: string) {
    await this.assertWard(tenantId, parentId, studentId);
    return this.studentPortal.getScheduleForStudent(tenantId, studentId);
  }

  async attendanceAlerts(tenantId: string, parentId: string, limit = 50) {
    await this.assertParent(tenantId, parentId);
    await this.flags.assertEnabled(tenantId, 'attendance.absence_alerts');
    const ds = await this.getDs(tenantId);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS attendance_absence_alerts_sent (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        absence_count int NOT NULL,
        sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    const safeLimit = Math.min(Math.max(Number(limit) || 50, 1), 100);
    const rows = await runDbQuery(
      ds,
      `SELECT a.id, a.student_id as "studentId", u.name as "studentName",
              a.absence_count as "absenceCount", a.sent_at as "sentAt"
       FROM attendance_absence_alerts_sent a
       JOIN parent_student_links l ON l.student_id = a.student_id AND l.parent_id = ?
       LEFT JOIN users u ON u.id = a.student_id
       ORDER BY a.sent_at DESC LIMIT ${safeLimit}`,
      [parentId],
    );
    return { items: rows };
  }

  async wardReportCardPdf(
    tenantId: string,
    parentId: string,
    studentId: string,
    termId: string,
  ): Promise<StreamableFile> {
    await this.assertWard(tenantId, parentId, studentId);
    await this.assertNotFrozen(tenantId, studentId);
    await this.flags.assertEnabled(tenantId, 'academic.report_cards');
    const buf = await this.reportCards.pdf(tenantId, studentId, termId);
    return new StreamableFile(Uint8Array.from(buf));
  }

  async wardReportCardData(
    tenantId: string,
    parentId: string,
    studentId: string,
    termId: string,
  ) {
    await this.assertWard(tenantId, parentId, studentId);
    const balance = await this.balanceFreeze.computeStudentBalance(
      tenantId,
      studentId,
    );
    if (balance.frozen) {
      return { frozen: true, freezeMessage: balance.message, data: null };
    }
    await this.flags.assertEnabled(tenantId, 'academic.report_cards');
    const data = await this.reportCards.getData(tenantId, studentId, termId);
    return { frozen: false, data };
  }

  async wardTranscriptPdf(
    tenantId: string,
    parentId: string,
    studentId: string,
  ): Promise<StreamableFile> {
    await this.assertWard(tenantId, parentId, studentId);
    await this.assertNotFrozen(tenantId, studentId);
    const buf = await this.reportCards.transcriptPdf(tenantId, studentId);
    return new StreamableFile(Uint8Array.from(buf));
  }

  async startPay(
    tenantId: string,
    parentId: string,
    body: {
      studentId: string;
      invoiceId?: string;
      installmentId?: string;
      amount: number;
      email: string;
      callbackUrl: string;
      kind?: 'invoice' | 'advance' | 'installment';
      term?: string;
      sessionLabel?: string;
    },
  ) {
    await this.assertWard(tenantId, parentId, body.studentId);
    if (body.kind !== 'invoice' && body.kind !== 'installment' && !body.installmentId) {
      await this.assertNotFrozen(tenantId, body.studentId);
    }
    return this.gateway.createCheckout(
      tenantId,
      {
        studentId: body.studentId,
        invoiceId: body.invoiceId,
        installmentId: body.installmentId,
        amount: body.amount,
        email: body.email,
        callbackUrl: body.callbackUrl,
        kind: body.kind || (body.installmentId ? 'installment' : 'invoice'),
        term: body.term,
        sessionLabel: body.sessionLabel,
      },
      parentId,
    );
  }

  async wardInstallments(tenantId: string, parentId: string, studentId: string) {
    await this.assertWard(tenantId, parentId, studentId);
    return this.gateway.listInstallmentPlans(tenantId, studentId);
  }

  async submitPaymentProof(
    tenantId: string,
    parentId: string,
    studentId: string,
    body: {
      invoiceId: string;
      fileName: string;
      mimeType: string;
      data: string;
    },
  ) {
    await this.assertWard(tenantId, parentId, studentId);
    const mimeType = String(body.mimeType || '').toLowerCase().trim();
    if (mimeType !== 'application/pdf' && !mimeType.startsWith('image/')) {
      throw new BadRequestException('Only PDF or image files are accepted');
    }

    const rawData = String(body.data || '').trim();
    const base64 = rawData.replace(/^data:[^;]+;base64,/, '');
    if (
      !base64 ||
      base64.length > 14_000_000 ||
      !/^[A-Za-z0-9+/]*={0,2}$/.test(base64) ||
      base64.length % 4 === 1
    ) {
      throw new BadRequestException('Proof must be a valid base64 file');
    }

    const ds = await this.getDs(tenantId);
    await this.ensurePaymentProofsTable(ds);
    const invoiceRows = await runDbQuery(
      ds,
      `SELECT id, status FROM financial_invoices
       WHERE id = ? AND student_id = ? LIMIT 1`,
      [body.invoiceId, studentId],
    );
    const invoice = invoiceRows[0];
    if (!invoice) throw new NotFoundException('Invoice not found for student');
    if (String(invoice.status).toLowerCase() === 'cancelled') {
      throw new BadRequestException('A cancelled invoice cannot receive proof');
    }
    const existing = await runDbQuery(
      ds,
      `SELECT id, status FROM financial_payment_proofs
       WHERE invoice_id = ? AND parent_id = ?
       ORDER BY created_at DESC LIMIT 1`,
      [body.invoiceId, parentId],
    );
    if (existing.length && String(existing[0].status).toLowerCase() !== 'rejected') {
      throw new BadRequestException(
        `This invoice already has a ${String(existing[0].status || 'submitted')} proof`,
      );
    }

    const id = `proof_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    const submittedAt = new Date().toISOString();
    await runDbQuery(
      ds,
      `INSERT INTO financial_payment_proofs
        (id, invoice_id, student_id, parent_id, file_name, mime_type, file_data, status, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)`,
      [
        id,
        body.invoiceId,
        studentId,
        parentId,
        String(body.fileName || 'payment-proof').slice(0, 255),
        mimeType,
        base64,
        submittedAt,
      ],
    );
    return { id, invoiceId: body.invoiceId, status: 'pending', submittedAt };
  }
}
