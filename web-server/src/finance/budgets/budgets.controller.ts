import { Body, Controller, Get, Param, Post, Query } from '@nestjs/common';
import { ok } from '../../common/types/api-response';
import { TenantId } from '../../common/auth/tenant-id.decorator';
import { CurrentUser } from '../../common/auth/current-user.decorator';
import { AuthUserClaims } from '../../common/auth/auth-user.interface';
import { RequireFeature } from '../../platform-config/require-feature.decorator';
import { RequirePermissions } from '../../common/auth/require-permissions.decorator';
import { BudgetsService } from './budgets.service';

@Controller('finance/budgets')
@RequireFeature('finance.budgets')
export class BudgetsController {
  constructor(private readonly budgets: BudgetsService) {}

  @Get()
  @RequirePermissions('budgets:read')
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('term') term?: string,
  ) {
    return ok('Budget lines', await this.budgets.list(tenantId, user.user_id || user.sub, term));
  }

  @Post()
  @RequirePermissions('budgets:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { category: string; term: string; sessionLabel?: string; plannedAmount: number; currency?: string },
  ) {
    return ok('Budget line created', await this.budgets.create(tenantId, user.user_id || user.sub, body));
  }

  @Post(':id/spend')
  @RequirePermissions('budgets:manage')
  async spend(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: { amount: number },
  ) {
    return ok('Spend recorded', await this.budgets.recordSpend(tenantId, user.user_id || user.sub, id, body.amount));
  }
}
