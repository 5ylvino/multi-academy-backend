import { ROLE_PERMISSIONS } from '../common/auth/role-permissions';

describe('inventory and budget permissions', () => {
  it('keeps finance writes with authorized staff roles', () => {
    expect(ROLE_PERMISSIONS.bursar).toEqual(
      expect.arrayContaining(['inventory:read', 'inventory:manage', 'budgets:read', 'budgets:manage']),
    );
    expect(ROLE_PERMISSIONS.parent).not.toContain('inventory:manage');
    expect(ROLE_PERMISSIONS.student).not.toContain('budgets:manage');
  });

  it('grants read access to school leadership without granting it to family roles', () => {
    expect(ROLE_PERMISSIONS.school_admin).toContain('inventory:read');
    expect(ROLE_PERMISSIONS.principal).toContain('budgets:read');
    expect(ROLE_PERMISSIONS.parent).not.toContain('budgets:read');
  });
});
