import { matchesSchoolSearch, schoolSearchThreshold } from './school-search.util';

describe('school search matching', () => {
  it('uses three characters for short first words', () => {
    expect(schoolSearchThreshold('Eden School')).toBe(3);
    expect(matchesSchoolSearch('Eden School', 'Ede')).toBe(true);
    expect(matchesSchoolSearch('Eden School', 'Ed')).toBe(false);
  });

  it('uses half of a long first word, rounded up', () => {
    expect(schoolSearchThreshold('Greenfield Academy')).toBe(5);
    expect(matchesSchoolSearch('Greenfield Academy', 'Gree')).toBe(false);
    expect(matchesSchoolSearch('Greenfield Academy', 'Green')).toBe(true);
  });

  it('matches case-insensitively and only from the first word', () => {
    expect(matchesSchoolSearch('Bright Future School', 'bRI')).toBe(true);
    expect(matchesSchoolSearch('Bright Future School', 'Future')).toBe(false);
    expect(matchesSchoolSearch('Bright Future School', 'Bright Future')).toBe(true);
    expect(matchesSchoolSearch('Krist Bethel College', 'kristbethel')).toBe(true);
  });
});
