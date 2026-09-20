import { isSafeSyncOperation } from './sync.service';

describe('offline sync policy', () => {
  it('accepts bounded mutation envelopes', () => {
    expect(isSafeSyncOperation({ operationId: 'op-1', entity: 'attendance', action: 'update', payload: {} })).toBe(true);
  });

  it('rejects missing identities and unsupported actions', () => {
    expect(isSafeSyncOperation({ entity: 'attendance', action: 'update', payload: {} })).toBe(false);
    expect(isSafeSyncOperation({ operationId: 'op-1', entity: 'attendance', action: 'delete', payload: {} })).toBe(false);
  });
});
