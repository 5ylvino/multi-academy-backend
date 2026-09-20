import { MigrationInterface, QueryRunner } from 'typeorm';

export class ScoreSheets1700000016000 implements MigrationInterface {
  name = 'ScoreSheets1700000016000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS academic_score_categories (
        id varchar(64) PRIMARY KEY,
        name varchar(128) NOT NULL,
        slug varchar(128) NOT NULL UNIQUE,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS academic_score_subcategories (
        id varchar(64) PRIMARY KEY,
        category_id varchar(64) NOT NULL,
        name varchar(128) NOT NULL,
        slug varchar(128) NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (category_id, slug)
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS academic_score_sheets (
        id varchar(64) PRIMARY KEY,
        session_id varchar(64) NOT NULL,
        term_id varchar(64) NOT NULL,
        class_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        subject_id varchar(64) NOT NULL,
        category_id varchar(64) NOT NULL,
        subcategory_id varchar(64) NULL,
        student_score decimal(10,2) NULL,
        obtainable_score decimal(10,2) NOT NULL,
        grade int NULL,
        status varchar(16) NOT NULL DEFAULT 'draft',
        recorder_user_id varchar(64) NOT NULL,
        recorded_by varchar(255) NOT NULL,
        recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (session_id, term_id, class_id, student_id, subject_id, category_id, subcategory_id)
      )
    `);
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_score_sheets_scope
       ON academic_score_sheets (class_id, subject_id, session_id, term_id)`,
    );
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_score_sheets_status
       ON academic_score_sheets (status)`,
    );
  }

  public async down(): Promise<void> {
    // Tenant tables are intentionally retained during migration rollback.
  }
}
