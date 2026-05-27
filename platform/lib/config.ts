import 'dotenv/config';

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Missing required env var: ${name}. Copy platform/.env.example to platform/.env and fill it in.`);
  return v;
}

function optional(name: string, fallback: string = ''): string {
  return process.env[name] ?? fallback;
}

export const config = {
  awsRegion:          process.env.AWS_REGION ?? 'us-east-1',
  awsAccountId:       required('AWS_ACCOUNT_ID'),
  domain:             required('DOMAIN'),
  approvalRecipient:  required('APPROVAL_RECIPIENT'),
  entraTenantId:      required('ENTRA_TENANT_ID'),
  entraClientId:      required('ENTRA_CLIENT_ID'),
  entraClientSecret:  required('ENTRA_CLIENT_SECRET'),
  googleClientId:     required('GOOGLE_CLIENT_ID'),
  googleClientSecret: required('GOOGLE_CLIENT_SECRET'),

  // Augmented 2026-05-26 — extracted from hardcoded source
  shastaDomain:       required('SHASTA_DOMAIN'),
  apiBaseUrl:         required('API_BASE_URL'),
  webRedirectUri:     required('WEB_REDIRECT_URI'),
  appDomain:          required('APP_DOMAIN'),
  adminEmails:        required('ADMIN_EMAILS'),
  apnsPlatformAppArn: required('APNS_PLATFORM_APP_ARN'),
  appCertArn:         required('APP_CERT_ARN'),
  legacyAppDomain:    optional('LEGACY_APP_DOMAIN', ''),
};
