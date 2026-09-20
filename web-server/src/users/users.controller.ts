import {
  Body,
  Controller,
  Delete,
  ForbiddenException,
  Get,
  Headers,
  NotFoundException,
  Param,
  Patch,
  Post,
  Query,
  Req,
  UnauthorizedException,
} from '@nestjs/common';
import { Request } from 'express';
import { ok } from '../common/types/api-response';
import { UsersService } from './users.service';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { CreateUserDto } from './dto/create-user.dto';
import { UpdateUserDto } from './dto/update-user.dto';
import { UpdatePassportPhotoDto } from './dto/passport-photo.dto';
import { AdminResetPasswordDto } from './dto/admin-reset-password.dto';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { AuthService } from '../auth/auth.service';

@Controller('users')
@RequireFeature('users.management')
export class UsersController {
  constructor(
    private readonly usersService: UsersService,
    private readonly authService: AuthService,
  ) {}

  /** Alias for GET /auth/me so clients and edge docs stay in sync. */
  @Get('me')
  @RequireFeature()
  async me(@Headers('authorization') authorization?: string) {
    if (!authorization) {
      throw new UnauthorizedException('Missing Authorization header');
    }
    const [scheme, token] = authorization.split(' ');
    if (scheme !== 'Bearer' || !token) {
      throw new UnauthorizedException('Invalid Authorization header');
    }
    const data = await this.authService.me(token);
    return ok('Current user profile', data);
  }

  @Get()
  @RequirePermissions('users:read', 'users:manage')
  async list(
    @TenantId() tenantId: string,
    @Query('role') role?: string,
    @Query('category') category?: 'academic' | 'administrative_financial' | 'student_parent',
    @Query('page') page?: string,
    @Query('limit') limit?: string,
    @Query('refresh') refresh?: string,
  ) {
    const result = await this.usersService.listTenantUsers({
      tenantId,
      role,
      category,
      page: page ? Number(page) : undefined,
      limit: limit ? Number(limit) : undefined,
      forceRefresh: refresh === 'true',
    });
    if (Array.isArray(result)) return ok('Users', result);
    return ok('Users', result.rows, result.meta);
  }

  @Post()
  @RequirePermissions('users:create', 'users:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() actor: AuthUserClaims,
    @Body() body: CreateUserDto,
    @Req() req: Request,
  ) {
    const user = await this.usersService.createTenantUser({
      tenantId,
      body,
      actor,
      requestMeta: {
        ip: (req.headers['x-forwarded-for'] as string) || req.ip,
        userAgent: req.headers['user-agent'] as string,
      },
    });
    return ok('User created', user);
  }

  /** Any authenticated user can upload/replace their own passport photo. */
  @Patch('me/passport-photo')
  async updateMyPassport(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: UpdatePassportPhotoDto,
  ) {
    const updated = await this.usersService.updateOwnPassportPhoto({
      tenantId,
      userId: user.user_id,
      passportPhoto: body.passportPhoto,
    });
    return ok('Passport photo updated', updated);
  }

  @Get(':id')
  @RequirePermissions('users:read', 'users:manage')
  async getOne(@TenantId() tenantId: string, @Param('id') id: string) {
    const user = await this.usersService.getTenantUserById({ tenantId, id });
    if (!user) {
      throw new NotFoundException('User not found');
    }
    return ok('User', user);
  }

  @Patch(':id')
  @RequirePermissions('users:update', 'users:manage')
  async update(
    @TenantId() tenantId: string,
    @CurrentUser() actor: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: UpdateUserDto,
    @Req() req: Request,
  ) {
    const user = await this.usersService.updateTenantUser({
      tenantId,
      id,
      body,
      actor,
      requestMeta: {
        ip: (req.headers['x-forwarded-for'] as string) || req.ip,
        userAgent: req.headers['user-agent'] as string,
      },
    });
    return ok('User updated', user);
  }

  @Patch(':id/passport-photo')
  async updatePassport(
    @TenantId() tenantId: string,
    @CurrentUser() actor: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: UpdatePassportPhotoDto,
  ) {
    if (actor.user_id !== id) {
      const granted = [...(actor.permissions || []), ...(actor.capabilities || [])];
      const canManage =
        granted.includes('*') ||
        granted.includes('users:manage') ||
        granted.includes('users:update') ||
        granted.includes('users:*');
      if (!canManage) {
        throw new ForbiddenException('You can only update your own passport photo');
      }
    }
    const user = await this.usersService.updateOwnPassportPhoto({
      tenantId,
      userId: id,
      passportPhoto: body.passportPhoto,
    });
    return ok('Passport photo updated', user);
  }

  @Post(':id/reset-password')
  @RequirePermissions('accounts:reset', 'accounts:manage')
  async resetPassword(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: AdminResetPasswordDto = {},
  ) {
    const result = await this.usersService.adminResetPassword({
      tenantId,
      id,
      password: body?.password,
    });
    return ok('Password reset', result);
  }

  @Delete(':id')
  @RequirePermissions('users:delete', 'users:manage')
  async remove(@TenantId() tenantId: string, @Param('id') id: string) {
    const res = await this.usersService.deleteTenantUser({ tenantId, id });
    return ok('User deleted', res);
  }
}
