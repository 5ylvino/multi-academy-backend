import { MigrationInterface, QueryRunner } from 'typeorm';

/**
 * Hot-path indexes for common list/filter columns.
 *
 * Tenant DBs are already school-scoped (no tenant_id column). Indexes target
 * the columns that list endpoints ORDER BY / WHERE on — payments, invoices,
 * results, attendance, and the link tables used by user enrichment.
 */
export class HotPathListIndexes1700000003000 implements MigrationInterface {
  name = 'HotPathListIndexes1700000003000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    const statements = [
      // Financial lists: ORDER BY created_at, filter by student/status
      `CREATE INDEX IF NOT EXISTS idx_financial_payments_created_at ON financial_payments (created_at DESC)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_payments_student ON financial_payments (student_id)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_payments_status ON financial_payments (status)`,
      // Provider references are the idempotency key for webhook settlement.
      `CREATE UNIQUE INDEX IF NOT EXISTS uq_financial_payments_provider_reference ON financial_payments (provider_reference)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_invoices_created_at ON financial_invoices (created_at DESC)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_invoices_student ON financial_invoices (student_id)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_invoices_status ON financial_invoices (status)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_scholarships_student ON financial_scholarships (student_id)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_refunds_student ON financial_refunds (student_id)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_fee_structures_created_at ON financial_fee_structures (created_at DESC)`,
      `CREATE INDEX IF NOT EXISTS idx_installment_plans_student ON financial_installment_plans (student_id)`,
      `CREATE INDEX IF NOT EXISTS idx_installments_plan ON financial_installments (plan_id)`,
      `CREATE INDEX IF NOT EXISTS idx_advance_credits_student ON financial_advance_credits (student_id)`,

      // Users / academic list paths
      `CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at DESC)`,
      `CREATE INDEX IF NOT EXISTS idx_academic_results_created_at ON academic_results (created_at DESC)`,
      `CREATE INDEX IF NOT EXISTS idx_academic_results_student ON academic_results (student_id)`,
      `CREATE INDEX IF NOT EXISTS idx_academic_results_class_term ON academic_results (class_id, term_id)`,
      `CREATE INDEX IF NOT EXISTS idx_academic_results_status ON academic_results (status)`,
      `CREATE INDEX IF NOT EXISTS idx_academic_assignments_created_at ON academic_assignments (created_at DESC)`,
      `CREATE INDEX IF NOT EXISTS idx_academic_terms_session ON academic_terms (session_id)`,
      `CREATE INDEX IF NOT EXISTS idx_class_teacher_links_teacher ON academic_class_teacher_links (teacher_id)`,
      `CREATE INDEX IF NOT EXISTS idx_subject_teacher_links_teacher ON academic_subject_teacher_links (teacher_id)`,
      `CREATE INDEX IF NOT EXISTS idx_enrollments_class ON student_class_enrollments (class_id)`,
      `CREATE INDEX IF NOT EXISTS idx_parent_links_parent ON parent_student_links (parent_id)`,

      // Attendance filters by date / class
      `CREATE INDEX IF NOT EXISTS idx_attendance_students_date ON attendance_students (date)`,
      `CREATE INDEX IF NOT EXISTS idx_attendance_students_class_date ON attendance_students (class_id, date)`,
      `CREATE INDEX IF NOT EXISTS idx_attendance_staff_date ON attendance_staff (date)`,

      // Comms / reports
      `CREATE INDEX IF NOT EXISTS idx_announcements_created_at ON announcements (created_at DESC)`,
      `CREATE INDEX IF NOT EXISTS idx_reports_generated_created_at ON reports_generated (created_at DESC)`,
      `CREATE INDEX IF NOT EXISTS idx_reports_generated_category ON reports_generated (category)`,
    ];

    for (const sql of statements) {
      await queryRunner.query(sql);
    }
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    const indexes = [
      'idx_financial_payments_created_at',
      'idx_financial_payments_student',
      'idx_financial_payments_status',
      'uq_financial_payments_provider_reference',
      'idx_financial_invoices_created_at',
      'idx_financial_invoices_student',
      'idx_financial_invoices_status',
      'idx_financial_scholarships_student',
      'idx_financial_refunds_student',
      'idx_financial_fee_structures_created_at',
      'idx_installment_plans_student',
      'idx_installments_plan',
      'idx_advance_credits_student',
      'idx_users_created_at',
      'idx_academic_results_created_at',
      'idx_academic_results_student',
      'idx_academic_results_class_term',
      'idx_academic_results_status',
      'idx_academic_assignments_created_at',
      'idx_academic_terms_session',
      'idx_class_teacher_links_teacher',
      'idx_subject_teacher_links_teacher',
      'idx_enrollments_class',
      'idx_parent_links_parent',
      'idx_attendance_students_date',
      'idx_attendance_students_class_date',
      'idx_attendance_staff_date',
      'idx_announcements_created_at',
      'idx_reports_generated_created_at',
      'idx_reports_generated_category',
    ];
    for (const name of indexes) {
      await queryRunner.query(`DROP INDEX IF EXISTS ${name}`);
    }
  }
}
