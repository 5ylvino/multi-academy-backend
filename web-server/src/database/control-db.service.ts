import { Injectable, OnModuleDestroy } from '@nestjs/common';
import { DataSource } from 'typeorm';
import { buildControlDataSourceOptions, runControlMigrations } from './control-db.config';
import { ConfigService } from '@nestjs/config';

@Injectable()
export class ControlDbService implements OnModuleDestroy {
  private dataSource: DataSource | null = null;

  constructor(private readonly config: ConfigService) {}

  async ensureInitialized(): Promise<DataSource> {
    if (this.dataSource && this.dataSource.isInitialized) {
      return this.dataSource;
    }
    const url = this.config.get<string>('CONTROL_DB_URL');
    if (!url) {
      throw new Error('CONTROL_DB_URL is not configured');
    }
    // Migrations are a deployment step, not a readiness-probe side effect.
    // Opt in only for legacy/local environments that explicitly require it.
    if (this.config.get<string>('CONTROL_DB_AUTO_MIGRATE') === 'true') {
      await runControlMigrations(url);
    }
    const ds = new DataSource(buildControlDataSourceOptions(url));
    await ds.initialize();
    this.dataSource = ds;
    return ds;
  }

  async getDataSource(): Promise<DataSource> {
    return this.ensureInitialized();
  }

  /** Readiness check that initializes the control pool when needed. */
  async ping(): Promise<boolean> {
    try {
      const dataSource = await this.ensureInitialized();
      await dataSource.query('SELECT 1');
      return true;
    } catch {
      return false;
    }
  }

  async onModuleDestroy() {
    if (this.dataSource && this.dataSource.isInitialized) {
      await this.dataSource.destroy();
    }
  }
}

