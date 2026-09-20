import { MigrationInterface, QueryRunner } from 'typeorm';

/** Authoritative tenant DDL for inventory and budget workflows. */
export class InventoryBudgetTables1700000008000 implements MigrationInterface {
  name = 'InventoryBudgetTables1700000008000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS inventory_items (
        id varchar(64) PRIMARY KEY,
        sku varchar(64) NULL,
        name varchar(255) NOT NULL,
        category varchar(64) NULL,
        qty_on_hand int NOT NULL DEFAULT 0,
        unit_cost decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS inventory_movements (
        id varchar(64) PRIMARY KEY,
        item_id varchar(64) NOT NULL,
        direction varchar(8) NOT NULL,
        qty int NOT NULL DEFAULT 0,
        reason varchar(255) NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS budget_lines (
        id varchar(64) PRIMARY KEY,
        category varchar(128) NOT NULL,
        term varchar(32) NOT NULL,
        session_label varchar(64) NULL,
        planned_amount decimal(15,2) NOT NULL DEFAULT 0,
        spent_amount decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query('DROP TABLE IF EXISTS inventory_movements');
    await queryRunner.query('DROP TABLE IF EXISTS inventory_items');
    await queryRunner.query('DROP TABLE IF EXISTS budget_lines');
  }
}
