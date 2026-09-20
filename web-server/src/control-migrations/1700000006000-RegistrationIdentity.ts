import { MigrationInterface, QueryRunner } from 'typeorm';

/** Store only hashed onboarding identity signals in the control database. */
export class RegistrationIdentity1700000006000
  implements MigrationInterface
{
  name = 'RegistrationIdentity1700000006000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE registration_staging
       ADD COLUMN IF NOT EXISTS install_token_hash varchar(64) NULL,
       ADD COLUMN IF NOT EXISTS device_hash varchar(128) NULL`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE registration_staging
       DROP COLUMN IF EXISTS device_hash,
       DROP COLUMN IF EXISTS install_token_hash`,
    );
  }
}
