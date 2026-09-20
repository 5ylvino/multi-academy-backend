import { IsBoolean, IsEmail, IsOptional, IsString, MinLength } from 'class-validator';

export class LoginDto {
  /** Set by /:schoolSlug/login so the email is resolved within that school. */
  @IsOptional()
  @IsString()
  schoolSlug?: string;

  @IsOptional()
  @IsString()
  schoolBusinessOrganisationId?: string;

  @IsEmail()
  email!: string;

  @IsString()
  @MinLength(8)
  password!: string;

  /** When true and `auth.remember_me` is on, refresh token TTL extends to 30 days. */
  @IsOptional()
  @IsBoolean()
  rememberMe?: boolean;
}
