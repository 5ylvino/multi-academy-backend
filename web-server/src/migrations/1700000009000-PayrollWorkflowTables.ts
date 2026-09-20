import { MigrationInterface, QueryRunner } from 'typeorm';

/** Authoritative tenant DDL for payroll approval and payout preparation. */
export class PayrollWorkflowTables1700000009000 implements MigrationInterface {
  name = 'PayrollWorkflowTables1700000009000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS payroll_runs (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        period_start varchar(32) NOT NULL,
        period_end varchar(32) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'draft',
        total_gross decimal(15,2) NOT NULL DEFAULT 0,
        total_net decimal(15,2) NOT NULL DEFAULT 0,
        salary_account_ref varchar(255) NULL,
        operating_account_ref varchar(255) NULL,
        approved_by varchar(64) NULL,
        approved_at TIMESTAMP NULL,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS payroll_paye_lines (
        id varchar(64) PRIMARY KEY,
        run_id varchar(64) NOT NULL,
        staff_id varchar(64) NOT NULL,
        gross decimal(15,2) NOT NULL DEFAULT 0,
        allowances decimal(15,2) NOT NULL DEFAULT 0,
        deductions decimal(15,2) NOT NULL DEFAULT 0,
        paye decimal(15,2) NOT NULL DEFAULT 0,
        net decimal(15,2) NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query('DROP TABLE IF EXISTS payroll_paye_lines');
    await queryRunner.query('DROP TABLE IF EXISTS payroll_runs');
  }
}
