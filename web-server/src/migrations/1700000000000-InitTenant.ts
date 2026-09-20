import { MigrationInterface, QueryRunner } from 'typeorm';

export class InitTenant1700000000000 implements MigrationInterface {
  name = 'InitTenant1700000000000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS users (
        id varchar(64) PRIMARY KEY,
        email varchar(255) NOT NULL UNIQUE,
        name varchar(255) NOT NULL,
        phone varchar(32) NULL,
        major varchar(128) NULL,
        password_hash varchar(255) NOT NULL,
        roles TEXT NOT NULL,
        permissions TEXT NOT NULL,
        capabilities TEXT NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await queryRunner.query(`
      UPDATE users
      SET roles = '[]', permissions = '[]', capabilities = '[]'
      WHERE roles = '' OR permissions = '' OR capabilities = '';
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS users;`);
  }
}

