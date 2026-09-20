import { Body, Controller, Get, Param, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { LibraryService } from './library.service';

@Controller('library')
@RequireFeature('ops.library')
export class LibraryController {
  constructor(private readonly library: LibraryService) {}

  @Get('items')
  @RequirePermissions(
    'classes:read',
    'classes:manage',
    'organization:view',
    'results:read',
  )
  async list(@TenantId() tenantId: string) {
    return ok('Library items', await this.library.listItems(tenantId));
  }

  @Post('items')
  @RequirePermissions('classes:manage', 'organization:update')
  async create(
    @TenantId() tenantId: string,
    @Body() body: { title: string; author?: string; isbn?: string; copies?: number },
  ) {
    return ok('Item created', await this.library.createItem(tenantId, body));
  }

  @Get('loans')
  @RequirePermissions(
    'classes:read',
    'classes:manage',
    'organization:view',
    'results:read',
  )
  async listLoans(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('status') status?: 'open' | 'returned',
  ) {
    return ok(
      'Library loans',
      await this.library.listLoans(tenantId, user.user_id || user.sub, status),
    );
  }

  @Post('loans')
  @RequirePermissions('classes:manage', 'organization:update', 'results:read')
  async loan(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { itemId: string; borrowerId?: string; dueAt?: string },
  ) {
    return ok(
      'Loan created',
      await this.library.loan(tenantId, user.user_id || user.sub, body),
    );
  }

  @Post('loans/:id/return')
  @RequirePermissions('classes:manage', 'organization:update')
  async returnLoan(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Loan returned', await this.library.returnLoan(tenantId, id));
  }
}
