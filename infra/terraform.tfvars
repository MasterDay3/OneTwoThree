# Non-secret values only. Secrets (google_client_secret) and personal data (budget_email,
# report_recipients) are variables of the HCP Terraform workspace.
project_name = "meetings"
region       = "us-east-1"

# The image the functions were created with; CI moves them to newer ones (ignored by Terraform).
backend_image_uri = "855215820653.dkr.ecr.us-east-1.amazonaws.com/meetings-backend:fb95abb2c32e805daa32f9b2fa0eb3e7420fd6cc"

app_origins = [
  "http://localhost:3000",
  "http://localhost:5173",
  "https://d33h5u6cgxnug1.cloudfront.net",
  "https://app.mykollazab.pp.ua",
]
google_client_id = "8926023606-u45dae9ahabjssv4rs44vfsl1c0bbp1u.apps.googleusercontent.com"

db_host        = "meetings-db.cluster-cw36qqi4eete.us-east-1.rds.amazonaws.com"
db_resource_id = "cluster-PDQTMNG2LMSEU2TKPTBHCFU3N4"
mail_domain    = "mykollazab.pp.ua"
