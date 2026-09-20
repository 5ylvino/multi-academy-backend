export interface ApiEnvelope<T = any> {
  has_error: boolean;
  message: string;
  data?: T;
  [key: string]: any;
}

export function ok<T>(message: string, data?: T, meta: Record<string, any> = {}): ApiEnvelope<T> {
  return {
    has_error: false,
    message,
    data,
    ...meta,
  };
}

export function fail(message: string, data?: any, meta: Record<string, any> = {}): ApiEnvelope {
  return {
    has_error: true,
    message,
    data,
    ...meta,
  };
}

