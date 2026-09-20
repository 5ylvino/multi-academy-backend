import { canViewHostelRole } from './hostel.service';

describe('hostel role scope', () => {
  it('allows hostel visibility only to boarding and authorized operations roles', () => {
    expect(canViewHostelRole(['parent'])).toBe(true);
    expect(canViewHostelRole(['student'])).toBe(true);
    expect(canViewHostelRole(['bursar'])).toBe(false);
    expect(canViewHostelRole(['teacher'])).toBe(false);
    expect(canViewHostelRole(['principal'])).toBe(true);
  });
});
