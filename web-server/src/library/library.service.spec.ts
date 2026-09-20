import { resolveBorrowerId } from './library.service';

describe('library borrower scope', () => {
  it('forces students to borrow against their own account', () => {
    expect(resolveBorrowerId(['student'], 'student-1', 'student-2')).toBe('student-1');
  });

  it('allows staff and parents to nominate a borrower for service validation', () => {
    expect(resolveBorrowerId(['parent'], 'parent-1', 'child-1')).toBe('child-1');
    expect(resolveBorrowerId(['school_admin'], 'admin-1', 'student-1')).toBe('student-1');
  });
});
