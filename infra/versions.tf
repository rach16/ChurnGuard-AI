terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # State is local by default. For anything with more than one operator, move it to
  # S3 with DynamoDB locking -- uncomment and run `terraform init -migrate-state`.
  #
  # backend "s3" {
  #   bucket         = "churnguard-warehouse-586723123589"
  #   key            = "terraform/churnguard.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "churnguard-tf-lock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "churnguard"
      ManagedBy = "terraform"
      Repo      = "github.com/rach16/ChurnGuard-AI"
    }
  }
}
