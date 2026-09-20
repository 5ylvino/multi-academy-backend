import { MigrationInterface, QueryRunner } from 'typeorm';

/** Replace the former assistant head teacher role ID in tenant user records. */
export class RenameAssistantHeadTeacherRole1700000018000
  implements MigrationInterface
{
  name = 'RenameAssistantHeadTeacherRole1700000018000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `UPDATE users
       SET roles = REPLACE(roles, '"assist_head_teacher_academy"', '"assistant_head_teacher"')
       WHERE roles LIKE '%"assist_head_teacher_academy"%'`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `UPDATE users
       SET roles = REPLACE(roles, '"assistant_head_teacher"', '"assist_head_teacher_academy"')
       WHERE roles LIKE '%"assistant_head_teacher"%'`,
    );
  }
}
