import { Injectable, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { buildReportDocx, buildReportPdf } from './report-file.util';
import { WorkerServiceClient } from '../worker/worker-service.client';

type ReportRow = Record<string, string | number | null | undefined>;
export type ReportDownloadFormat = 'csv' | 'json' | 'pdf' | 'docx' | 'doc';

type BuiltDataset = {
  columns: string[];
  rows: ReportRow[];
  summary?: Record<string, string | number>;
};

@Injectable()
export class ReportsService {
  private readonly ensuredTenants = new Set<string>();

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly worker: WorkerServiceClient,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureTables(tenantId: string, ds: any) {
    if (this.ensuredTenants.has(tenantId)) return;
    await ds.query(`
      CREATE TABLE IF NOT EXISTS reports_generated (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        type varchar(64) NOT NULL,
        scope varchar(128) NOT NULL,
        period varchar(128) NOT NULL,
        generated_date varchar(32) NOT NULL,
        total_amount decimal(15,2) NULL,
        status varchar(32) NOT NULL DEFAULT 'draft',
        category varchar(32) NOT NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    // Lightweight related tables so download queries never fail on fresh tenants.
    await Promise.all([
      ds.query(`
        CREATE TABLE IF NOT EXISTS financial_payments (
          id varchar(64) PRIMARY KEY,
          student_id varchar(64) NOT NULL,
          amount decimal(15,2) NOT NULL DEFAULT 0,
          method varchar(32) NOT NULL,
          reference varchar(255) NULL,
          date varchar(32) NOT NULL,
          status varchar(32) NOT NULL DEFAULT 'completed',
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
      `),
      ds.query(`
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
      `),
      ds.query(`
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
      `),
      ds.query(`
        CREATE TABLE IF NOT EXISTS academic_results (
          id varchar(64) PRIMARY KEY,
          student_id varchar(64) NULL,
          class_id varchar(64) NOT NULL,
          subject_id varchar(64) NOT NULL,
          term_id varchar(64) NOT NULL,
          ca_score decimal(10,2) NULL,
          exam_score decimal(10,2) NULL,
          total_score decimal(10,2) NULL,
          grade varchar(8) NULL,
          status varchar(32) NOT NULL DEFAULT 'draft',
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
      `),
      ds.query(`
        CREATE TABLE IF NOT EXISTS academic_classes (
          id varchar(64) PRIMARY KEY,
          name varchar(255) NOT NULL,
          code varchar(64) NOT NULL,
          school_level varchar(32) NOT NULL,
          class_teacher_id varchar(64) NULL,
          capacity int NULL,
          is_active boolean NOT NULL DEFAULT true,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
      `),
      ds.query(`
        CREATE TABLE IF NOT EXISTS academic_subjects (
          id varchar(64) PRIMARY KEY,
          name varchar(255) NOT NULL,
          code varchar(64) NOT NULL,
          school_level varchar(32) NOT NULL,
          description text NULL,
          class_ids TEXT NULL,
          is_active boolean NOT NULL DEFAULT true,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
      `),
      ds.query(`
        CREATE TABLE IF NOT EXISTS attendance_students (
          id varchar(64) PRIMARY KEY,
          student_id varchar(64) NOT NULL,
          class_id varchar(64) NULL,
          date varchar(16) NOT NULL,
          status varchar(16) NOT NULL,
          marked_by varchar(64) NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
      `),
      ds.query(`
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
      `),
      ds.query(`
        CREATE TABLE IF NOT EXISTS student_class_enrollments (
          id varchar(64) PRIMARY KEY,
          student_id varchar(64) NOT NULL,
          class_id varchar(64) NOT NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (student_id, class_id)
        );
      `),
    ]);
    this.ensuredTenants.add(tenantId);
  }

  private mapReportRow(row: any) {
    if (!row) return null;
    return {
      id: row.id,
      title: row.title,
      type: row.type,
      scope: row.scope,
      period: row.period,
      generatedDate: row.generatedDate ?? row.generateddate ?? row.generated_date ?? null,
      totalAmount:
        row.totalAmount ?? row.totalamount ?? row.total_amount ?? null,
      status: row.status,
      category: row.category,
    };
  }

  async listReports(tenantId: string, category?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    if (category && category !== 'all') {
      const rows = await runDbQuery(
        ds,
        `SELECT id, title, type, scope, period, generated_date as generatedDate, total_amount as totalAmount, status, category
         FROM reports_generated WHERE category = ? ORDER BY created_at DESC LIMIT 200`,
        [category],
      );
      return (rows || []).map((r: any) => this.mapReportRow(r));
    }
    const rows = await ds.query(
      `SELECT id, title, type, scope, period, generated_date as generatedDate, total_amount as totalAmount, status, category
       FROM reports_generated ORDER BY created_at DESC LIMIT 200`,
    );
    return (rows || []).map((r: any) => this.mapReportRow(r));
  }

  async getReportById(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    const rows = await runDbQuery(
      ds,
      `SELECT id, title, type, scope, period, generated_date as generatedDate, total_amount as totalAmount, status, category
       FROM reports_generated WHERE id = ? LIMIT 1`,
      [id],
    );
    return this.mapReportRow(rows[0] || null);
  }

  async createReport(tenantId: string, userId: string, body: any) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    const id = randomToken('rpt');
    let totalAmount = body.totalAmount ?? null;
    if (totalAmount == null && body.category === 'financial') {
      const summary = await this.getSummary(tenantId);
      totalAmount = summary.totalRevenue;
    }
    await runDbQuery(
      ds,
      `INSERT INTO reports_generated (id, title, type, scope, period, generated_date, total_amount, status, category, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())`,
      [
        id,
        body.title,
        body.type,
        body.scope,
        body.period,
        body.generatedDate || new Date().toISOString().slice(0, 10),
        totalAmount,
        body.status || 'draft',
        body.category,
        userId,
      ],
    );
    return { id, totalAmount };
  }

  async deleteReport(tenantId: string, id: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    await runDbQuery(ds, `DELETE FROM reports_generated WHERE id = ?`, [id]);
    return { id };
  }

  async getSummary(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    const [totals, byStatus, reportCount, outstanding] = await Promise.all([
      ds.query(`
        SELECT COALESCE(SUM(amount), 0)::float as totalRevenue,
               COUNT(*)::int as paymentCount
        FROM financial_payments
      `),
      ds.query(`
        SELECT status, COUNT(*)::int as count
        FROM financial_payments
        GROUP BY status
      `),
      ds.query(`SELECT COUNT(*)::int as count FROM reports_generated`),
      ds.query(`
        SELECT COALESCE(SUM(amount), 0)::float as total
        FROM financial_invoices
        WHERE LOWER(status) IN ('unpaid', 'partial', 'overdue', 'open')
      `).catch(() => [{ total: 0 }]),
    ]);
    const statusMap = Object.fromEntries(
      (byStatus || []).map((r: any) => [String(r.status || '').toLowerCase(), Number(r.count || 0)]),
    );
    return {
      totalRevenue: Number(totals?.[0]?.totalrevenue ?? totals?.[0]?.totalRevenue ?? 0),
      paymentCount: Number(totals?.[0]?.paymentcount ?? totals?.[0]?.paymentCount ?? 0),
      paymentStatus: {
        paid: statusMap.completed || 0,
        pending: statusMap.pending || 0,
        failed: statusMap.failed || 0,
      },
      outstandingAmount: Number(outstanding?.[0]?.total || 0),
      reportCount: Number(reportCount?.[0]?.count || 0),
    };
  }

  /** Phase 3 advanced analytics — live aggregates (not stub labels). */
  async getAdvancedDashboard(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    const base = await this.getSummary(tenantId);

    const [feeTrend, attendanceHeat, subjectPerf, financeOps] = await Promise.all([
      ds.query(`
        SELECT COALESCE(date, TO_CHAR(created_at, 'YYYY-MM-DD')) as day,
               COALESCE(SUM(amount), 0)::float as total
        FROM financial_payments
        WHERE created_at >= NOW() - INTERVAL '30 days'
           OR date >= TO_CHAR(NOW() - INTERVAL '30 days', 'YYYY-MM-DD')
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 30
      `).catch(() => []),
      ds.query(`
        SELECT status, COUNT(*)::int as count
        FROM student_attendance
        WHERE date >= TO_CHAR(NOW() - INTERVAL '14 days', 'YYYY-MM-DD')
        GROUP BY status
      `).catch(() =>
        ds.query(`
          SELECT status, COUNT(*)::int as count
          FROM attendance_records
          GROUP BY status
        `).catch(() => []),
      ),
      ds.query(`
        SELECT s.name as subject,
               AVG(COALESCE(r.total_score, r.ca_score + r.exam_score, 0))::float as avgScore,
               COUNT(*)::int as resultCount
        FROM academic_results r
        LEFT JOIN academic_subjects s ON s.id = r.subject_id
        GROUP BY s.name
        ORDER BY avgScore DESC NULLS LAST
        LIMIT 20
      `).catch(() => []),
      Promise.all([
        ds.query(`SELECT COUNT(*)::int as count, COALESCE(SUM(total_net), 0)::float as net
                  FROM payroll_runs WHERE status IN ('approved', 'payout_ready')`).catch(() => []),
        ds.query(`SELECT COUNT(*)::int as items, COALESCE(SUM(qty_on_hand * unit_cost), 0)::float as stockValue
                  FROM inventory_items`).catch(() => []),
        ds.query(`SELECT COALESCE(SUM(planned_amount), 0)::float as planned,
                         COALESCE(SUM(spent_amount), 0)::float as spent
                  FROM budget_lines`).catch(() => []),
      ]),
    ]);

    return {
      ...base,
      cohorts: {
        feeCollectionTrend: (feeTrend || []).map((r: any) => ({
          day: r.day,
          total: Number(r.total || 0),
        })),
        attendanceHeatmap: (attendanceHeat || []).map((r: any) => ({
          status: r.status,
          count: Number(r.count || 0),
        })),
        subjectPerformance: (subjectPerf || []).map((r: any) => ({
          subject: r.subject || 'Unknown',
          avgScore: Number(r.avgscore ?? r.avgScore ?? 0),
          resultCount: Number(r.resultcount ?? r.resultCount ?? 0),
        })),
      },
      finance: {
        approvedPayrollRuns: Number(financeOps?.[0]?.[0]?.count || 0),
        approvedPayrollNet: Number(financeOps?.[0]?.[0]?.net || 0),
        inventoryItems: Number(financeOps?.[1]?.[0]?.items || 0),
        inventoryStockValue: Number(financeOps?.[1]?.[0]?.stockvalue ?? financeOps?.[1]?.[0]?.stockValue ?? 0),
        budgetPlanned: Number(financeOps?.[2]?.[0]?.planned || 0),
        budgetSpent: Number(financeOps?.[2]?.[0]?.spent || 0),
      },
      generatedAt: new Date().toISOString(),
    };
  }

  private csvEscape(value: unknown): string {
    const s = value == null ? '' : String(value);
    if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  }

  private toCsv(columns: string[], rows: ReportRow[], metaLines: string[] = []): string {
    const lines = [
      ...metaLines.map((l) => `# ${l}`),
      columns.join(','),
      ...rows.map((row) => columns.map((c) => this.csvEscape(row[c])).join(',')),
    ];
    return lines.join('\n');
  }

  private async buildDataset(tenantId: string, report: any): Promise<BuiltDataset> {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureTables(tenantId, ds);
    const type = String(report.type || '');

    switch (type) {
      case 'monthly_revenue':
      case 'payment_summary': {
        const rows = await ds.query(`
          SELECT COALESCE(LEFT(NULLIF(p.date, ''), 7), 'Unknown') as "Month",
                 COUNT(*)::int as "Payments",
                 COALESCE(SUM(p.amount), 0)::float as "Amount",
                 SUM(CASE WHEN p.status = 'completed' THEN 1 ELSE 0 END)::int as "Completed",
                 SUM(CASE WHEN p.status = 'pending' THEN 1 ELSE 0 END)::int as "Pending"
          FROM financial_payments p
          GROUP BY 1
          ORDER BY 1 DESC
        `);
        return {
          columns: ['Month', 'Payments', 'Amount', 'Completed', 'Pending'],
          rows: rows || [],
          summary: { reportType: type },
        };
      }
      case 'fee_collection': {
        const rows = await ds.query(`
          SELECT f.name as "Fee", f.school_level as "Level", f.term as "Term",
                 f.amount::float as "Amount",
                 CASE WHEN f.is_active THEN 'Active' ELSE 'Inactive' END as "Status",
                 f.due_date as "DueDate"
          FROM financial_fee_structures f
          ORDER BY f.created_at DESC
        `);
        return {
          columns: ['Fee', 'Level', 'Term', 'Amount', 'Status', 'DueDate'],
          rows: rows || [],
        };
      }
      case 'outstanding_payments': {
        const rows = await ds.query(`
          SELECT COALESCE(u.name, i.student_id) as "Student",
                 i.title as "Invoice",
                 i.amount::float as "Amount",
                 i.term as "Term",
                 i.status as "Status",
                 i.due_date as "DueDate"
          FROM financial_invoices i
          LEFT JOIN users u ON u.id = i.student_id
          WHERE i.status IN ('unpaid', 'partial')
          ORDER BY i.due_date ASC NULLS LAST, i.created_at DESC
        `);
        return {
          columns: ['Student', 'Invoice', 'Amount', 'Term', 'Status', 'DueDate'],
          rows: rows || [],
        };
      }
      case 'expenses': {
        return {
          columns: ['Category', 'Description', 'Amount', 'Date'],
          rows: [],
          summary: { note: 'Expense tracking module not configured; empty export.' },
        };
      }
      case 'balance_sheet': {
        const [payments, invoices, fees] = await Promise.all([
          ds.query(`SELECT COALESCE(SUM(amount),0)::float as total FROM financial_payments WHERE status = 'completed'`),
          ds.query(`SELECT COALESCE(SUM(amount),0)::float as total FROM financial_invoices WHERE status IN ('unpaid','partial')`),
          ds.query(`SELECT COALESCE(SUM(amount),0)::float as total FROM financial_fee_structures WHERE is_active = true`),
        ]);
        const collected = Number(payments?.[0]?.total || 0);
        const outstanding = Number(invoices?.[0]?.total || 0);
        const feeBook = Number(fees?.[0]?.total || 0);
        return {
          columns: ['Item', 'Amount'],
          rows: [
            { Item: 'Fees collected (completed payments)', Amount: collected },
            { Item: 'Outstanding invoices', Amount: outstanding },
            { Item: 'Active fee structures (book value)', Amount: feeBook },
            { Item: 'Net (collected - outstanding)', Amount: collected - outstanding },
          ],
        };
      }
      case 'student_performance':
      case 'result_summary':
      case 'student_progress': {
        const rows = await ds.query(`
          SELECT COALESCE(u.name, r.student_id) as "Student",
                 COALESCE(c.name, r.class_id) as "Class",
                 r.term_id as "Term",
                 COUNT(*)::int as "Subjects",
                 ROUND(AVG(COALESCE(r.total_score, 0))::numeric, 2)::float as "Average",
                 MAX(r.grade) as "SampleGrade",
                 CASE
                   WHEN bool_and(r.status = 'approved') THEN 'approved'
                   WHEN bool_or(r.status = 'submitted') THEN 'submitted'
                   ELSE 'draft'
                 END as "Status"
          FROM academic_results r
          LEFT JOIN users u ON u.id = r.student_id
          LEFT JOIN academic_classes c ON c.id = r.class_id
          GROUP BY u.name, r.student_id, c.name, r.class_id, r.term_id
          ORDER BY 5 DESC NULLS LAST
        `);
        return {
          columns: ['Student', 'Class', 'Term', 'Subjects', 'Average', 'SampleGrade', 'Status'],
          rows: rows || [],
        };
      }
      case 'class_performance': {
        const rows = await ds.query(`
          SELECT COALESCE(c.name, r.class_id) as "Class",
                 r.term_id as "Term",
                 COUNT(DISTINCT r.student_id)::int as "Students",
                 COUNT(*)::int as "ResultRows",
                 ROUND(AVG(COALESCE(r.total_score, 0))::numeric, 2)::float as "Average"
          FROM academic_results r
          LEFT JOIN academic_classes c ON c.id = r.class_id
          GROUP BY c.name, r.class_id, r.term_id
          ORDER BY 5 DESC NULLS LAST
        `);
        return {
          columns: ['Class', 'Term', 'Students', 'ResultRows', 'Average'],
          rows: rows || [],
        };
      }
      case 'subject_analysis': {
        const rows = await ds.query(`
          SELECT COALESCE(s.name, r.subject_id) as "Subject",
                 r.term_id as "Term",
                 COUNT(*)::int as "Entries",
                 ROUND(AVG(COALESCE(r.ca_score, 0))::numeric, 2)::float as "AvgCA",
                 ROUND(AVG(COALESCE(r.exam_score, 0))::numeric, 2)::float as "AvgExam",
                 ROUND(AVG(COALESCE(r.total_score, 0))::numeric, 2)::float as "AvgTotal"
          FROM academic_results r
          LEFT JOIN academic_subjects s ON s.id = r.subject_id
          GROUP BY s.name, r.subject_id, r.term_id
          ORDER BY 6 DESC NULLS LAST
        `);
        return {
          columns: ['Subject', 'Term', 'Entries', 'AvgCA', 'AvgExam', 'AvgTotal'],
          rows: rows || [],
        };
      }
      case 'attendance_summary':
      case 'student_attendance': {
        const rows = await ds.query(`
          SELECT a.date as "Date",
                 COALESCE(u.name, a.student_id) as "Student",
                 COALESCE(c.name, a.class_id) as "Class",
                 a.status as "Status"
          FROM attendance_students a
          LEFT JOIN users u ON u.id = a.student_id
          LEFT JOIN academic_classes c ON c.id = a.class_id
          ORDER BY a.date DESC, u.name ASC
          LIMIT 5000
        `);
        return {
          columns: ['Date', 'Student', 'Class', 'Status'],
          rows: rows || [],
        };
      }
      case 'staff_attendance': {
        const rows = await ds.query(`
          SELECT a.date as "Date",
                 COALESCE(u.name, a.staff_id) as "Staff",
                 a.status as "Status",
                 a.sign_in_time as "SignIn",
                 CASE WHEN a.biometric_verified THEN 'Yes' ELSE 'No' END as "Biometric"
          FROM attendance_staff a
          LEFT JOIN users u ON u.id = a.staff_id
          ORDER BY a.date DESC, u.name ASC
          LIMIT 5000
        `);
        return {
          columns: ['Date', 'Staff', 'Status', 'SignIn', 'Biometric'],
          rows: rows || [],
        };
      }
      case 'absenteeism': {
        const rows = await ds.query(`
          SELECT COALESCE(u.name, a.student_id) as "Student",
                 SUM(CASE WHEN a.status = 'absent' THEN 1 ELSE 0 END)::int as "AbsentDays",
                 SUM(CASE WHEN a.status = 'late' THEN 1 ELSE 0 END)::int as "LateDays",
                 COUNT(*)::int as "MarkedDays"
          FROM attendance_students a
          LEFT JOIN users u ON u.id = a.student_id
          GROUP BY u.name, a.student_id
          HAVING SUM(CASE WHEN a.status IN ('absent', 'late') THEN 1 ELSE 0 END) > 0
          ORDER BY 2 DESC, 3 DESC
        `);
        return {
          columns: ['Student', 'AbsentDays', 'LateDays', 'MarkedDays'],
          rows: rows || [],
        };
      }
      case 'enrollment': {
        const rows = await ds.query(`
          SELECT c.name as "Class",
                 c.code as "Code",
                 c.school_level as "Level",
                 COALESCE(en.cnt, 0)::int as "Enrollment",
                 COALESCE(c.capacity, 0)::int as "Capacity"
          FROM academic_classes c
          LEFT JOIN (
            SELECT class_id, COUNT(*)::int as cnt
            FROM student_class_enrollments
            GROUP BY class_id
          ) en ON en.class_id = c.id
          WHERE c.is_active = true
          ORDER BY c.school_level, c.name
        `);
        return {
          columns: ['Class', 'Code', 'Level', 'Enrollment', 'Capacity'],
          rows: rows || [],
        };
      }
      case 'staff_summary': {
        const rows = await ds.query(`
          SELECT name as "Name", email as "Email", phone as "Phone", roles as "Roles",
                 CASE WHEN is_active THEN 'Active' ELSE 'Inactive' END as "Status"
          FROM users
          WHERE roles NOT LIKE '%"student"%'
            AND roles NOT LIKE '%"parent"%'
          ORDER BY name ASC
        `);
        return {
          columns: ['Name', 'Email', 'Phone', 'Roles', 'Status'],
          rows: (rows || []).map((r: any) => ({
            ...r,
            Roles: typeof r.Roles === 'string' ? r.Roles : JSON.stringify(r.Roles || r.roles || []),
          })),
        };
      }
      case 'demographics': {
        const rows = await ds.query(`
          SELECT c.school_level as "Level",
                 COUNT(DISTINCT e.student_id)::int as "Students",
                 COUNT(DISTINCT c.id)::int as "Classes"
          FROM academic_classes c
          LEFT JOIN student_class_enrollments e ON e.class_id = c.id
          GROUP BY c.school_level
          ORDER BY c.school_level
        `);
        return {
          columns: ['Level', 'Students', 'Classes'],
          rows: rows || [],
        };
      }
      case 'comprehensive': {
        const [enrollment, payments, results] = await Promise.all([
          ds.query(`
            SELECT 'Enrollment' as "Section", c.school_level as "Key",
                   COUNT(DISTINCT e.student_id)::int as "Value"
            FROM academic_classes c
            LEFT JOIN student_class_enrollments e ON e.class_id = c.id
            GROUP BY c.school_level
          `),
          ds.query(`
            SELECT 'Finance' as "Section", status as "Key",
                   COALESCE(SUM(amount),0)::float as "Value"
            FROM financial_payments
            GROUP BY status
          `),
          ds.query(`
            SELECT 'Academics' as "Section", status as "Key",
                   COUNT(*)::int as "Value"
            FROM academic_results
            GROUP BY status
          `),
        ]);
        return {
          columns: ['Section', 'Key', 'Value'],
          rows: [...(enrollment || []), ...(payments || []), ...(results || [])],
        };
      }
      default: {
        // Fallback: export the report metadata as a one-row sheet.
        return {
          columns: ['Field', 'Value'],
          rows: [
            { Field: 'Title', Value: report.title },
            { Field: 'Type', Value: report.type },
            { Field: 'Category', Value: report.category },
            { Field: 'Scope', Value: report.scope },
            { Field: 'Period', Value: report.period },
            { Field: 'Generated', Value: report.generatedDate },
            { Field: 'Status', Value: report.status },
            { Field: 'TotalAmount', Value: report.totalAmount ?? '' },
          ],
        };
      }
    }
  }

  private normalizeRows(columns: string[], rows: ReportRow[]): ReportRow[] {
    return (rows || []).map((row) => {
      const lookup = new Map<string, unknown>();
      for (const [k, v] of Object.entries(row || {})) {
        lookup.set(k.toLowerCase(), v);
      }
      const out: ReportRow = {};
      for (const col of columns) {
        out[col] = (lookup.get(col.toLowerCase()) as any) ?? '';
      }
      return out;
    });
  }

  async downloadReport(
    tenantId: string,
    id: string,
    format: ReportDownloadFormat = 'csv',
    actorId = 'system',
  ) {
    const heavy = format === 'pdf' || format === 'docx' || format === 'doc';
    if (heavy && this.worker.isEnabled()) {
      const job = await this.worker.enqueueReportDownload({
        tenantId,
        actorId,
        reportId: id,
        format,
      });
      if (job?.jobId) {
        const result = await this.worker.pollJob(tenantId, actorId, job.jobId);
        if (result) return result;
      }
    }
    return this.downloadReportLocal(tenantId, id, format);
  }

  async downloadReportLocal(tenantId: string, id: string, format: ReportDownloadFormat = 'csv') {
    const report = await this.getReportById(tenantId, id);
    if (!report) throw new NotFoundException('Report not found');

    const dataset = await this.buildDataset(tenantId, report);
    const rows = this.normalizeRows(dataset.columns, dataset.rows);
    const safeType = String(report.type || 'report').replace(/[^a-z0-9_-]+/gi, '-');
    const date = new Date().toISOString().slice(0, 10);
    const filenameBase = `${safeType}-${date}`;
    const normalizedFormat: ReportDownloadFormat =
      format === 'doc' ? 'docx' : format;

    if (normalizedFormat === 'json') {
      return {
        report,
        format: 'json' as const,
        filename: `${filenameBase}.json`,
        mimeType: 'application/json;charset=utf-8',
        encoding: 'utf8' as const,
        content: JSON.stringify(
          {
            meta: report,
            summary: dataset.summary || null,
            columns: dataset.columns,
            rows,
          },
          null,
          2,
        ),
        columns: dataset.columns,
        rowCount: rows.length,
      };
    }

    if (normalizedFormat === 'pdf') {
      const buffer = await buildReportPdf({
        report,
        columns: dataset.columns,
        rows,
        summary: dataset.summary || null,
      });
      return {
        report,
        format: 'pdf' as const,
        filename: `${filenameBase}.pdf`,
        mimeType: 'application/pdf',
        encoding: 'base64' as const,
        content: buffer.toString('base64'),
        columns: dataset.columns,
        rowCount: rows.length,
      };
    }

    if (normalizedFormat === 'docx') {
      const buffer = await buildReportDocx({
        report,
        columns: dataset.columns,
        rows,
        summary: dataset.summary || null,
      });
      return {
        report,
        format: 'docx' as const,
        filename: `${filenameBase}.docx`,
        mimeType:
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        encoding: 'base64' as const,
        content: buffer.toString('base64'),
        columns: dataset.columns,
        rowCount: rows.length,
      };
    }

    const metaLines = [
      `Title: ${report.title}`,
      `Type: ${report.type}`,
      `Category: ${report.category}`,
      `Scope: ${report.scope}`,
      `Period: ${report.period}`,
      `Generated: ${report.generatedDate}`,
      `Rows: ${rows.length}`,
      ...(dataset.summary
        ? Object.entries(dataset.summary).map(([k, v]) => `${k}: ${v}`)
        : []),
    ];

    return {
      report,
      format: 'csv' as const,
      filename: `${filenameBase}.csv`,
      mimeType: 'text/csv;charset=utf-8',
      encoding: 'utf8' as const,
      content: this.toCsv(dataset.columns, rows, metaLines),
      columns: dataset.columns,
      rowCount: rows.length,
    };
  }
}
