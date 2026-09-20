import { MigrationInterface, QueryRunner } from 'typeorm';

/** Add unique admission numbers for student users. */
export class UserAdmissionNo1700000020000 implements MigrationInterface {
  name = 'UserAdmissionNo1700000020000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE users ADD COLUMN IF NOT EXISTS admission_no varchar(12) NULL`,
    );
    await queryRunner.query(
      `CREATE UNIQUE INDEX IF NOT EXISTS users_admission_no_unique ON users (admission_no)`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `DROP INDEX IF EXISTS users_admission_no_unique`,
    );
    await queryRunner.query(
      `ALTER TABLE users DROP COLUMN IF EXISTS admission_no`,
    );
  }
}
