import 'dotenv/config';

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Missing required env var: ${name}. Copy platform/.env.example to platform/.env and fill it in.`);
  return v;
}

export const config = {
  awsRegion:          process.env.AWS_REGION ?? 'us-east-1',
  domain:             required('DOMAIN'),
  approvalRecipient:  required('APPROVAL_RECIPIENT'),
  entraTenantId:      required('ENTRA_TENANT_ID'),
  entraClientId:      required('ENTRA_CLIENT_ID'),
  entraClientSecret:  required('ENTRA_CLIENT_SECRET'),
  googleClientId:     required('GOOGLE_CLIENT_ID'),
  googleClientSecret: required('GOOGLE_CLIENT_SECRET'),
};
