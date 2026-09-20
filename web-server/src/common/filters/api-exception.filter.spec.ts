import { HttpException, HttpStatus } from '@nestjs/common';
import { ApiExceptionFilter } from './api-exception.filter';

function mockHost(exceptionIgnored?: unknown) {
  const json = jest.fn();
  const status = jest.fn().mockReturnValue({ json });
  const host = {
    switchToHttp: () => ({
      getResponse: () => ({ status }),
      getRequest: () => ({ url: '/api/v1/test', method: 'GET' }),
    }),
  };
  return { host: host as any, json, status, exceptionIgnored };
}

describe('ApiExceptionFilter', () => {
  const originalEnv = process.env.NODE_ENV;

  afterEach(() => {
    process.env.NODE_ENV = originalEnv;
  });

  it('returns the HttpException message', () => {
    const filter = new ApiExceptionFilter();
    const { host, json, status } = mockHost();
    filter.catch(new HttpException('Nope', HttpStatus.BAD_REQUEST), host);
    expect(status).toHaveBeenCalledWith(400);
    expect(json).toHaveBeenCalledWith(
      expect.objectContaining({ has_error: true, message: 'Nope' }),
    );
  });

  it('hides unexpected error details in production', () => {
    process.env.NODE_ENV = 'production';
    const filter = new ApiExceptionFilter();
    const { host, json, status } = mockHost();
    filter.catch(new Error('password hash blew up at line 12'), host);
    expect(status).toHaveBeenCalledWith(500);
    expect(json).toHaveBeenCalledWith(
      expect.objectContaining({
        has_error: true,
        message: 'Internal server error',
      }),
    );
    expect(JSON.stringify(json.mock.calls[0][0])).not.toContain('password hash');
  });
});
