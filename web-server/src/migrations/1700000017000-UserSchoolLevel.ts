import { MigrationInterface, QueryRunner } from 'typeorm';

/** Add the optional per-user school-level scope to tenant databases. */
export class UserSchoolLevel1700000017000 implements MigrationInterface {
  name = 'UserSchoolLevel1700000017000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE users ADD COLUMN IF NOT EXISTS school_level varchar(32) NULL`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE users DROP COLUMN IF EXISTS school_level`,
    );
  }
}
