import { createParamDecorator, ExecutionContext } from '@nestjs/common';
import { AuthUserClaims } from './auth-user.interface';

export const CurrentUser = createParamDecorator(
  (_data: unknown, ctx: ExecutionContext): AuthUserClaims => {
    const request = ctx.switchToHttp().getRequest();
    return request.user as AuthUserClaims;
  },
);
