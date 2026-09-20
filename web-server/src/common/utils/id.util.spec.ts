import {
  generateSlugFromName,
  isValidSchoolSlug,
  SCHOOL_SLUG_PATTERN,
} from './id.util';

describe('school login slugs', () => {
  it('generates the saved school-name slug used by branded login', () => {
    expect(generateSlugFromName('Krist Bethel College')).toBe(
      'krist-bethel-college',
    );
    expect(SCHOOL_SLUG_PATTERN.test('krist-bethel-college')).toBe(true);
  });

  it('accepts only lowercase URL-safe slug segments', () => {
    expect(isValidSchoolSlug('kristbethel-college')).toBe(true);
    expect(isValidSchoolSlug('krist-bethel-college')).toBe(true);
    expect(isValidSchoolSlug('Krist Bethel College')).toBe(false);
    expect(isValidSchoolSlug('../login')).toBe(false);
    expect(isValidSchoolSlug('school/login')).toBe(false);
    expect(isValidSchoolSlug('')).toBe(false);
  });
});
