import { MigrationInterface, QueryRunner } from 'typeorm';

/** Authoritative tenant DDL for transport and clinic operations. */
export class TransportClinicTables1700000007000 implements MigrationInterface {
  name = 'TransportClinicTables1700000007000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS transport_routes (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        stops text NULL,
        driver_id varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS transport_vehicles (
        id varchar(64) PRIMARY KEY,
        plate varchar(32) NOT NULL,
        capacity int NOT NULL DEFAULT 30,
        route_id varchar(64) NULL,
        status varchar(32) NOT NULL DEFAULT 'active',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS transport_assignments (
        id varchar(64) PRIMARY KEY,
        route_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        pickup_stop varchar(128) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS clinic_visits (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        symptoms text NULL,
        treatment text NULL,
        severity varchar(16) NOT NULL DEFAULT 'minor',
        nurse_id varchar(64) NULL,
        visited_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_transport_assignments_student ON transport_assignments (student_id)`,
    );
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_clinic_visits_student ON clinic_visits (student_id, visited_at)`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS clinic_visits`);
    await queryRunner.query(`DROP TABLE IF EXISTS transport_assignments`);
    await queryRunner.query(`DROP TABLE IF EXISTS transport_vehicles`);
    await queryRunner.query(`DROP TABLE IF EXISTS transport_routes`);
  }
}
