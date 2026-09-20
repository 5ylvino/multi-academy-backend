import { createParamDecorator, ExecutionContext } from '@nestjs/common';
import { AuthUserClaims } from './auth-user.interface';

export const TenantId = createParamDecorator(
  (_data: unknown, ctx: ExecutionContext): string => {
    const request = ctx.switchToHttp().getRequest();
    const user = request.user as AuthUserClaims;
    return user.tenant_id;
  },
);
