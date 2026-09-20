import { MigrationInterface, QueryRunner } from 'typeorm';

/** Add optional gender data for student profiles. */
export class UserGender1700000019000 implements MigrationInterface {
  name = 'UserGender1700000019000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE users ADD COLUMN IF NOT EXISTS gender varchar(16) NULL`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE users DROP COLUMN IF EXISTS gender`,
    );
  }
}
