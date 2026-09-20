import { Body, Controller, Get, Post, Query } from '@nestjs/common';
import { ok } from '../../common/types/api-response';
import { TenantId } from '../../common/auth/tenant-id.decorator';
import { CurrentUser } from '../../common/auth/current-user.decorator';
import { AuthUserClaims } from '../../common/auth/auth-user.interface';
import { RequireFeature } from '../../platform-config/require-feature.decorator';
import { RequirePermissions } from '../../common/auth/require-permissions.decorator';
import { InventoryService } from './inventory.service';

@Controller('finance/inventory')
@RequireFeature('finance.inventory')
export class InventoryController {
  constructor(private readonly inventory: InventoryService) {}

  @Get('items')
  @RequirePermissions('inventory:read')
  async listItems(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok('Inventory items', await this.inventory.listItems(tenantId, user.user_id || user.sub));
  }

  @Post('items')
  @RequirePermissions('inventory:manage')
  async createItem(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { sku?: string; name: string; category?: string; qtyOnHand?: number; unitCost?: number; currency?: string },
  ) {
    return ok('Item created', await this.inventory.createItem(tenantId, user.user_id || user.sub, body));
  }

  @Get('movements')
  @RequirePermissions('inventory:read')
  async listMovements(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('itemId') itemId?: string,
  ) {
    return ok('Movements', await this.inventory.listMovements(tenantId, user.user_id || user.sub, itemId));
  }

  @Post('movements')
  @RequirePermissions('inventory:manage')
  async recordMovement(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { itemId: string; direction: 'in' | 'out'; qty: number; reason?: string },
  ) {
    return ok('Movement recorded', await this.inventory.recordMovement(tenantId, user.user_id || user.sub, body));
  }
}
