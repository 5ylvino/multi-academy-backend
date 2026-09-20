export function schoolSearchThreshold(name: string): number {
  const firstWord = name.trim().split(/\s+/)[0] || '';
  return firstWord.length <= 5 ? 3 : Math.ceil(firstWord.length / 2);
}

export function matchesSchoolSearch(name: string, query: string): boolean {
  const normalizedName = name.trim().replace(/\s+/g, ' ');
  const normalizedQuery = query.trim().replace(/\s+/g, ' ');
  const compactName = normalizedName.replace(/[\s-]+/g, '').toLocaleLowerCase();
  const compactQuery = normalizedQuery.replace(/[\s-]+/g, '').toLocaleLowerCase();
  return (
    normalizedQuery.length >= schoolSearchThreshold(normalizedName) &&
    (normalizedName.toLocaleLowerCase().startsWith(normalizedQuery.toLocaleLowerCase()) ||
      compactName.startsWith(compactQuery))
  );
}
