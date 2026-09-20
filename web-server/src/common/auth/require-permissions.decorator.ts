import { SetMetadata } from '@nestjs/common';

export const PERMISSIONS_KEY = 'permissions';
export const ROLES_KEY = 'roles';

/** Require at least one of the listed permissions or capabilities (supports wildcard `*`). */
export const RequirePermissions = (...permissions: string[]) =>
  SetMetadata(PERMISSIONS_KEY, permissions);

/** Require at least one of the listed roles. */
export const RequireRoles = (...roles: string[]) => SetMetadata(ROLES_KEY, roles);
