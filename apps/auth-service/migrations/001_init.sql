-- Each business onboarded is an organization; the ledger-api scopes all data by org_id.
CREATE TABLE organizations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES organizations(id),
  email         text NOT NULL UNIQUE,  -- stored lowercased
  password_hash text NOT NULL,
  role          text NOT NULL CHECK (role IN ('admin', 'approver', 'ap_clerk', 'vendor')),
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX users_org_id_idx ON users (org_id);

-- Opaque refresh tokens, stored hashed. Rotated on every use; reusing a rotated
-- token revokes its whole family (a sign the token was stolen).
CREATE TABLE refresh_tokens (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  family_id  uuid NOT NULL,
  token_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refresh_tokens_family_id_idx ON refresh_tokens (family_id);

CREATE TABLE audit_log (
  id         bigserial PRIMARY KEY,
  user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
  action     text NOT NULL,
  ip         text,
  created_at timestamptz NOT NULL DEFAULT now()
);
