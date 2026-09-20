import { MigrationInterface, QueryRunner } from 'typeorm';

/** Authoritative tenant DDL for emergency communications and consent workflows. */
export class EmergencyConsentTables1700000010000 implements MigrationInterface {
  name = 'EmergencyConsentTables1700000010000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS emergency_broadcasts (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        message text NOT NULL,
        channels varchar(128) NOT NULL DEFAULT 'sms,email',
        audience varchar(64) NOT NULL DEFAULT 'all_parents',
        created_by varchar(64) NULL,
        sent_count int NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS consent_slips (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        body text NOT NULL,
        slip_type varchar(64) NOT NULL DEFAULT 'permission',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS consent_responses (
        id varchar(64) PRIMARY KEY,
        slip_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        guardian_name varchar(255) NULL,
        status varchar(32) NOT NULL DEFAULT 'pending',
        signed_at TIMESTAMP NULL,
        notes text NULL
      )
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query('DROP TABLE IF EXISTS consent_responses');
    await queryRunner.query('DROP TABLE IF EXISTS consent_slips');
    await queryRunner.query('DROP TABLE IF EXISTS emergency_broadcasts');
  }
}
