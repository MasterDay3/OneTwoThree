#!/usr/bin/env bash
# ACM certificate for a custom domain (used by the Makefile `aws-frontend-*` and `aws-backend-*` domain targets).
#
#   cert.sh request <domain>  request (or reuse) a DNS-validated certificate and set up validation
#   cert.sh status  <domain>  show certificate status and the validation record
#   cert.sh wait    <domain>  block until the certificate is issued
#   cert.sh arn     <domain>  print the ARN of the issued certificate (empty if none)
#   cert.sh zone-id <domain>  print the Route 53 public hosted zone serving the domain (empty if none)
#
# If the domain's zone is in Route 53 in this account, validation records are created automatically;
# otherwise the record to add at your DNS provider is printed.
# The certificate is tagged PROJECT_NAME=$PROJECT_NAME (set by the Makefile), like every stack resource.
set -euo pipefail

# CloudFront only accepts certificates from us-east-1, whatever region the stacks are in.
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1

cmd=${1:?usage: $0 <request|status|wait|arn|zone-id> <domain>}
domain=${2:?domain required}
project=${PROJECT_NAME:-meetings}

bold=$'\033[1m'
reset=$'\033[0m'

none_to_empty() { sed -e 's/^None$//'; }

# Walk up the labels (a.b.example.com -> b.example.com -> example.com) looking for a public zone.
zone_id() {
  local name=$domain id
  while [[ $name == *.* ]]; do
    id=$(aws route53 list-hosted-zones-by-name --dns-name "$name" --max-items 1 \
      --query "HostedZones[?Name=='${name}.' && !Config.PrivateZone].Id | [0]" \
      --output text 2>/dev/null | none_to_empty) || id=""
    if [[ -n $id ]]; then
      echo "${id#/hostedzone/}"
      return
    fi
    name=${name#*.}
  done
}

# Newest certificate for the domain with one of the given statuses.
cert_arn() {
  aws acm list-certificates --certificate-statuses "$@" \
    --query "CertificateSummaryList[?DomainName=='${domain}'].CertificateArn | [0]" \
    --output text | none_to_empty
}

cert_status() {
  aws acm describe-certificate --certificate-arn "$1" --query Certificate.Status --output text
}

# Prints "<name> <value>" of the CNAME ACM wants; it can take a few seconds to appear after a request.
validation_record() {
  local record
  for _ in $(seq 1 20); do
    record=$(aws acm describe-certificate --certificate-arn "$1" \
      --query "Certificate.DomainValidationOptions[0].ResourceRecord.[Name, Value]" \
      --output text | none_to_empty)
    if [[ -n $record && $record != *None* ]]; then
      echo "$record"
      return
    fi
    sleep 3
  done
  echo "Timed out waiting for ACM to publish the validation record" >&2
  return 1
}

upsert_cname() {
  local zone=$1 name=$2 value=$3
  aws route53 change-resource-record-sets --hosted-zone-id "$zone" --change-batch "$(cat <<JSON
{"Changes": [{"Action": "UPSERT", "ResourceRecordSet": {
  "Name": "$name", "Type": "CNAME", "TTL": 300, "ResourceRecords": [{"Value": "$value"}]}}]}
JSON
)" >/dev/null
}

print_manual_record() {
  local name=$1 value=$2
  cat <<EOF
Add this record at the DNS provider of ${domain#*.}:

  ${bold}Type:  CNAME${reset}
  ${bold}Name:  ${name%.}${reset}
  ${bold}Value: ${value%.}${reset}

(Some providers want only the part of the name before .${domain#*.}.)
Then run the matching: make aws-frontend-https / make aws-backend-https
EOF
}

case $cmd in
  request)
    arn=$(cert_arn ISSUED PENDING_VALIDATION)
    if [[ -z $arn ]]; then
      token=$(tr -cd '[:alnum:]' <<<"$domain" | cut -c1-32)
      arn=$(aws acm request-certificate --domain-name "$domain" --validation-method DNS \
        --idempotency-token "$token" --tags "Key=PROJECT_NAME,Value=$project" \
        --query CertificateArn --output text)
      echo "Requested certificate for $domain"
    else
      aws acm add-tags-to-certificate --certificate-arn "$arn" --tags "Key=PROJECT_NAME,Value=$project"
      echo "Reusing certificate for $domain"
    fi
    echo "ARN: $arn"

    status=$(cert_status "$arn")
    if [[ $status == ISSUED ]]; then
      echo "Status: ISSUED"
      exit 0
    fi

    read -r name value < <(validation_record "$arn") || true
    [[ -n ${name:-} ]] || exit 1
    zone=$(zone_id)
    if [[ -n $zone ]]; then
      upsert_cname "$zone" "$name" "$value"
      echo "Status: $status. Validation record added to Route 53 zone $zone; ACM usually validates within minutes."
    else
      echo "Status: $status. No Route 53 zone for $domain in this account."
      echo
      print_manual_record "$name" "$value"
    fi
    ;;

  status)
    arn=$(cert_arn ISSUED PENDING_VALIDATION)
    if [[ -z $arn ]]; then
      echo "No certificate for $domain in $AWS_REGION. Run the matching: make aws-frontend-cert / make aws-backend-cert"
      exit 1
    fi
    status=$(cert_status "$arn")
    echo "Domain: $domain"
    echo "ARN:    $arn"
    echo "Status: $status"
    if [[ $status != ISSUED ]]; then
      read -r name value < <(validation_record "$arn")
      echo "Validation CNAME: ${name%.} -> ${value%.}"
    fi
    ;;

  wait)
    arn=$(cert_arn ISSUED PENDING_VALIDATION)
    if [[ -z $arn ]]; then
      echo "No certificate for $domain. Run the matching: make aws-frontend-cert / make aws-backend-cert" >&2
      exit 1
    fi
    if [[ $(cert_status "$arn") != ISSUED ]]; then
      echo "Waiting for ACM to validate $domain (checks every minute; Ctrl+C is safe)..."
      aws acm wait certificate-validated --certificate-arn "$arn"
    fi
    echo "Certificate for $domain is ISSUED"
    ;;

  arn)
    cert_arn ISSUED
    ;;

  zone-id)
    zone_id
    ;;

  *)
    echo "Unknown command: $cmd" >&2
    exit 2
    ;;
esac
