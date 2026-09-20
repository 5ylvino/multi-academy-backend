import { MigrationInterface, QueryRunner } from 'typeorm';

/** DDL for push preferences, academic planning, and idempotent offline sync. */
export class PushAcademicSyncTables1700000012000 implements MigrationInterface {
  name = 'PushAcademicSyncTables1700000012000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`CREATE TABLE IF NOT EXISTS push_preferences (
      user_id varchar(64) PRIMARY KEY, enabled boolean NOT NULL DEFAULT true,
      categories text NULL, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await queryRunner.query(`CREATE TABLE IF NOT EXISTS timetable_slots (
      id varchar(64) PRIMARY KEY, class_id varchar(64) NOT NULL, subject_id varchar(64) NULL,
      teacher_id varchar(64) NULL, day_of_week int NOT NULL, start_time varchar(8) NOT NULL,
      end_time varchar(8) NOT NULL, room varchar(64) NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await queryRunner.query(`CREATE TABLE IF NOT EXISTS academic_lesson_notes (
      id varchar(64) PRIMARY KEY, class_id varchar(64) NOT NULL, subject_id varchar(64) NULL,
      term_id varchar(64) NULL, title varchar(255) NOT NULL, content text NOT NULL,
      week_label varchar(64) NULL, created_by varchar(64) NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
    await queryRunner.query(`CREATE TABLE IF NOT EXISTS sync_operations (
      operation_id varchar(128) PRIMARY KEY, user_id varchar(64) NOT NULL,
      entity varchar(128) NOT NULL, action varchar(16) NOT NULL, payload text NOT NULL,
      applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query('DROP TABLE IF EXISTS sync_operations');
    await queryRunner.query('DROP TABLE IF EXISTS academic_lesson_notes');
    await queryRunner.query('DROP TABLE IF EXISTS timetable_slots');
    await queryRunner.query('DROP TABLE IF EXISTS push_preferences');
  }
}
