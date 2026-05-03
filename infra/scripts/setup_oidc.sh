#!/usr/bin/env bash
# One-time setup: creates the OIDC provider, GitHub Actions IAM role,
# and EC2 instance role for keyless CI/CD via SSM Run Command.
#
# Run ONCE from your local machine with admin AWS credentials:
#   bash infra/scripts/setup_oidc.sh
#
# Before running, set the three variables below.
set -euo pipefail

# ── FILL THESE IN ─────────────────────────────────────────────────────────────
GITHUB_ORG="YOUR_GITHUB_ORG"        # e.g. "angelgalindor"
GITHUB_REPO="YOUR_REPO_NAME"        # e.g. "MasterTheBehavioralInterview"
EC2_INSTANCE_ID="YOUR_INSTANCE_ID"  # e.g. "i-0abc1234567890def"
AWS_REGION="us-west-2"
# ─────────────────────────────────────────────────────────────────────────────

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
GHA_ROLE_NAME="github-actions-behavioral-dummy"
EC2_ROLE_NAME="behavioral-dummy-ec2"
OIDC_URL="https://token.actions.githubusercontent.com"
OIDC_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"

echo "Account : $AWS_ACCOUNT_ID"
echo "Region  : $AWS_REGION"
echo "Repo    : $GITHUB_ORG/$GITHUB_REPO"
echo ""

# ── 1. OIDC identity provider ─────────────────────────────────────────────────
echo "==> Registering GitHub OIDC provider"
if aws iam get-open-id-connect-provider \
    --open-id-connect-provider-arn "$OIDC_ARN" > /dev/null 2>&1; then
  echo "    Already registered — skipping."
else
  # Thumbprint is stable for token.actions.githubusercontent.com
  aws iam create-open-id-connect-provider \
    --url "$OIDC_URL" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1"
  echo "    Registered."
fi

# ── 2. GitHub Actions IAM role ────────────────────────────────────────────────
echo ""
echo "==> Creating GitHub Actions role: $GHA_ROLE_NAME"

TRUST_POLICY=$(cat infra/iam/github-actions-trust-policy.json \
  | sed "s/YOUR_ACCOUNT_ID/$AWS_ACCOUNT_ID/g" \
  | sed "s/YOUR_GITHUB_ORG/$GITHUB_ORG/g" \
  | sed "s/YOUR_REPO_NAME/$GITHUB_REPO/g")

if aws iam get-role --role-name "$GHA_ROLE_NAME" > /dev/null 2>&1; then
  echo "    Role exists — updating trust policy."
  aws iam update-assume-role-policy \
    --role-name "$GHA_ROLE_NAME" \
    --policy-document "$TRUST_POLICY"
else
  aws iam create-role \
    --role-name "$GHA_ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --description "Assumed by GitHub Actions for keyless deployment of BehavioralDummy"
fi

PERMISSIONS_POLICY=$(cat infra/iam/github-actions-permissions.json \
  | sed "s/YOUR_ACCOUNT_ID/$AWS_ACCOUNT_ID/g" \
  | sed "s/YOUR_REGION/$AWS_REGION/g" \
  | sed "s/YOUR_EC2_INSTANCE_ID/$EC2_INSTANCE_ID/g")

aws iam put-role-policy \
  --role-name "$GHA_ROLE_NAME" \
  --policy-name "deploy-policy" \
  --policy-document "$PERMISSIONS_POLICY"

GHA_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${GHA_ROLE_NAME}"
echo "    Ready: $GHA_ROLE_ARN"

# ── 3. EC2 instance role ──────────────────────────────────────────────────────
# The EC2 role only needs AmazonSSMManagedInstanceCore — no inline policy.
# That managed policy lets the SSM agent register itself and receive commands.
echo ""
echo "==> Creating EC2 instance role: $EC2_ROLE_NAME"

EC2_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

if aws iam get-role --role-name "$EC2_ROLE_NAME" > /dev/null 2>&1; then
  echo "    Role exists."
else
  aws iam create-role \
    --role-name "$EC2_ROLE_NAME" \
    --assume-role-policy-document "$EC2_TRUST" \
    --description "EC2 instance role for BehavioralDummy"
fi

aws iam attach-role-policy \
  --role-name "$EC2_ROLE_NAME" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" \
  2>/dev/null || echo "    SSM policy already attached."

# Instance profile wires the role to the EC2 instance
if ! aws iam get-instance-profile \
    --instance-profile-name "$EC2_ROLE_NAME" > /dev/null 2>&1; then
  aws iam create-instance-profile \
    --instance-profile-name "$EC2_ROLE_NAME"
  aws iam add-role-to-instance-profile \
    --instance-profile-name "$EC2_ROLE_NAME" \
    --role-name "$EC2_ROLE_NAME"
  echo "    Instance profile created."
else
  echo "    Instance profile already exists."
fi

# ── 4. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo " Setup complete. Do these three things now:"
echo "════════════════════════════════════════════════════════"
echo ""
echo " 1. Attach the instance profile to your EC2 instance:"
echo "    aws ec2 associate-iam-instance-profile \\"
echo "      --instance-id $EC2_INSTANCE_ID \\"
echo "      --iam-instance-profile Name=$EC2_ROLE_NAME"
echo ""
echo " 2. Verify SSM agent sees the instance (wait ~60s after step 1):"
echo "    aws ssm describe-instance-information \\"
echo "      --filters Key=InstanceIds,Values=$EC2_INSTANCE_ID \\"
echo "      --region $AWS_REGION"
echo ""
echo " 3. Add these as GitHub Actions Variables (Settings → Secrets and variables"
echo "    → Variables — NOT Secrets, none of these are credentials):"
echo ""
echo "    IAM_ROLE_ARN    = $GHA_ROLE_ARN"
echo "    AWS_REGION      = $AWS_REGION"
echo "    EC2_INSTANCE_ID = $EC2_INSTANCE_ID"
echo ""
echo " 4. Create a GitHub Environment named 'production' (Settings →"
echo "    Environments). The OIDC token is scoped to this environment —"
echo "    jobs outside it cannot assume the IAM role."
echo ""
echo " 5. You can now close port 22 on your EC2 security group."
echo "    SSM communicates over HTTPS outbound — SSH is not required."
echo ""
