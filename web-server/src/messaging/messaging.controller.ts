import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { IsArray, IsNotEmpty, IsOptional, IsString, MaxLength, MinLength } from 'class-validator';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { MessagingService } from './messaging.service';

class CreateThreadDto {
  @IsOptional()
  @IsString()
  @MaxLength(255)
  subject?: string;

  @IsArray()
  @IsString({ each: true })
  participantIds!: string[];

  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(5000)
  body?: string;
}

class PostMessageDto {
  @IsString()
  @IsNotEmpty()
  @MaxLength(5000)
  body!: string;
}

@Controller('messaging')
@RequireFeature('comms.messaging')
export class MessagingController {
  constructor(private readonly messaging: MessagingService) {}

  @Get('threads')
  async listThreads(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok(
      'Threads',
      await this.messaging.listThreads(tenantId, user.user_id || user.sub),
    );
  }

  @Get('recipients')
  async listRecipients(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    return ok(
      'Message recipients',
      await this.messaging.listParentRecipients(tenantId, user.user_id || user.sub),
    );
  }

  @Post('threads')
  async createThread(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateThreadDto,
  ) {
    return ok(
      'Thread created',
      await this.messaging.createThread(tenantId, user.user_id || user.sub, body),
    );
  }

  @Get('threads/:id/messages')
  async listMessages(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Messages',
      await this.messaging.listMessages(tenantId, user.user_id || user.sub, id),
    );
  }

  @Post('threads/:id/messages')
  async postMessage(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: PostMessageDto,
  ) {
    return ok(
      'Message sent',
      await this.messaging.postMessage(tenantId, user.user_id || user.sub, id, body.body),
    );
  }
}
