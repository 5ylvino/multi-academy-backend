import { MigrationInterface, QueryRunner } from 'typeorm';

export class AnnouncementPublishColumns1700000015000 implements MigrationInterface {
  name = 'AnnouncementPublishColumns1700000015000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE announcements ADD COLUMN IF NOT EXISTS school_level varchar(32) NULL`,
    );
    await queryRunner.query(
      `ALTER TABLE announcements ADD COLUMN IF NOT EXISTS is_published boolean NOT NULL DEFAULT true`,
    );
    await queryRunner.query(
      `ALTER TABLE announcements ADD COLUMN IF NOT EXISTS is_critical boolean NOT NULL DEFAULT false`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`ALTER TABLE announcements DROP COLUMN IF EXISTS school_level`);
    await queryRunner.query(`ALTER TABLE announcements DROP COLUMN IF EXISTS is_published`);
  }
}
