import { MigrationInterface, QueryRunner } from 'typeorm';

/** Align legacy staff attendance tables with the daily verification schema. */
export class AttendanceStaffCompatibility1700000021000
  implements MigrationInterface
{
  name = 'AttendanceStaffCompatibility1700000021000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE attendance_staff ADD COLUMN IF NOT EXISTS sign_in_time varchar(32) NULL`,
    );
    await queryRunner.query(
      `ALTER TABLE attendance_staff ADD COLUMN IF NOT EXISTS location varchar(64) NULL`,
    );
    await queryRunner.query(
      `ALTER TABLE attendance_staff ADD COLUMN IF NOT EXISTS biometric_verified boolean NOT NULL DEFAULT false`,
    );
    await queryRunner.query(
      `ALTER TABLE attendance_staff ADD COLUMN IF NOT EXISTS marked_by varchar(64) NULL`,
    );
    await queryRunner.query(
      `UPDATE attendance_staff
       SET sign_in_time = COALESCE(sign_in_time, check_in_at)
       WHERE sign_in_time IS NULL`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE attendance_staff DROP COLUMN IF EXISTS marked_by`,
    );
    await queryRunner.query(
      `ALTER TABLE attendance_staff DROP COLUMN IF EXISTS biometric_verified`,
    );
    await queryRunner.query(
      `ALTER TABLE attendance_staff DROP COLUMN IF EXISTS location`,
    );
    await queryRunner.query(
      `ALTER TABLE attendance_staff DROP COLUMN IF EXISTS sign_in_time`,
    );
  }
}
