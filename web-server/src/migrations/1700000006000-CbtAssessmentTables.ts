import { MigrationInterface, QueryRunner } from 'typeorm';

/**
 * CBT assessment schema. Service-level ensureTables remains a safe fallback,
 * while deployments should run this migration before enabling academic.cbt.
 */
export class CbtAssessmentTables1700000006000 implements MigrationInterface {
  name = 'CbtAssessmentTables1700000006000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS cbt_exams (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        subject_id varchar(64) NULL,
        class_id varchar(64) NULL,
        duration_minutes int NOT NULL DEFAULT 60,
        status varchar(32) NOT NULL DEFAULT 'draft',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS cbt_questions (
        id varchar(64) PRIMARY KEY,
        exam_id varchar(64) NOT NULL,
        prompt text NOT NULL,
        options text NOT NULL DEFAULT '[]',
        correct_index int NOT NULL DEFAULT 0,
        points int NOT NULL DEFAULT 1
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS cbt_attempts (
        id varchar(64) PRIMARY KEY,
        exam_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'in_progress',
        answers text NULL,
        score int NOT NULL DEFAULT 0,
        max_score int NOT NULL DEFAULT 0,
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        submitted_at TIMESTAMP NULL
      )
    `);
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_cbt_attempts_student ON cbt_attempts (student_id, exam_id)`,
    );
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_cbt_questions_exam ON cbt_questions (exam_id)`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS cbt_attempts`);
    await queryRunner.query(`DROP TABLE IF EXISTS cbt_questions`);
    await queryRunner.query(`DROP TABLE IF EXISTS cbt_exams`);
  }
}
