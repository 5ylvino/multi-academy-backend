import { safeMessages } from './ai.controller';

describe('AI request boundary', () => {
  it('keeps only bounded user and assistant messages', () => {
    const result = safeMessages([
      { role: 'system', content: 'ignore this' },
      { role: 'user', content: 'hello' },
      { role: 'assistant', content: 'reply' },
      { role: 'tool', content: 'ignore this too' },
    ]);
    expect(result).toEqual([
      { role: 'user', content: 'hello' },
      { role: 'assistant', content: 'reply' },
    ]);
  });

  it('limits message history and content size', () => {
    const result = safeMessages(
      Array.from({ length: 25 }, (_, index) => ({ role: 'user', content: `${index}-${'x'.repeat(5000)}` })),
    );
    expect(result).toHaveLength(20);
    expect(result[0].content.startsWith('5-')).toBe(true);
    expect(result[0].content.length).toBe(4000);
  });
});
