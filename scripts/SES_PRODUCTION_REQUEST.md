# SES Production Access — Request Justification

Paste the relevant fields into AWS Console → **SES** → **Account dashboard** →
**Request production access**. The form asks the following.

---

## Mail type
**Transactional**

## Website URL
`https://shasta.transilience.cloud/` (will move to `https://shasta.transilience.cloud/` once DNS for the SPA is configured)

## Use case description

CISO Copilot is a multi-tenant cloud-security platform that sends two types
of low-volume transactional emails to customers and admin users:

1. **Access-approval admin emails** — when a new customer signs up, the
   platform admin receives a single email with Approve/Reject links so they
   can authorize the new tenant. Sent to one verified mailbox per tenant
   (initially `kkmookhey@gmail.com`). Already working in sandbox.

2. **Access-approval user notifications** — once admin approves or rejects
   a sign-up, the requesting user receives one email confirming the
   decision and inviting them to open the app. This is what currently
   fails in sandbox because we can't pre-verify every prospective user's
   email address.

Volume expectation: <100 emails/day during beta, <1,000/day at GA. No
marketing, no newsletters, no list-purchased outbound.

All mail is initiated by an explicit user action — they signed up, or an
admin approved them. No unsolicited sends.

## How will you handle bounces and complaints?

- **Sender domain**: `settlingforless.com`, DKIM + SPF verified in SES
  (CNAMEs + TXT records published in Google Cloud DNS).
- **Bounce/complaint endpoint**: SES default notifications are enabled.
  Bounces and complaints are visible in CloudWatch (`AWS/SES` namespace)
  and the SES suppression list automatically prevents re-sends to bouncing
  addresses.
- **Suppression-list integration**: planned — a Lambda subscribed to the
  default SES SNS topic that flips the relevant `users.status` to
  `suppressed` in our Aurora DB so we don't try to re-send to the same
  address from a different code path.
- **Unsubscribe**: every email includes a single link back to the user's
  CISO Copilot profile where they can disable optional notifications.
  Mandatory transactional emails (access-approval results, security
  alerts the user explicitly subscribed to) cannot be unsubscribed
  while the account is active; deleting the account stops all mail.

## Do you send unsolicited mail?

**No.** Every email is in direct response to a user-initiated action:
either the user signed up for the platform, or an admin within the user's
tenant approved/denied that sign-up. There are no purchased lists, no
cold outreach, and no marketing newsletters.

## Will you only send to verified recipients?

**No** — that's why we're requesting production access. The recipient set
is "users who signed up via Microsoft 365 or Google Workspace federation."
We cannot pre-verify every prospective customer in SES.

## Compliance posture

- DKIM signing enforced via the verified `settlingforless.com` domain
  identity in `us-east-1`.
- SPF: `v=spf1 include:amazonses.com ~all` (apex TXT, published).
- Confirmed dual sender domain reputation via no recent bounces in our
  early test sends (`aws ses get-send-statistics` returns 0 bounces /
  0 complaints across our 4 production sends to date).

## Region

`us-east-1` only.

## Daily send quota request

Default new-account production limit (50k/24h) is more than enough; no
elevated request needed.
