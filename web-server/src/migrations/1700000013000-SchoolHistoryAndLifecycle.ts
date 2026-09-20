import { MigrationInterface, QueryRunner } from 'typeorm';

/**
 * Additive school-domain foundation for scoped teaching, historical records,
 * and non-destructive lifecycle operations.
 */
export class SchoolHistoryAndLifecycle1700000013000 implements MigrationInterface {
  name = 'SchoolHistoryAndLifecycle1700000013000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    const statements = [
      `CREATE TABLE IF NOT EXISTS academic_subject_categories (
        id varchar(64) PRIMARY KEY,
        name varchar(128) NOT NULL,
        slug varchar(128) NOT NULL UNIQUE,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `ALTER TABLE academic_subjects ADD COLUMN IF NOT EXISTS category_id varchar(64) NULL`,
      `ALTER TABLE academic_subjects ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`,
      `ALTER TABLE academic_classes ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`,
      `ALTER TABLE users ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`,
      `ALTER TABLE users ADD COLUMN IF NOT EXISTS archive_reason text NULL`,
      `ALTER TABLE financial_fee_structures ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`,
      `ALTER TABLE financial_fee_structures ADD COLUMN IF NOT EXISTS created_by varchar(64) NULL`,
      `ALTER TABLE financial_invoices ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`,
      `ALTER TABLE financial_invoices ADD COLUMN IF NOT EXISTS payment_plan_type varchar(32) NOT NULL DEFAULT 'full'`,
      `ALTER TABLE financial_scholarships ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL`,
      `ALTER TABLE financial_scholarships ADD COLUMN IF NOT EXISTS created_by varchar(64) NULL`,
      `CREATE TABLE IF NOT EXISTS academic_student_history (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        session_id varchar(64) NULL,
        term_id varchar(64) NULL,
        class_id varchar(64) NULL,
        event_type varchar(64) NOT NULL,
        payload text NOT NULL DEFAULT '{}',
        recorded_by varchar(64) NULL,
        recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS academic_student_promotions (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        from_class_id varchar(64) NULL,
        to_class_id varchar(64) NOT NULL,
        session_id varchar(64) NULL,
        promoted_by varchar(64) NULL,
        notes text NULL,
        promoted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS financial_student_history (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        record_type varchar(32) NOT NULL,
        source_id varchar(64) NOT NULL,
        amount decimal(15,2) NULL,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        status varchar(32) NULL,
        metadata text NULL,
        recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (record_type, source_id)
      )`,
      `CREATE INDEX IF NOT EXISTS idx_student_history_student ON academic_student_history (student_id, recorded_at)`,
      `CREATE INDEX IF NOT EXISTS idx_student_promotions_student ON academic_student_promotions (student_id, promoted_at)`,
      `CREATE INDEX IF NOT EXISTS idx_financial_history_student ON financial_student_history (student_id, recorded_at)`,
    ];
    for (const statement of statements) await queryRunner.query(statement);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    void queryRunner;
  }
}
