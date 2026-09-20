import { MigrationInterface, QueryRunner } from 'typeorm';

export class BiometricCredentialColumns1700000004000 implements MigrationInterface {
  name = 'BiometricCredentialColumns1700000004000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE biometric_credentials ADD COLUMN IF NOT EXISTS transports text NULL`,
    );
    await queryRunner.query(
      `ALTER TABLE biometric_credentials ADD COLUMN IF NOT EXISTS device_type varchar(32) NULL`,
    );
    await queryRunner.query(
      `ALTER TABLE biometric_credentials ADD COLUMN IF NOT EXISTS backed_up boolean NOT NULL DEFAULT false`,
    );
    await queryRunner.query(
      `ALTER TABLE biometric_credentials ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE biometric_credentials DROP COLUMN IF EXISTS device_type`,
    );
    await queryRunner.query(
      `ALTER TABLE biometric_credentials DROP COLUMN IF EXISTS backed_up`,
    );
    await queryRunner.query(
      `ALTER TABLE biometric_credentials DROP COLUMN IF EXISTS transports`,
    );
    await queryRunner.query(
      `ALTER TABLE biometric_credentials DROP COLUMN IF EXISTS updated_at`,
    );
  }
}
