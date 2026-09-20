import { derivePermissionsForRoles } from './role-permissions';

/** Wildcard (primary owner) keeps full access; everyone else gets catalog-derived permissions. */
export function effectivePermissions(
  roles: string[],
  storedPermissions: string[],
): string[] {
  if (storedPermissions?.includes('*')) return ['*'];
  return derivePermissionsForRoles(roles || []);
}

export function effectiveSchoolLevel(
  userSchoolLevel: string | string[] | null | undefined,
  roles: string[],
  tenantSchoolLevels: string[],
): string | string[] {
  if (userSchoolLevel) return userSchoolLevel;
  if (roles.includes('head_teacher') || roles.includes('assistant_head_teacher')) {
    return 'primary';
  }
  if (roles.includes('principal') || roles.includes('vice_principal')) {
    return 'secondary';
  }
  return tenantSchoolLevels;
}
