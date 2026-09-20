import { MigrationInterface, QueryRunner } from 'typeorm';

/** Authoritative tenant DDL for hostel houses, beds, and allocations. */
export class HostelTables1700000011000 implements MigrationInterface {
  name = 'HostelTables1700000011000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS hostel_houses (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        gender varchar(16) NULL,
        capacity int NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS hostel_beds (
        id varchar(64) PRIMARY KEY,
        house_id varchar(64) NOT NULL,
        label varchar(64) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'available',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS hostel_allocations (
        id varchar(64) PRIMARY KEY,
        bed_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        from_date varchar(32) NULL,
        to_date varchar(32) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query('DROP TABLE IF EXISTS hostel_allocations');
    await queryRunner.query('DROP TABLE IF EXISTS hostel_beds');
    await queryRunner.query('DROP TABLE IF EXISTS hostel_houses');
  }
}
